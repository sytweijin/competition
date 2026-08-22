"""成员轻量汇报页与会议旁听测试（不发起真实网络连接）。"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import MEMORY_DIR
from app.main import app
from app.models.schemas import (
    AssignmentInput,
    CourseInfo,
    FullPlan,
    PlanOutput,
    QAOutput,
    ReportOutput,
    SubTask,
    TeamMember,
    TimelineOutput,
)
from app.services.realtime_client import RealtimeChatResult

TEST_PLAN = "test_report_plan.json"


@pytest.fixture()
def saved_plan():
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="汇报测试", description=""),
            members=[
                TeamMember(
                    name="张三", role="执行成员",
                    daily_available_hours=4, unavailable_dates=[]),
                TeamMember(
                    name="李四", role="执行成员",
                    daily_available_hours=4, unavailable_dates=[]),
            ],
            deadline=date(2026, 9, 18),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="调研", estimated_hours=6,
                    assignee_id="张三", status="pending"),
                SubTask(
                    id="T2", name="文案", estimated_hours=4,
                    assignee_id="李四", status="pending"),
            ],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
    )
    path = MEMORY_DIR / TEST_PLAN
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    yield plan
    try:
        path.unlink()
    except OSError:
        pass


async def _make_token(client, member="张三"):
    resp = await client.post(
        "/api/report/link",
        json={"filename": TEST_PLAN, "member": member},
    )
    assert resp.status_code == 200
    return resp.json()["token"]


@pytest.mark.asyncio
async def test_report_link_state_and_update(saved_plan):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client)

        st = await client.get(
            "/api/report/state", params={"token": token})
        assert st.status_code == 200
        data = st.json()
        assert data["member"] == "张三"
        assert {t["id"] for t in data["tasks"]} == {"T1"}

        up = await client.post("/api/report/update", json={
            "token": token,
            "task_id": "T1",
            "status": "completed",
            "actual_hours": 6.0,
            "note": "已交付",
        })
        assert up.status_code == 200
        assert up.json()["status"] == "completed"

        # 更新已持久化，重新读状态可见
        st2 = await client.get(
            "/api/report/state", params={"token": token})
        task = st2.json()["tasks"][0]
        assert task["status"] == "completed"
        assert task["actual_hours"] == 6.0
        assert "已交付" in task["notes"]


@pytest.mark.asyncio
async def test_report_voice_parses_action(saved_plan, monkeypatch):
    import app.services.media_analysis as media
    import app.services.realtime_client as rt

    def fake_decode(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text="完成|6|数据已归档")

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client)
        resp = await client.post(
            "/api/report/voice",
            data={"token": token, "task_id": "T1"},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )
    assert resp.status_code == 200
    assert resp.json()["parsed"] == {
        "status": "完成", "actual_hours": 6.0, "note": "数据已归档"}


@pytest.mark.asyncio
async def test_report_photo_marks_completed(saved_plan):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client)
        resp = await client.post(
            "/api/report/photo",
            data={"token": token, "task_id": "T1"},
            files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["photo"]


@pytest.mark.asyncio
async def test_report_rejects_non_member_and_bad_token(saved_plan):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client, member="李四")
        resp = await client.post("/api/report/update", json={
            "token": token,
            "task_id": "T1",
            "status": "completed",
        })
        assert resp.status_code == 403

        bad = await client.get(
            "/api/report/state", params={"token": "not-a-token"})
        assert bad.status_code == 404


@pytest.mark.asyncio
async def test_meeting_parses_tasks(monkeypatch):
    import app.services.media_analysis as media
    import app.services.realtime_client as rt

    def fake_decode(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text=(
            "【总结】\n确定调研分工与截止时间\n"
            "【任务】\n- 完成问卷 | 张三 | 下周一\n- 整理数据 | 李四 | 无\n"
            "【风险】\n无"))

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/realtime/meeting",
            files={"file": ("meeting.webm", b"fake-audio", "audio/webm")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "确定调研分工与截止时间"
    assert data["tasks"][0]["name"] == "完成问卷"
    assert data["tasks"][0]["owner"] == "张三"
    assert data["tasks"][0]["deadline"] == "下周一"
    assert len(data["tasks"]) == 2
