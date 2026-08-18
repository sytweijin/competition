"""Coordinator 集成测试（mock LLM 调用）"""
from datetime import date

import pytest

from app.coordinator import Coordinator
from app.models.schemas import AssignmentInput, CourseInfo, TeamMember


def test_coordinator_with_mock(monkeypatch):
    """验证 Coordinator 主链路能正常跑通（mock LLM 层）"""
    from app.agents.planner import PlannerAgent
    from app.models.schemas import PlanOutput, SubTask

    # Mock Planner
    def mock_planner_run(self, **kwargs):
        return PlanOutput(
            tasks=[
                SubTask(id="T1", name="需求分析", description="", estimated_hours=4.0),
                SubTask(id="T2", name="系统设计", description="", estimated_hours=6.0, dependencies=["T1"]),
            ],
            summary="两个任务",
        )
    monkeypatch.setattr(PlannerAgent, "run", mock_planner_run)

    # Mock Matcher / Timeline / Reporter 也返回有效对象
    from app.agents.matcher import MatcherAgent
    from app.agents.timeline import TimelineAgent
    from app.agents.reporter import ReporterAgent
    from app.models.schemas import QAOutput, TimelineOutput, ReportOutput

    monkeypatch.setattr(MatcherAgent, "run", lambda self, **kw: QAOutput(assignments=[]))
    monkeypatch.setattr(TimelineAgent, "run", lambda self, **kw: TimelineOutput(
        tasks=[], critical_path=[], total_days=5))
    monkeypatch.setattr(ReporterAgent, "run", lambda self, **kw: ReportOutput(
        summary="OK", timeline_section="", qa_matrix_section=""))

    coord = Coordinator()
    inp = AssignmentInput(
        course=CourseInfo(name="软件工程", description="小组项目"),
        members=[TeamMember(name="张三", skill_tags=["前端"]),
                 TeamMember(name="李四", skill_tags=["后端"])],
        deadline=date(2026, 7, 15),
    )
    result = coord.run(inp)
    assert result.plan.summary == "两个任务"
    assert len(result.plan.tasks) == 2
    assert result.timeline.total_days == 5

    # 核心请求不等待 Reporter / Reflection。
    assert result.reflection is None
    assert result.report.summary == ""
    assert result.performance["task_count"] == 2
    assert result.performance["member_count"] == 2
    assert result.performance["cpm_ms"] >= 0


def test_reporter_and_reflection_do_not_block_core_result(monkeypatch):
    """核心响应不应调用两个非核心 LLM 阶段。"""
    import time
    from app.agents.planner import PlannerAgent
    from app.agents.matcher import MatcherAgent
    from app.agents.timeline import TimelineAgent
    from app.agents.reporter import ReporterAgent
    from app.agents.reflection import ReflectionAgent
    from app.models.schemas import (
        PlanOutput, SubTask, QAOutput, QAAssignment, TimelineOutput,
        ReportOutput, ReflectionOutput,
    )

    plan = PlanOutput(tasks=[SubTask(
        id="T1", name="高风险任务", estimated_hours=20)], summary="测试")
    qa = QAOutput(assignments=[QAAssignment(
        task_id="T1", task_name="高风险任务", presenter="张三",
        qa_primary="张三", score=0.1)], workload={"张三": 20})
    monkeypatch.setattr(PlannerAgent, "run", lambda self, **kw: plan)
    monkeypatch.setattr(MatcherAgent, "run", lambda self, **kw: qa)
    monkeypatch.setattr(TimelineAgent, "run", lambda self, **kw: TimelineOutput(
        tasks=[], critical_path=[], total_days=1))

    def slow_report(self, **kw):
        time.sleep(0.05)
        return ReportOutput(summary="OK")

    def slow_reflect(self, **kw):
        time.sleep(0.05)
        return ReflectionOutput(overall_score=8, passed=True)

    monkeypatch.setattr(ReporterAgent, "run", slow_report)
    monkeypatch.setattr(ReflectionAgent, "run", slow_reflect)
    inp = AssignmentInput(
        course=CourseInfo(name="并行测试", description=""),
        members=[TeamMember(name="张三", available_hours=4)],
        deadline=date(2026, 8, 20),
    )
    started = time.perf_counter()
    result = Coordinator().run(inp)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.04
    assert result.performance["reflection_executed"] is False
    assert result.performance["reporter_blocks_response"] is False
