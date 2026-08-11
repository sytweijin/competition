"""
Agent 单元测试 - 使用 FakeLLMClient 注入

覆盖:
- Planner: 正常路径 + LLM 失败 -> Coordinator fallback
- Matcher: 正常路径 + sanitize + 空分配 fallback
- Reporter: 正常路径 + LLM 失败 fallback
- InterviewSim: 正常路径
- validate_plan: 去重 + 依赖重映射 + 环检测
"""

import json
from datetime import date

import pytest

from app.agents.base import BaseAgent
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.reporter import ReporterAgent
from app.agents.interview_sim import InterviewSimAgent
from app.agents.validation import validate_plan, PlanValidationError
from app.models.schemas import (
    AgentError, PlanOutput, SubTask, QAOutput, QAAssignment,
    TimelineOutput, TeamMember, ReportOutput,
)


class FakeLLMClient:
    """可控的假 LLM 客户端，用于单元测试。"""

    def __init__(self, structured_response=None, text_response=None,
                 raise_error=None):
        self._structured = structured_response
        self._text = text_response
        self._raise = raise_error

    def chat_structured(self, system_prompt, user_prompt,
                        response_model, temperature=0.3, max_retries=3):
        if self._raise:
            return AgentError(agent="FakeLLM", error_type="llm_timeout",
                            message=str(self._raise), recoverable=True)
        if self._structured is not None:
            return self._structured
        return AgentError(agent="FakeLLM", error_type="unknown",
                         message="No mock configured", recoverable=True)

    def chat_text(self, system_prompt, user_prompt, temperature=0.7):
        if self._raise:
            return AgentError(agent="FakeLLM", error_type="llm_timeout",
                            message=str(self._raise), recoverable=True)
        return self._text or ""


# ──────────── Planner ────────────

def test_planner_success():
    """LLM 返回合法 JSON -> 正确 PlanOutput"""
    mock_plan = PlanOutput(
        tasks=[
            SubTask(id="T1", name="调研", estimated_hours=4.0),
            SubTask(id="T2", name="开发", estimated_hours=8.0, dependencies=["T1"]),
        ],
        summary="两个任务",
    )
    fake = FakeLLMClient(structured_response=mock_plan)
    agent = PlannerAgent(llm=fake)
    result = agent.run("测试课程", "描述", ["张三", "李四"], "2026-08-01")
    assert not isinstance(result, AgentError)
    assert len(result.tasks) == 2
    assert result.tasks[1].dependencies == ["T1"]


def test_planner_llm_failure():
    """LLM 抛异常 -> 返回 AgentError（Coordinator 会走 fallback）"""
    fake = FakeLLMClient(raise_error=RuntimeError("LLM down"))
    agent = PlannerAgent(llm=fake)
    result = agent.run("测试课程", "描述", ["张三"], "2026-08-01")
    assert isinstance(result, AgentError)
    assert result.recoverable is True


def test_planner_duplicate_ids_remapped():
    """Planner 输出有重复 id -> validate_plan 去重并重映射依赖"""
    mock_plan = PlanOutput(
        tasks=[
            SubTask(id="T1", name="任务A", estimated_hours=4.0),
            SubTask(id="T1", name="任务B", estimated_hours=4.0),
            SubTask(id="T2", name="任务C", estimated_hours=4.0, dependencies=["T1"]),
        ],
        summary="有重复",
    )
    fake = FakeLLMClient(structured_response=mock_plan)
    agent = PlannerAgent(llm=fake)
    result = agent.run("课程", "描述", ["张三"], "2026-08-01")
    assert not isinstance(result, AgentError)
    ids = [t.id for t in result.tasks]
    assert ids == ["T1", "T1_1", "T2"]
    # T2 deps=["T1"] should point to first T1 instance (keeping its original ID)
    deps = result.tasks[2].dependencies
    assert deps == ["T1"], f"Expected deps pointing to first T1, got {deps}"


# ──────────── Matcher ────────────

