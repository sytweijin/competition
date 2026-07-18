from datetime import date

from fastapi.testclient import TestClient

from app.coordinator import Coordinator
from app.main import app
from app.models.schemas import AssignmentInput, CourseInfo, TeamMember


def _input():
    return AssignmentInput(
        course=CourseInfo(name="社会实践推送", description="制作暑期社会实践总结秀米推送"),
        background="实践前定框架，实践中摄影记录，实践后完成文案、选图、排版、审核和发布。",
        requirements="需要秀米推送",
        members=[
            TeamMember(name="文案同学", skill_tags=["文案撰写"]),
            TeamMember(name="摄影同学", skill_tags=["摄影"]),
            TeamMember(name="排版同学", skill_tags=["秀米排版"]),
        ],
        deadline=date(2026, 8, 20),
        default_start_date=date(2026, 7, 20),
        default_end_date=date(2026, 8, 20),
    )


def test_draft_does_not_assign_and_has_professional_tasks(monkeypatch):
    coordinator = Coordinator()
    monkeypatch.setattr(coordinator, "_step_planner", lambda inp: coordinator._fallback_plan(inp))
    draft = coordinator.draft(_input())
    assert len(draft.tasks) == 10
    assert all(t.assignee_id is None for t in draft.tasks)
    assert {"文案", "摄影", "排版"}.issubset({t.category for t in draft.tasks})
    assert all(t.estimated_hours > 0 and t.execution_stage for t in draft.tasks)


def test_file_analysis_txt():
    client = TestClient(app)
    response = client.post(
        "/api/analyze-files",
        data={"background": "社会实践总结"},
        files={"files": ("requirements.txt", "目标：发布总结推送。截止：8月20日。".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["files"][0]["status"] == "ok"
