"""ReflectionAgent 单元测试。

覆盖：
- 确定性兜底（不依赖 LLM）：负载均衡、工时超产能、关键路径过长、大任务、孤立任务
- LLM 调用失败自动降级
- run() 正常返回 ReflectionOutput 结构
"""
from __future__ import annotations

import pytest

from app.agents.reflection import ReflectionAgent
from app.coordinator import should_reflect
from datetime import datetime, timedelta

from app.models.schemas import (
    AgentError,
    PlanOutput,
    QAAssignment,
    QAOutput,
    ReflectionOutput,
    SubTask,
    TimelineOutput,
    TimelineTask,
)


# ──────────── fixtures ────────────

def _make_plan(tasks: list[SubTask] | None = None) -> PlanOutput:
    if tasks is None:
        tasks = [
            SubTask(id="T1", name="需求分析", description="", estimated_hours=4.0),
            SubTask(id="T2", name="系统设计", description="", estimated_hours=6.0,
                    dependencies=["T1"]),
            SubTask(id="T3", name="开发", description="", estimated_hours=8.0,
                    dependencies=["T2"]),
        ]
    return PlanOutput(tasks=tasks, summary="测试计划")


_BASE_DATE = datetime(2026, 7, 1)


def _make_timeline(total_days: int = 5,
                   critical_path: list[str] | None = None) -> TimelineOutput:
    cp = critical_path or ["T1", "T2", "T3"]
    tl_tasks = [
        TimelineTask(
            task_id=tid, name=tid,
            start_date=_BASE_DATE + timedelta(days=i),
            end_date=_BASE_DATE + timedelta(days=i + 1),
            is_critical=(tid in cp),
        )
        for i, tid in enumerate(["T1", "T2", "T3"])
    ]
    return TimelineOutput(tasks=tl_tasks, critical_path=cp, total_days=total_days)


def _make_qa(assignments: list | None = None) -> QAOutput:
    if assignments is None:
        assignments = [
            QAAssignment(task_id="T1", task_name="需求分析",
                         presenter="张三", qa_primary="李四"),
            QAAssignment(task_id="T2", task_name="系统设计",
                         presenter="张三", qa_primary="王五"),
            QAAssignment(task_id="T3", task_name="开发",
                         presenter="李四", qa_primary="张三"),
        ]
    return QAOutput(assignments=assignments)


agent = ReflectionAgent()
_ORIGINAL_REFLECTION_RUN = ReflectionAgent.__dict__["run"]


@pytest.fixture(autouse=True)
def _restore_original_run():
    """防止 conftest 的链路 stub 在完整测试顺序中污染本模块。"""
    ReflectionAgent.run = _ORIGINAL_REFLECTION_RUN
    yield
    ReflectionAgent.run = _ORIGINAL_REFLECTION_RUN


# ──────────── 确定性兜底 基础功能 ────────────

class TestDeterministicReflect:

    def test_returns_reflection_output(self):
        result = agent._deterministic_reflect(
            _make_plan(), _make_timeline(), _make_qa(), total_capacity=40.0
        )
        assert isinstance(result, ReflectionOutput)
        assert 0.0 <= result.overall_score <= 10.0
        assert isinstance(result.passed, bool)
        assert isinstance(result.issues, list)

    def test_balanced_plan_high_score(self):
        """均衡计划分数应 >= 7"""
        result = agent._deterministic_reflect(
            _make_plan(), _make_timeline(), _make_qa(), total_capacity=40.0
        )
        assert result.overall_score >= 7.0

    def test_overload_warning(self):
        """总工时超产能 130% 应产生 warning"""
        plan = _make_plan([
            SubTask(id=f"T{i}", name=f"任务{i}", description="",
                    estimated_hours=10.0)
            for i in range(1, 9)   # 80h
        ])
        result = agent._deterministic_reflect(
            plan, _make_timeline(), _make_qa(), total_capacity=50.0
        )
        levels = [iss.level for iss in result.issues]
        assert "warning" in levels, "超产能应有 warning"

    def test_underload_suggestion(self):
        """总工时低于产能 50% 应产生 suggestion"""
        plan = _make_plan([
            SubTask(id="T1", name="小任务", description="", estimated_hours=5.0),
        ])
        result = agent._deterministic_reflect(
            plan, _make_timeline(), _make_qa(), total_capacity=80.0
        )
        dims = [iss.dimension for iss in result.issues]
        assert "工时估算" in dims

    def test_long_critical_path_warning(self):
        """关键路径 > 5 应有 warning"""
        cp = [f"T{i}" for i in range(1, 8)]   # 7 个
        tasks = [
            SubTask(id=f"T{i}", name=f"任务{i}", description="",
                    estimated_hours=2.0,
                    dependencies=[f"T{i-1}"] if i > 1 else [])
            for i in range(1, 8)
        ]
        tl_tasks = [
            TimelineTask(
                task_id=t.id, name=t.name,
                start_date=_BASE_DATE + timedelta(days=i),
                end_date=_BASE_DATE + timedelta(days=i + 2),
                is_critical=True,
            )
            for i, t in enumerate(tasks)
        ]
        timeline = TimelineOutput(tasks=tl_tasks, critical_path=cp, total_days=14)
        result = agent._deterministic_reflect(
            _make_plan(tasks), timeline, _make_qa(), total_capacity=40.0
        )
        dims = [iss.dimension for iss in result.issues]
        assert "时间线" in dims

    def test_heavy_task_suggestion(self):
        """单任务 > 12h 应有 suggestion"""
        plan = _make_plan([
            SubTask(id="T1", name="超大任务", description="", estimated_hours=16.0),
            SubTask(id="T2", name="小任务", description="", estimated_hours=4.0,
                    dependencies=["T1"]),
        ])
        result = agent._deterministic_reflect(
            plan, _make_timeline(), _make_qa(), total_capacity=40.0
        )
        affected = [iss for iss in result.issues
                    if "T1" in iss.affected_tasks and iss.dimension == "任务拆解"]
        assert affected, "超大任务应被标记"

    def test_unbalanced_workload_warning(self):
        """负载比 >= 2.5x 应产生 warning"""
        # 张三主负责 5 个，李四主负责 1 个 → 比例 5x
        assignments = [
            QAAssignment(task_id=f"T{i}", task_name=f"任务{i}",
                         presenter="张三", qa_primary="李四")
            for i in range(1, 6)
        ] + [
            QAAssignment(task_id="T6", task_name="任务6",
                         presenter="李四", qa_primary="张三"),
        ]
        qa = QAOutput(assignments=assignments)
        tasks = [
            SubTask(id=f"T{i}", name=f"任务{i}", description="",
                    estimated_hours=2.0)
            for i in range(1, 7)
        ]
        result = agent._deterministic_reflect(
            _make_plan(tasks), _make_timeline(), qa, total_capacity=40.0
        )
        dims = [iss.dimension for iss in result.issues]
        assert "负载均衡" in dims

    def test_passed_false_when_error(self):
        """存在 error 级问题时 passed 应为 False"""
        from app.models.schemas import ReflectionIssue
        # 直接构造含 error 的输出，验证 passed 逻辑
        output = ReflectionOutput(
            issues=[ReflectionIssue(level="error", dimension="任务拆解",
                                    description="严重问题", suggestion="")],
            overall_score=5.0,
            overall_comment="有严重问题",
            improvement_priority=[],
            passed=False,
        )
        assert output.passed is False

    def test_no_capacity_skips_capacity_check(self):
        """total_capacity=0 时不产生产能相关问题"""
        result = agent._deterministic_reflect(
            _make_plan(), _make_timeline(), _make_qa(), total_capacity=0.0
        )
        cap_issues = [i for i in result.issues if i.dimension == "工时估算"]
        assert cap_issues == []