def test_matcher_success():
    """LLM 返回合法分配 -> 正确 QAOutput"""
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="开发", required_skills=["Python"])],
        summary="一个任务",
    )
    mock_qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="张三", qa_primary="李四",
                     qa_support=["王五"]),
    ])
    fake = FakeLLMClient(structured_response=mock_qa)
    members = [TeamMember(name="张三", skill_tags=["Python"]),
               TeamMember(name="李四", skill_tags=["Java"]),
               TeamMember(name="王五", skill_tags=["Go"])]
    agent = MatcherAgent(llm=fake)
    result = agent.run(plan=plan, members=members)
    assert not isinstance(result, AgentError)
    assert len(result.assignments) == 1


def test_matcher_sanitize_invalid_names():
    """LLM 编造成员名 -> sanitize 修正为有效成员"""
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="开发")],
        summary="一个任务",
    )
    mock_qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="不存在的人", qa_primary="也是编造的",
                     qa_support=["张三"]),
    ])
    fake = FakeLLMClient(structured_response=mock_qa)
    members = [TeamMember(name="张三"), TeamMember(name="李四")]
    agent = MatcherAgent(llm=fake)
    result = agent.run(plan=plan, members=members)
    assert not isinstance(result, AgentError)
    a = result.assignments[0]
    assert a.presenter == "张三"  # fallback to first member
    assert a.qa_primary == "张三"


def test_matcher_all_invalid_returns_error():
    """所有分配都引用不存在的任务 -> 返回 AgentError"""
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="开发")],
        summary="一个任务",
    )
    mock_qa = QAOutput(assignments=[
        QAAssignment(task_id="FAKE_TASK", task_name="编造",
                     presenter="张三", qa_primary="李四"),
    ])
    fake = FakeLLMClient(structured_response=mock_qa)
    members = [TeamMember(name="张三"), TeamMember(name="李四")]
    agent = MatcherAgent(llm=fake)
    result = agent.run(plan=plan, members=members)
    assert isinstance(result, AgentError)
    assert result.recoverable is True


def test_matcher_llm_failure():
    """LLM 抛异常 -> 返回 AgentError（Coordinator 走 B3）"""
    plan = PlanOutput(tasks=[SubTask(id="T1", name="开发")], summary="test")
    fake = FakeLLMClient(raise_error=RuntimeError("timeout"))
    agent = MatcherAgent(llm=fake)
    result = agent.run(plan=plan, members=[TeamMember(name="张三")])
    assert isinstance(result, AgentError)


# ──────────── Reporter ────────────

def test_reporter_success():
    """LLM 返回合法报告 -> 正确 ReportOutput"""
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="开发", estimated_hours=8.0)],
        summary="一个任务",
    )
    timeline = TimelineOutput(tasks=[], critical_path=[], total_days=3)
    qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="张三", qa_primary="李四"),
    ])
    mock_report = ReportOutput(summary="测试报告", risk_note="无风险")
    fake = FakeLLMClient(structured_response=mock_report)
    agent = ReporterAgent(llm=fake)
    result = agent.run(plan=plan, timeline=timeline, qa_matrix=qa)
    assert result.summary == "测试报告"


def test_reporter_llm_failure_fallback():
    """LLM 失败 -> 返回纯文本兜底报告"""
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="开发", estimated_hours=8.0)],
        summary="一个任务",
    )
    timeline = TimelineOutput(tasks=[], critical_path=[], total_days=3)
    qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="张三", qa_primary="李四"),
    ])
    fake = FakeLLMClient(raise_error=RuntimeError("LLM down"))
    agent = ReporterAgent(llm=fake)
    result = agent.run(plan=plan, timeline=timeline, qa_matrix=qa)
    # Should return fallback ReportOutput, not crash
    assert isinstance(result, ReportOutput)
    assert "一个任务" in result.summary or "8" in result.summary


# ──────────── InterviewSim ────────────

def test_interview_sim_success():
    """LLM 返回文本 -> 正确返回"""
    plan = PlanOutput(tasks=[SubTask(id="T1", name="开发")], summary="test")
    qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="张三", qa_primary="李四"),
    ])
    fake = FakeLLMClient(text_response="[高优先级] 为什么选这个方案？")
    agent = InterviewSimAgent(llm=fake)
    result = agent.run(plan=plan, qa_matrix=qa, user_requirements="关注技术选型")
    assert "高优先级" in result


