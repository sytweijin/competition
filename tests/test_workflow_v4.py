from datetime import date

from fastapi.testclient import TestClient

from app.coordinator import Coordinator
from app.main import app
from app.models.schemas import AgentError, AssignmentInput, CourseInfo, TeamMember
from app.services.project_service import generate_draft


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
    assert all(t.suggested_people >= 1 for t in draft.tasks)


def test_domain_fallback_uses_project_keywords():
    inp = AssignmentInput(
        course=CourseInfo(name="调研汇报", description="开展社会调研并完成报告和答辩PPT"),
        background="实践中访谈和收集资料，实践后分析数据、撰写总结并汇报。",
        members=[TeamMember(name="甲", skill_tags=["调研"])],
        deadline=date(2026, 8, 20),
    )
    plan = Coordinator._fallback_plan(inp, "timeout")
    names = {task.name for task in plan.tasks}
    assert "开展调研与资料采集" in names
    assert "撰写报告或总结正文" in names
    assert "制作演示文稿与视觉排版" in names
    assert len(plan.tasks) >= 7


def test_fast_draft_does_not_call_llm(monkeypatch):
    coordinator_called = {"value": False}

    def fail_if_called(*args, **kwargs):
        coordinator_called["value"] = True
        raise AssertionError("快速草案不应调用 LLM")

    monkeypatch.setattr(Coordinator, "draft", fail_if_called)
    plan = generate_draft(_input(), use_ai=False)
    assert not coordinator_called["value"]
    assert len(plan.tasks) == 10


def test_planner_receives_confirmed_and_file_requirements(monkeypatch):
    coordinator = Coordinator()
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return coordinator._fallback_plan(_input())

    monkeypatch.setattr(coordinator.planner, "run", fake_run)
    inp = _input().model_copy(update={
        "requirements": "用户确认：必须发布秀米推送",
        "requirement_analysis": {"summary": "文件要求：实践后提交"},
    })
    coordinator.draft(inp)
    assert "必须发布秀米推送" in captured["extra"]
    assert "实践后提交" in captured["extra"]


def test_file_analysis_txt():
    client = TestClient(app)
    response = client.post(
        "/api/analyze-files",
        data={"background": "社会实践总结"},
        files={"files": ("requirements.txt", "目标：发布总结推送。截止：8月20日。".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["files"][0]["status"] == "ok"
    assert response.json()["analysis"]["time_requirements"]
    assert "发布总结推送" in response.json()["analysis"]["summary"]


def test_chat_can_read_draft_without_full_plan(monkeypatch):
    from app.llm.client import LLMClient
    monkeypatch.setattr(
        LLMClient, "chat_text",
        lambda *args, **kwargs: AgentError(
            agent="test", error_type="timeout", message="offline"))
    client = TestClient(app)
    response = client.post("/api/chat", json={
        "message": "当前有哪些任务？",
        "draft": {
            "tasks": [{
                "id": "T1", "name": "文案撰写", "estimated_hours": 5,
                "suggested_people": 1, "dependencies": [],
            }],
            "summary": "草案",
        },
    })
    assert response.status_code == 200
    assert "文案撰写" in response.json()["reply"]