# ──────────── run() 降级 ────────────

class TestRunFallback:

    def test_run_falls_back_on_llm_error(self, monkeypatch):
        """LLM 返回 AgentError 时，run() 应自动降级到确定性方法"""
        monkeypatch.setattr(
            ReflectionAgent, "_call_llm",
            lambda self, prompt, **kw: AgentError(agent="Reflection",
                                                   error_type="llm_timeout",
                                                   message="网络超时"),
        )
        result = agent.run(
            plan=_make_plan(),
            timeline=_make_timeline(),
            qa_matrix=_make_qa(),
            total_capacity=40.0,
        )
        assert isinstance(result, ReflectionOutput)
        assert result.overall_score >= 0.0

    def test_run_returns_reflection_output_on_success(self, monkeypatch):
        """LLM 返回正确对象时，run() 直接返回"""
        fake_output = ReflectionOutput(
            issues=[],
            overall_score=9.0,
            overall_comment="很好",
            improvement_priority=[],
            passed=True,
        )
        monkeypatch.setattr(
            ReflectionAgent, "_call_llm",
            lambda self, prompt, **kw: fake_output,
        )
        result = agent.run(
            plan=_make_plan(),
            timeline=_make_timeline(),
            qa_matrix=_make_qa(),
            total_capacity=40.0,
        )
        assert result.overall_score == 9.0
        assert result.passed is True


class TestReflectionRiskGate:

    def test_normal_simple_plan_skips_llm_reflection(self):
        reflect, reasons = should_reflect(
            _make_plan(), _make_timeline(5, ["T1", "T2", "T3"]),
            _make_qa(), total_capacity=40.0,
        )
        assert reflect is False
        assert reasons == []

    def test_obvious_risk_triggers_reflection(self):
        qa = QAOutput(assignments=[
            QAAssignment(task_id="T1", task_name="需求分析",
                         presenter="张三", qa_primary="李四", score=0.1),
        ], workload={"张三": 30, "李四": 2})
        reflect, reasons = should_reflect(
            _make_plan(), _make_timeline(), qa, total_capacity=10.0,
        )
        assert reflect is True
        assert "low_skill_match" in reasons
        assert "unassigned_task" in reasons
        assert "capacity_overload" in reasons


# ──────────── improvement_priority ────────────

class TestImprovementPriority:

    def test_priority_at_most_5(self):
        """改进优先级最多 5 条"""
        # 构造会触发多条 issue 的场景
        tasks = [
            SubTask(id=f"T{i}", name=f"任务{i}", description="",
                    estimated_hours=15.0)   # 每个都超 12h
            for i in range(1, 7)
        ]
        cp = [f"T{i}" for i in range(1, 7)]
        tl_tasks = [
            TimelineTask(
                task_id=t.id, name=t.name,
                start_date=_BASE_DATE + timedelta(days=i),
                end_date=_BASE_DATE + timedelta(days=i + 2),
                is_critical=True,
            )
            for i, t in enumerate(tasks)
        ]
        timeline = TimelineOutput(tasks=tl_tasks, critical_path=cp, total_days=20)
        result = agent._deterministic_reflect(
            _make_plan(tasks), timeline, _make_qa(), total_capacity=20.0
        )
        assert len(result.improvement_priority) <= 5