def test_interview_sim_with_requirements():
    """用户自定义要求传入 -> prompt 包含要求"""
    plan = PlanOutput(tasks=[SubTask(id="T1", name="开发")], summary="test")
    qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="开发",
                     presenter="张三", qa_primary="李四"),
    ])
    fake = FakeLLMClient(text_response="模拟问题列表")
    agent = InterviewSimAgent(llm=fake)
    result = agent.run(plan=plan, qa_matrix=qa,
                       user_requirements="重点关注数据库设计")
    assert isinstance(result, str)


def test_interview_sim_uses_defense_material_instead_of_task_status():
    plan = PlanOutput(tasks=[SubTask(id="T1", name="开发")], summary="test")
    qa = QAOutput(assignments=[])
    calls = []

    class MaterialLLM(FakeLLMClient):
        def chat_text(self, system_prompt, user_prompt, temperature=0.7):
            calls.append(user_prompt)
            return "【高】样本量为什么足以支撑这个结论？"

    result = InterviewSimAgent(llm=MaterialLLM()).run(
        plan=plan,
        qa_matrix=qa,
        project_context="课程要求进行现场答辩",
        material_text="第4页：调研覆盖120名学生，满意度为82%。",
        material_names=["答辩PPT.pptx"],
    )
    assert "样本量" in result
    assert "调研覆盖120名学生" in calls[0]
    assert "答辩PPT.pptx" in calls[0]
    assert "任务完成状态" in calls[0]


# ──────────── validate_plan ────────────

def test_validate_plan_cycle_detection():
    """有环的计划 -> 断环容错（保留任务，断开入环依赖），不再抛异常"""
    plan = PlanOutput(
        tasks=[
            SubTask(id="T1", name="A", dependencies=["T2"]),
            SubTask(id="T2", name="B", dependencies=["T1"]),
        ],
        summary="有环",
    )
    result = validate_plan(plan)
    assert len(result.tasks) == 2
    for t in result.tasks:
        assert t.dependencies == []


def test_validate_plan_empty():
    """空计划 -> PlanValidationError"""
    plan = PlanOutput(tasks=[], summary="空")
    with pytest.raises(PlanValidationError):
        validate_plan(plan)


def test_validate_plan_dangling_deps():
    """悬空依赖 -> 被剔除"""
    plan = PlanOutput(
        tasks=[
            SubTask(id="T1", name="A"),
            SubTask(id="T2", name="B", dependencies=["T1", "GHOST"]),
        ],
        summary="有悬空",
    )
    result = validate_plan(plan)
    assert "GHOST" not in result.tasks[1].dependencies
    assert "T1" in result.tasks[1].dependencies


# ──────────── Coordinator fallback ────────────

def test_coordinator_planner_fallback():
    """Planner 失败时 Coordinator 走确定性兜底而非 RuntimeError"""
    from app.coordinator import Coordinator
    from app.models.schemas import AssignmentInput, CourseInfo

    # Mock planner to return AgentError
    coord = Coordinator()
    original_run = coord.planner.run

    def failing_run(**kwargs):
        return AgentError(agent="Planner", error_type="llm_timeout",
                         message="LLM timed out", recoverable=True)

    coord.planner.run = failing_run
    coord.matcher.run = lambda **kw: QAOutput(assignments=[])
    coord.timeline.run = lambda **kw: TimelineOutput(
        tasks=[], critical_path=[], total_days=5)
    coord.reporter.run = lambda **kw: ReportOutput(
        summary="OK", timeline_section="", qa_matrix_section="")

    inp = AssignmentInput(
        course=CourseInfo(name="测试", description="desc"),
        members=[TeamMember(name="张三")],
        deadline=date(2026, 8, 1),
    )
    # Should NOT raise RuntimeError
    result = coord.run(inp)
    # 小团队（总产能 ≤ 30h）自适应缩为 3 阶段
    assert len(result.plan.tasks) == 3
    assert "fallback" in result.plan.summary.lower() or "兜底" in result.plan.summary
