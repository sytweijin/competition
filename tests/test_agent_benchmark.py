"""Planner / Matcher benchmark 与故障注入测试；不访问真实网络。"""

from types import SimpleNamespace

from app.agents.matcher import MatcherAgent
from app.agents.planner import PlannerAgent
from app.llm.client import LLMClient
from app.models.schemas import AgentError, PlanOutput, QAOutput, QAAssignment, SubTask
from app.performance import PerformanceTrace, llm_event_sink, request_trace, stage
from scripts.benchmark_agents import aggregate


def response(*, parsed=None, content=None, finish_reason="stop"):
    message = SimpleNamespace(parsed=parsed, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(
        message=message, finish_reason=finish_reason)])


def fake_client(parse_fn, create_fn=None):
    client = LLMClient()
    client._client = SimpleNamespace(
        beta=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(parse=parse_fn))),
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=create_fn or (lambda **kwargs: response(content="{}")))),
    )
    client._prefer_plain = False
    original_chat_structured = client.chat_structured

    def isolated_chat_structured(*args, max_retries=2, **kwargs):
        """故障注入固定两次尝试，不继承开发机 .env 的生产重试配置。"""
        return original_chat_structured(
            *args, max_retries=max_retries, **kwargs)

    client.chat_structured = isolated_chat_structured
    return client


def valid_plan():
    return PlanOutput(
        tasks=[SubTask(id="T1", name="开发", estimated_hours=4)], summary="计划")


def valid_qa():
    return QAOutput(assignments=[QAAssignment(
        task_id="T1", task_name="开发", presenter="M1", qa_primary="M1")])


def test_fault_timeout_is_bounded_and_emits_structured_events():
    calls = {"count": 0}
    events = []

    def timeout(**kwargs):
        calls["count"] += 1
        raise TimeoutError("injected")

    trace = PerformanceTrace(task_count=1, member_count=1)
    client = fake_client(timeout)
    with request_trace(trace), llm_event_sink("req-timeout", events.append), stage("Planner"):
        result = client.chat_structured("system", "user", PlanOutput, max_retries=99)

    assert isinstance(result, AgentError)
    assert result.error_type == "timeout"
    assert calls["count"] == 2, "即使请求 99 次重试，实际物理尝试也必须有上限"
    assert [item["attempt"] for item in events] == [1, 2]
    assert all(item["status"] == "timeout" for item in events)
    assert all(item["request_id"] == "req-timeout" for item in events)


def test_fault_first_timeout_second_success_tracks_retry_amplification():
    calls = {"count": 0}

    def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("first attempt")
        return response(parsed=valid_plan())

    trace = PerformanceTrace(task_count=1, member_count=1)
    with request_trace(trace), stage("Planner"):
        result = PlannerAgent(llm=fake_client(flaky)).run(
            course_name="测试", course_description="测试", members=["M1"],
            deadline="2026-12-31")
    metrics = trace.finish()

    assert isinstance(result, PlanOutput)
    assert calls["count"] == 2
    assert metrics["llm_retries"]["Planner"] == 1
    assert metrics["llm_timeouts"]["Planner"] == 1
    assert metrics["llm_retry_extra_ms"]["Planner"] >= 0


def test_fault_parse_failure_enters_plain_fallback_and_returns_valid_plan():
    calls = {"parse": 0, "plain": 0}

    def malformed(**kwargs):
        calls["parse"] += 1
        return response(content="not json")

    def valid_plain(**kwargs):
        calls["plain"] += 1
        return response(content=valid_plan().model_dump_json())

    trace = PerformanceTrace(task_count=1, member_count=1)
    with request_trace(trace), stage("Planner"):
        result = PlannerAgent(llm=fake_client(malformed, valid_plain)).run(
            course_name="测试", course_description="测试", members=["M1"],
            deadline="2026-12-31")
    metrics = trace.finish()

    assert isinstance(result, PlanOutput)
    assert calls == {"parse": 1, "plain": 1}
    assert metrics["llm_plain_fallbacks"]["Planner"] == 1


def test_fault_matcher_first_failure_second_success_still_returns_valid_result():
    calls = {"count": 0}

    def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("first attempt")
        return response(parsed=valid_qa())

    plan = valid_plan()
    from app.models.schemas import TeamMember
    result = MatcherAgent(llm=fake_client(flaky)).run(
        plan=plan, members=[TeamMember(name="M1")])
    assert isinstance(result, QAOutput)
    assert result.assignments[0].presenter == "M1"
    assert calls["count"] == 2


def test_fault_plain_fallback_failure_terminates_without_loop():
    calls = {"parse": 0, "plain": 0}

    def malformed(**kwargs):
        calls["parse"] += 1
        return response(content="not json")

    def malformed_plain(**kwargs):
        calls["plain"] += 1
        return response(content="still not json")

    result = fake_client(malformed, malformed_plain).chat_structured(
        "system", "user", QAOutput, max_retries=99)
    assert isinstance(result, AgentError)
    assert calls == {"parse": 1, "plain": 1}


def test_preferred_plain_timeout_is_counted():
    client = fake_client(lambda **kwargs: response())
    client._prefer_plain = True
    client._client.chat.completions.create = lambda **kwargs: (_ for _ in ()).throw(
        TimeoutError("plain timeout"))
    trace = PerformanceTrace(task_count=1, member_count=1)
    with request_trace(trace), stage("Matcher"):
        result = client.chat_structured("system", "user", QAOutput)
    metrics = trace.finish()
    assert isinstance(result, AgentError)
    assert metrics["llm_timeouts"]["Matcher"] == 1


def test_deepseek_v4_plain_request_explicitly_disables_thinking():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return response(content=valid_plan().model_dump_json())

    client = fake_client(lambda **kwargs: response(), create)
    client.model = "deepseek-v4-flash"
    client._prefer_plain = True
    result = client.chat_structured("system", "user", PlanOutput)
    assert isinstance(result, PlanOutput)
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_non_deepseek_model_does_not_receive_vendor_thinking_option():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return response(content=valid_plan().model_dump_json())

    client = fake_client(lambda **kwargs: response(), create)
    client.model = "qwen-compatible-model"
    client._prefer_plain = True
    result = client.chat_structured("system", "user", PlanOutput)
    assert isinstance(result, PlanOutput)
    assert "extra_body" not in captured


def test_benchmark_summary_calculates_tail_and_retry_metrics():
    rows = [
        {"agent": "planner", "latency_ms": 1000, "llm_calls": 1,
         "first_success": True, "success": True, "timeouts": 0,
         "retries": 0, "fallbacks": 0, "retry_extra_ms": 0},
        {"agent": "planner", "latency_ms": 3000, "llm_calls": 2,
         "first_success": False, "success": True, "timeouts": 1,
         "retries": 1, "fallbacks": 0, "retry_extra_ms": 1200},
        {"agent": "matcher", "latency_ms": 2000, "llm_calls": 1,
         "first_success": True, "success": True, "timeouts": 0,
         "retries": 0, "fallbacks": 0, "retry_extra_ms": 0},
        {"agent": "matcher", "latency_ms": 6000, "llm_calls": 2,
         "first_success": False, "success": False, "timeouts": 1,
         "retries": 1, "fallbacks": 1, "retry_extra_ms": 2500},
    ]
    summary = aggregate(rows)
    assert summary["planner"]["p50_ms"] == 2000
    assert summary["planner"]["first_success_rate"] == 0.5
    assert summary["matcher"]["p95_ms"] > summary["planner"]["p95_ms"]
    assert summary["matcher"]["retry_extra_avg_ms"] == 1250
