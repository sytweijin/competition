"""Knowledge Agent and cross-project experience tests."""

from datetime import date, timedelta

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)
from app.services.collab import knowledge_search, save_experience
from app.services.knowledge_agent import ask


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试项目", description="做调研"),
            members=[TeamMember(name="小文", role="执行成员")],
            deadline=date.today() + timedelta(days=10),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="调研任务", estimated_hours=4,
                    assignee_id="小文", actual_hours=7,
                ),
            ],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_agent_asks_workload_tool():
    result = ask("工作量怎么样？", _plan())
    assert "workload" in result["trace"]
    assert "小文" in result["answer"]


def test_agent_falls_back_to_plan_summary():
    result = ask("帮我看看这个项目", _plan())
    assert result["answer"]
    assert "测试项目" in result["answer"]


def test_experience_is_saved_and_searchable(tmp_path, monkeypatch):
    import app.services.collab as collab

    monkeypatch.setattr(collab, "EXPERIENCE_FILE", tmp_path / "experience.jsonl")
    monkeypatch.setattr(collab, "MEMORY_DIR", tmp_path / "memory")
    count = save_experience(_plan())
    assert count > 0
    second = save_experience(_plan())
    assert second == 0
    assert len((tmp_path / "experience.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    result = knowledge_search("任务实际工时高于计划", plan=None)
    assert any("经验" in item["name"] for item in result["sources"])
