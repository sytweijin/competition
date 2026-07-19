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

    # ── Reflection 字段断言 ──
    # conftest.py 已 stub ReflectionAgent.run → 返回 overall_score=8.0
    assert result.reflection is not None, "FullPlan 应包含 reflection 字段"
    assert result.reflection.passed is True
    assert result.reflection.overall_score == 8.0
    assert isinstance(result.reflection.issues, list)
