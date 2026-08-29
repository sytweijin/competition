"""成员轻量汇报页与会议旁听测试（不发起真实网络连接）。"""

import json
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
    ProjectModule,
    QAOutput,
    ReportOutput,
    SubTask,
    TeamMember,
    TimelineOutput,
    Volunteer,
)
from app.services.realtime_client import RealtimeChatResult

TEST_PLAN = "test_report_plan.json"


@pytest.fixture()
def saved_plan():
    try:
        from app.services.report_link import NOTES_FILE
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        data = {
            k: v for k, v in data.items()
            if not k.startswith(TEST_PLAN + "::")
        }
        NOTES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass
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
                    assignee_id="张三", collaborator_ids=["李四"],
                    status="pending"),
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
    try:
        from app.services.report_link import NOTES_FILE
        data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        data = {
            k: v for k, v in data.items()
            if not k.startswith(TEST_PLAN + "::")
        }
        NOTES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


async def _make_token(client, member="张三"):
    resp = await client.post(
        "/api/report/link",
        json={"filename": TEST_PLAN, "member": member},
    )
    assert resp.status_code == 200
    return resp.json()["token"]


def _load_saved_plan() -> FullPlan:
    path = MEMORY_DIR / TEST_PLAN
    return FullPlan.model_validate(
        json.loads(path.read_text(encoding="utf-8")))


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
        assert task["notes"].count("已交付") == 1  # 备注不重复


@pytest.mark.asyncio
async def test_report_update_does_not_create_version(saved_plan):
    """成员汇报只落盘方案文件，不滚动版本树（版本仅保存方案时生成）。"""
    from app.services.audit_store import AUDIT_DIR, VERSION_DIR, list_versions
    import shutil

    # 先清空该方案的历史版本树，保证断言确定，且不依赖用例执行顺序
    shutil.rmtree(VERSION_DIR / TEST_PLAN, ignore_errors=True)
    try:
        (AUDIT_DIR / f"{TEST_PLAN}.jsonl").unlink()
    except OSError:
        pass

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            token = await _make_token(client)

            # 实质更新：方案文件更新，但版本树不新增
            up = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "in_progress",
                "actual_hours": 2.0,
                "note": "开始做",
            })
            assert up.status_code == 200
            assert list_versions(TEST_PLAN) == []

            # 重复提交同一状态：仍不生成版本
            noop = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "in_progress",
                "note": "重复确认",
            })
            assert noop.status_code == 200
            assert list_versions(TEST_PLAN) == []

            # 再次实质更新：方案文件反映最新状态，版本树仍为空
            up2 = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "completed",
                "actual_hours": 4.0,
                "note": "完成了",
            })
            assert up2.status_code == 200
            assert list_versions(TEST_PLAN) == []
            plan = FullPlan.model_validate(
                json.loads((MEMORY_DIR / TEST_PLAN).read_text(encoding="utf-8")))
            assert plan.plan.tasks[0].status == "completed"
            assert plan.plan.tasks[0].actual_hours == 4.0
    finally:
        # 无论断言成败都清理本测试产生的版本快照与审计记录
        shutil.rmtree(VERSION_DIR / TEST_PLAN, ignore_errors=True)
        try:
            (AUDIT_DIR / f"{TEST_PLAN}.jsonl").unlink()
        except OSError:
            pass


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
        "status": "completed",
        "status_label": "完成",
        "actual_hours": 6.0,
        "note": "数据已归档",
    }


@pytest.mark.asyncio
async def test_report_voice_apply_persists_status(saved_plan, monkeypatch):
    """语音解析返回英文枚举：确认应用后状态真实写入并持久化。"""
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
        parsed = (await client.post(
            "/api/report/voice",
            data={"token": token, "task_id": "T1"},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )).json()["parsed"]
        assert parsed["status"] == "completed"

        up = await client.post("/api/report/update", json={
            "token": token,
            "task_id": "T1",
            "status": parsed["status"],
            "actual_hours": parsed["actual_hours"],
            "note": parsed["note"],
        })
        assert up.status_code == 200
        assert up.json()["status"] == "completed"

        st = (await client.get(
            "/api/report/state", params={"token": token})).json()
        task = next(t for t in st["tasks"] if t["id"] == "T1")
        assert task["status"] == "completed"
        assert task["actual_hours"] == 6.0


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
    assert resp.json()["confirmed"] is True
    photo_name = resp.json()["photo"]
    try:
        (MEMORY_DIR / "attachments" / photo_name).unlink()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_report_photo_keeps_multiple_photos(saved_plan):
    """同一成员同一任务可上传多张交付物照片，互不覆盖且都可查看。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client)
        names = []
        for index in range(2):
            resp = await client.post(
                "/api/report/photo",
                data={"token": token, "task_id": "T1"},
                files={"file": (
                    f"photo{index}.png",
                    b"\x89PNG\r\n\x1a\n",
                    "image/png",
                )},
            )
            assert resp.status_code == 200
            names.append(resp.json()["photo"])

        # 两次上传应生成两个不同文件
        assert len(set(names)) == 2

        st = (await client.get(
            "/api/report/state", params={"token": token})).json()
        task = next(t for t in st["tasks"] if t["id"] == "T1")
        owner_row = next(
            m for m in task["members"] if m["name"] == "张三")
        assert owner_row["photos"] == names

        # 每张照片都能通过 photo 参数单独访问
        for name in names:
            resp = await client.get(
                "/api/report/attachment",
                params={"token": token, "task_id": "T1", "photo": name},
            )
            assert resp.status_code == 200

    for name in names:
        try:
            (MEMORY_DIR / "attachments" / name).unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_report_rejects_non_member_and_bad_token(saved_plan):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # 张三不是 T2 的成员（T2 负责人为李四且无协作者）
        token = await _make_token(client, member="张三")
        resp = await client.post("/api/report/update", json={
            "token": token,
            "task_id": "T2",
            "status": "completed",
        })
        assert resp.status_code == 403

        bad = await client.get(
            "/api/report/state", params={"token": "not-a-token"})
        assert bad.status_code == 404


@pytest.mark.asyncio
async def test_report_collaborator_completion_awaits_owner_confirm(saved_plan):
    """协作者可报"我的部分完成"，任务级状态仍由负责人确认并可见。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # T1 负责人是张三，李四为协作者
        token = await _make_token(client, member="李四")
        resp = await client.post("/api/report/update", json={
            "token": token,
            "task_id": "T1",
            "status": "completed",
            "actual_hours": 3.0,
            "note": "我负责的部分已完成",
        })
        assert resp.status_code == 200
        # 任务级状态仍是进行中，等待负责人确认
        assert resp.json()["status"] == "in_progress"
        assert resp.json()["awaiting_confirm"] is True

        # 协作者上传交付物：记录但不标记完成
        resp3 = await client.post(
            "/api/report/photo",
            data={"token": token, "task_id": "T1"},
            files={"file": ("p.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert resp3.status_code == 200
        assert resp3.json()["status"] == "in_progress"
        assert resp3.json()["confirmed"] is False
        photo_name = resp3.json().get("photo")

        # 负责人视角能看到协作者进度与待确认标记
        owner_token = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": owner_token})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        row = next(m for m in t1["members"] if m["name"] == "李四")
        assert row["status"] == "completed"
        assert row["awaiting_confirm"] is True
        assert row["photo"]

        # 负责人确认后任务完成，待确认标记消失
        up = await client.post("/api/report/update", json={
            "token": owner_token,
            "task_id": "T1",
            "status": "completed",
        })
        assert up.status_code == 200
        assert up.json()["status"] == "completed"
        st2 = (await client.get(
            "/api/report/state", params={"token": owner_token})).json()
        t1b = next(t for t in st2["tasks"] if t["id"] == "T1")
        row2 = next(m for m in t1b["members"] if m["name"] == "李四")
        assert row2["awaiting_confirm"] is False

        for fname in (photo_name,):
            if fname:
                try:
                    (MEMORY_DIR / "attachments" / fname).unlink()
                except OSError:
                    pass


@pytest.mark.asyncio
async def test_report_hours_follow_member_status_changes(saved_plan):
    """各成员上报的工时自动累加；改回未完成时该成员工时退出累计并回退任务。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        r1 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
            "actual_hours": 3.0,
        })
        assert r1.status_code == 200

        zhang = await _make_token(client, member="张三")
        r2 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
            "actual_hours": 3.0,
        })
        assert r2.status_code == 200

        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["actual_hours"] == 6.0
        assert t1["status"] == "completed"

        # 李四改回进行中（不报工时）：其 3h 退出累计，任务回退进行中
        r3 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
        })
        assert r3.status_code == 200
        assert r3.json()["status"] == "in_progress"

        st2 = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1b = next(t for t in st2["tasks"] if t["id"] == "T1")
        assert t1b["actual_hours"] == 3.0
        assert t1b["status"] == "in_progress"
        rows = {m["name"]: m["actual_hours"] for m in t1b["members"]}
        assert rows["张三"] == 3.0
        assert rows.get("李四") is None  # 改回未完成后工时已清
        li_row = next(m for m in t1b["members"] if m["name"] == "李四")
        assert li_row["status"] == "in_progress"
        assert "回退为进行中" in li_row["note"]


@pytest.mark.asyncio
async def test_report_owner_can_revert_completed_to_pending(saved_plan):
    """负责人可把已完成任务改回待开始并持久化。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client, member="张三")
        await client.post("/api/report/update", json={
            "token": token, "task_id": "T1", "status": "completed",
        })
        r = await client.post("/api/report/update", json={
            "token": token, "task_id": "T1", "status": "pending",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "pending"
        st = (await client.get(
            "/api/report/state", params={"token": token})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "pending"


@pytest.mark.asyncio
async def test_report_only_owner_sets_completion_date(saved_plan):
    """任务完成日期只有负责人能设置；协作者提交日期不影响任务。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        r1 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
            "actual_end_date": "2026-09-10",
        })
        assert r1.status_code == 200

        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["actual_end_date"] is None  # 协作者提交的日期被忽略

        r2 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
            "actual_end_date": "2026-09-12",
        })
        assert r2.status_code == 200
        st2 = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1b = next(t for t in st2["tasks"] if t["id"] == "T1")
        assert t1b["actual_end_date"] == "2026-09-12"


@pytest.mark.asyncio
async def test_report_collaborator_can_report_blocked(saved_plan):
    """协作者可报阻塞：阻塞优先——任务整体置为阻塞（已完成任务也回退阻塞）。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
            "actual_hours": 3.0,
        })
        zhang = await _make_token(client, member="张三")
        await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
        })

        # 李四把已完成改为阻塞
        r = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "blocked",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "blocked"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "blocked"
        assert "阻塞" in li_row["note"]
        # 负责人行只反映负责人自己的上报（已完成），不被协作者阻塞强加
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "completed"


@pytest.mark.asyncio
async def test_report_collaborator_blocked_propagates_to_task(saved_plan):
    """协作者在任务未开始时报阻塞：任务整体立即置为阻塞。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        r = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "blocked",
            "note": "外部接口挂了",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "blocked"
        # 负责人从未上报：负责人行保持"未开始"，不被任务阻塞强加
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "pending"


@pytest.mark.asyncio
async def test_report_collaborator_revert_to_pending_resets_task(saved_plan):
    """协作者把"进行中"改回"待开始"且无其他活跃成员：任务回退待开始。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        r1 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
            "note": "我先开始",
        })
        assert r1.status_code == 200
        assert r1.json()["status"] == "in_progress"

        r2 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "pending",
        })
        assert r2.status_code == 200
        assert r2.json()["status"] == "pending"

        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "pending"
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "pending"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "pending"
        assert "改回待开始" in li_row["note"]


@pytest.mark.asyncio
async def test_report_collaborator_revert_keeps_task_when_owner_active(saved_plan):
    """负责人仍在进行时，协作者改回待开始不把任务拉回。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
        })
        zhang = await _make_token(client, member="张三")
        await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "in_progress",
        })

        r = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "pending",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "in_progress"

        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "in_progress"


@pytest.mark.asyncio
async def test_task_status_complete_requires_confirm(saved_plan):
    """主页面强制完成任务：有成员未完成时需确认，确认后成员行变"已确认"。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # 协作者李四报阻塞 → 任务整体阻塞
        li = await _make_token(client, member="李四")
        r = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "blocked",
            "note": "数据源挂了",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "blocked"

        plan = _load_saved_plan()
        # 不带 confirm_members 强制完成 → 400 + 未完成成员清单
        blocked = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "completed",
            "filename": TEST_PLAN,
        })
        assert blocked.status_code == 400
        body = blocked.json()
        assert body["unfinished_members"] == [
            {"name": "李四", "status": "blocked"}]
        # 任务仍是阻塞，未被改动
        plan = _load_saved_plan()
        assert plan.plan.tasks[0].status == "blocked"

        # 带 confirm_members 重发 → 完成，李四行变"已确认"
        ok = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "completed",
            "filename": TEST_PLAN,
            "confirm_members": ["李四"],
        })
        assert ok.status_code == 200
        data = ok.json()
        assert data["plan"]["plan"]["tasks"][0]["status"] == "completed"

        st = (await client.get(
            "/api/report/state", params={"token": li})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "completed"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "confirmed"
        # 主页面确认任务完成：负责人行同步为已完成
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "completed"


@pytest.mark.asyncio
async def test_task_status_regular_change_saves_and_syncs(saved_plan):
    """主页面改状态（非完成）：直接落盘但不生成版本，汇报页负责人行同步。"""
    from app.services.audit_store import list_versions

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        plan = _load_saved_plan()
        r = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "in_progress",
            "filename": TEST_PLAN,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["plan"]["plan"]["tasks"][0]["status"] == "in_progress"
        # 状态变更不生成版本（版本仅保存方案时生成）
        assert list_versions(TEST_PLAN) == []

        # 汇报页（负责人视角）：任务徽章为整体状态，负责人行只反映自己上报
        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "in_progress"
        # 主页面改状态 = 负责人确认：负责人行同步为进行中
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "in_progress"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "pending"


@pytest.mark.asyncio
async def test_task_status_rejects_stale_base_version(saved_plan):
    """主页面改状态带不匹配的版本号：并发校验拦截（409）。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # 先保存一次生成版本号
        plan = _load_saved_plan()
        saved = await client.post(
            "/api/save",
            params={"filename": TEST_PLAN},
            json=plan.model_dump(mode="json"),
        )
        assert saved.status_code == 200
        version_id = saved.json()["version_id"]

        # 用错误版本号改状态 → 409
        r = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "in_progress",
            "filename": TEST_PLAN,
            "base_version": "wrong-version",
        })
        assert r.status_code == 409

        # 用正确版本号 → 成功
        r2 = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "in_progress",
            "filename": TEST_PLAN,
            "base_version": version_id,
        })
        assert r2.status_code == 200

    try:
        from app.services.audit_store import AUDIT_DIR, VERSION_DIR
        import shutil
        shutil.rmtree(VERSION_DIR / TEST_PLAN, ignore_errors=True)
        (AUDIT_DIR / f"{TEST_PLAN}.jsonl").unlink()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_status_and_save_reject_stale_fingerprint(saved_plan):
    """成员汇报后主页面用旧指纹改状态/保存：并发校验拦截（409），不覆盖汇报。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # 保存并获取指纹
        plan = _load_saved_plan()
        saved = await client.post(
            "/api/save",
            params={"filename": TEST_PLAN},
            json=plan.model_dump(mode="json"),
        )
        assert saved.status_code == 200
        fp1 = saved.json()["fingerprint"]

        # 成员汇报：落盘但版本树不前进（版本仅保存时生成）
        li = await _make_token(client, member="李四")
        rep = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
            "actual_hours": 3.0,
        })
        assert rep.status_code == 200

        # 主页面用旧指纹改状态 → 409，成员汇报的工时不被覆盖
        stale = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "blocked",
            "filename": TEST_PLAN,
            "base_fingerprint": fp1,
        })
        assert stale.status_code == 409
        disk = _load_saved_plan()
        assert disk.plan.tasks[0].status == "in_progress"
        assert disk.plan.tasks[0].actual_hours == 3.0

        # 主页面用旧指纹保存 → 409
        stale_save = await client.post(
            "/api/save",
            params={"filename": TEST_PLAN, "base_fingerprint": fp1},
            json=plan.model_dump(mode="json"),
        )
        assert stale_save.status_code == 409

        # 用最新指纹（模拟重载后）改状态 → 成功
        fresh_fp = (await client.get(
            "/api/plan-fingerprint", params={"filename": TEST_PLAN}
        )).json()["fingerprint"]
        fresh_plan = _load_saved_plan()
        ok = await client.post("/api/task-status", json={
            "plan": fresh_plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "blocked",
            "filename": TEST_PLAN,
            "base_fingerprint": fresh_fp,
        })
        assert ok.status_code == 200

    try:
        from app.services.audit_store import AUDIT_DIR, VERSION_DIR
        import shutil
        shutil.rmtree(VERSION_DIR / TEST_PLAN, ignore_errors=True)
        (AUDIT_DIR / f"{TEST_PLAN}.jsonl").unlink()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_task_status_unblock_requires_confirm(saved_plan):
    """主页面解除阻塞（blocked→in_progress）：成员仍报阻塞时需确认处理。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # 协作者李四报阻塞 → 任务整体阻塞
        li = await _make_token(client, member="李四")
        rep = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "blocked",
            "note": "数据源挂了",
        })
        assert rep.status_code == 200
        assert rep.json()["status"] == "blocked"

        plan = _load_saved_plan()
        # 不带确认解除阻塞 → 400 + 阻塞成员清单
        blocked = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "in_progress",
            "filename": TEST_PLAN,
        })
        assert blocked.status_code == 400
        body = blocked.json()
        assert body["blocked_members"] == [
            {"name": "李四", "status": "blocked"}]
        # 任务仍是阻塞
        assert _load_saved_plan().plan.tasks[0].status == "blocked"

        # 带确认解除阻塞 → 任务进行中，李四行标记为已处理
        ok = await client.post("/api/task-status", json={
            "plan": plan.model_dump(mode="json"),
            "task_id": "T1",
            "status": "in_progress",
            "filename": TEST_PLAN,
            "confirm_members": ["李四"],
        })
        assert ok.status_code == 200
        assert ok.json()["plan"]["plan"]["tasks"][0]["status"] == "in_progress"

        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "in_progress"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "in_progress"
        assert "阻塞已处理" in li_row["note"]
        # 主页面解除阻塞 = 负责人确认：负责人行同步为进行中
        zhang_row = next(m for m in t1["members"] if m["name"] == "张三")
        assert zhang_row["status"] == "in_progress"

    try:
        from app.services.audit_store import AUDIT_DIR, VERSION_DIR
        import shutil
        shutil.rmtree(VERSION_DIR / TEST_PLAN, ignore_errors=True)
        (AUDIT_DIR / f"{TEST_PLAN}.jsonl").unlink()
    except OSError:
        pass


@pytest.mark.asyncio
async def test_report_attachment_serves_photo(saved_plan):
    """交付物照片可通过鉴权接口查看，非任务成员不可访问。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token = await _make_token(client, member="张三")
        up = await client.post(
            "/api/report/photo",
            data={"token": token, "task_id": "T1"},
            files={"file": ("p.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert up.status_code == 200
        photo = up.json()["photo"]
        try:
            resp = await client.get(
                "/api/report/attachment",
                params={"token": token, "task_id": "T1"},
            )
            assert resp.status_code == 200
            assert resp.content.startswith(b"\x89PNG")

            # 非任务成员（李四不是 T2 成员）不能查看
            token_li = await _make_token(client, member="李四")
            bad = await client.get(
                "/api/report/attachment",
                params={"token": token_li, "task_id": "T2"},
            )
            assert bad.status_code in (403, 404)
        finally:
            try:
                (MEMORY_DIR / "attachments" / photo).unlink()
            except OSError:
                pass


@pytest.mark.asyncio
async def test_report_confirmed_volunteer_can_report():
    """大型项目：已确认志愿者可生成链接、汇报自己的部分，角色显示志愿者。"""
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="志愿测试", description=""),
            members=[TeamMember(
                name="张三", role="骨干 / 模块负责人",
                daily_available_hours=4, unavailable_dates=[])],
            deadline=date(2026, 9, 18),
            project_mode="large_project",
        ),
        plan=PlanOutput(
            tasks=[SubTask(
                id="T1", name="模块子任务", estimated_hours=6,
                assignee_id="张三", status="pending")],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
        volunteer_pool=[
            Volunteer(name="李四", task_id="T1", status="已确认"),
        ],
    )
    filename = "test_report_volunteer.json"
    path = MEMORY_DIR / filename
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            link = await client.post(
                "/api/report/link",
                json={"filename": filename, "member": "李四"},
            )
            assert link.status_code == 200
            token = link.json()["token"]

            up = await client.post("/api/report/update", json={
                "token": token, "task_id": "T1",
                "status": "completed", "actual_hours": 2.0,
            })
            assert up.status_code == 200
            assert up.json()["awaiting_confirm"] is True

            st = (await client.get(
                "/api/report/state", params={"token": token})).json()
            t1 = next(t for t in st["tasks"] if t["id"] == "T1")
            li_row = next(m for m in t1["members"] if m["name"] == "李四")
            assert li_row["role"] == "志愿者"
            assert li_row["status"] == "completed"
            assert li_row["actual_hours"] == 2.0
    finally:
        try:
            path.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_task_status_complete_confirms_volunteer_even_unreported():
    """主页面完成任务：已确认但从未上报的志愿者必须纳入确认，完成时不悬挂。"""
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="志愿者完成测试", description=""),
            members=[TeamMember(
                name="张三", role="骨干 / 模块负责人",
                daily_available_hours=4, unavailable_dates=[])],
            deadline=date(2026, 9, 18),
            project_mode="large_project",
        ),
        plan=PlanOutput(
            tasks=[SubTask(
                id="T1", name="模块子任务", estimated_hours=6,
                assignee_id="张三", extra_helpers_needed=1,
                status="pending")],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
        volunteer_pool=[
            Volunteer(name="王五", task_id="T1", status="已确认"),
        ],
    )
    filename = "test_report_volunteer_complete.json"
    path = MEMORY_DIR / filename
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # 志愿者从未上报：主页面完成必须确认志愿者
            blocked = await client.post("/api/task-status", json={
                "plan": plan.model_dump(mode="json"),
                "task_id": "T1",
                "status": "completed",
                "filename": filename,
            })
            assert blocked.status_code == 400
            body = blocked.json()
            assert body["unfinished_members"] == [
                {"name": "王五", "status": "pending"}]

            ok = await client.post("/api/task-status", json={
                "plan": plan.model_dump(mode="json"),
                "task_id": "T1",
                "status": "completed",
                "filename": filename,
                "confirm_members": ["王五"],
            })
            assert ok.status_code == 200

            zhang = await client.post(
                "/api/report/link",
                json={"filename": filename, "member": "张三"},
            )
            st = (await client.get(
                "/api/report/state",
                params={"token": zhang.json()["token"]})).json()
            t1 = next(t for t in st["tasks"] if t["id"] == "T1")
            assert t1["status"] == "completed"
            wang_row = next(m for m in t1["members"] if m["name"] == "王五")
            assert wang_row["role"] == "志愿者"
            assert wang_row["status"] == "confirmed"
    finally:
        try:
            path.unlink()
        except OSError:
            pass
        try:
            from app.services.report_link import NOTES_FILE
            data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
            data = {
                k: v for k, v in data.items()
                if not k.startswith(filename + "::")
            }
            NOTES_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass


@pytest.mark.asyncio
async def test_report_overview_for_project_leader():
    """大型项目：项目负责人拿到团队总览（模块+任务+成员进度），骨干没有。"""
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="大型总览测试", description=""),
            members=[
                TeamMember(
                    name="张三", role="项目负责人",
                    daily_available_hours=4, unavailable_dates=[]),
                TeamMember(
                    name="李四", role="骨干 / 模块负责人",
                    manager="张三",
                    daily_available_hours=4, unavailable_dates=[]),
            ],
            deadline=date(2026, 9, 18),
            project_mode="large_project",
        ),
        plan=PlanOutput(
            modules=[ProjectModule(
                id="M1", name="内容制作", assignee_id="李四", order=1,
            )],
            tasks=[SubTask(
                id="T1", name="撰写正文", estimated_hours=8,
                module_id="M1", assignee_id="李四", status="in_progress")],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
    )
    filename = "test_report_overview.json"
    path = MEMORY_DIR / filename
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            link = await client.post(
                "/api/report/link",
                json={"filename": filename, "member": "张三"},
            )
            assert link.status_code == 200
            st = (await client.get(
                "/api/report/state",
                params={"token": link.json()["token"]})).json()
            ov = st["overview"]
            assert ov is not None
            assert ov["stats"]["total"] == 1
            assert ov["modules"][0]["id"] == "M1"
            task = ov["modules"][0]["tasks"][0]
            assert task["id"] == "T1"
            assert any(
                m["name"] == "李四" and m["role"] == "负责人"
                for m in task["members"]
            )

            # 骨干看不到总览
            link2 = await client.post(
                "/api/report/link",
                json={"filename": filename, "member": "李四"},
            )
            st2 = (await client.get(
                "/api/report/state",
                params={"token": link2.json()["token"]})).json()
            assert st2["overview"] is None
            assert any(t["id"] == "T1" for t in st2["tasks"])
    finally:
        try:
            path.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_report_owner_complete_blocks_unreported_confirmed_volunteer():
    """负责人从汇报页标完成时，已确认但从未上报的志愿者必须拦截（与主页面同口径）。"""
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="汇报页志愿者完成测试", description=""),
            members=[TeamMember(
                name="张三", role="骨干 / 模块负责人",
                daily_available_hours=4, unavailable_dates=[])],
            deadline=date(2026, 9, 18),
            project_mode="large_project",
        ),
        plan=PlanOutput(
            tasks=[SubTask(
                id="T1", name="模块子任务", estimated_hours=6,
                assignee_id="张三", extra_helpers_needed=1,
                status="in_progress")],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
        volunteer_pool=[
            Volunteer(name="王五", task_id="T1", status="已确认"),
        ],
    )
    filename = "test_report_owner_volunteer_block.json"
    path = MEMORY_DIR / filename
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            owner = await client.post(
                "/api/report/link",
                json={"filename": filename, "member": "张三"},
            )
            token = owner.json()["token"]

            # 志愿者从未上报：负责人直接标完成 → 400 并列出志愿者
            blocked = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "completed",
            })
            assert blocked.status_code == 400
            assert "王五" in blocked.json()["detail"]

            # 负责人先代确认志愿者，再标完成 → 通过，且志愿者行不悬挂
            confirm = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "completed",
                "member": "王五",
            })
            assert confirm.status_code == 200

            ok = await client.post("/api/report/update", json={
                "token": token,
                "task_id": "T1",
                "status": "completed",
            })
            assert ok.status_code == 200
            assert ok.json()["status"] == "completed"

            st = (await client.get(
                "/api/report/state",
                params={"token": token})).json()
            t1 = next(t for t in st["tasks"] if t["id"] == "T1")
            assert t1["status"] == "completed"
            wang_row = next(m for m in t1["members"] if m["name"] == "王五")
            assert wang_row["role"] == "志愿者"
            assert wang_row["status"] == "confirmed"
    finally:
        try:
            path.unlink()
        except OSError:
            pass
        try:
            from app.services.report_link import NOTES_FILE
            data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
            data = {
                k: v for k, v in data.items()
                if not k.startswith(filename + "::")
            }
            NOTES_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass


@pytest.mark.asyncio
async def test_report_owner_cannot_complete_while_member_unfinished(saved_plan):
    """负责人确认完成时，若有成员已上报未完成，则拒绝并提示。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
        })
        zhang = await _make_token(client, member="张三")
        r = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
        })
        assert r.status_code == 400
        assert "未完成" in r.json()["detail"]

        # 李四把自己的部分标为已完成后再确认即可
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
        })
        r2 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
        })
        assert r2.status_code == 200
        assert r2.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_report_owner_can_confirm_member_status(saved_plan):
    """负责人可代协作者确认完成：成员状态 confirmed、工时保留、待确认消失。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        r1 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
            "actual_hours": 3.0,
        })
        assert r1.status_code == 200
        assert r1.json()["awaiting_confirm"] is True

        # 负责人张三代李四确认完成
        zhang = await _make_token(client, member="张三")
        r2 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1",
            "status": "completed", "member": "李四",
        })
        assert r2.status_code == 200
        assert r2.json()["member_status"] == "confirmed"

        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "confirmed"
        assert li_row["awaiting_confirm"] is False
        assert li_row["actual_hours"] == 3.0  # 代确认不清除其工时

        # 非负责人不能代他人确认
        r3 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1",
            "status": "completed", "member": "张三",
        })
        assert r3.status_code == 403


@pytest.mark.asyncio
async def test_owner_confirm_after_member_revert_completes_task(saved_plan):
    """已完成任务被成员改回进行中后，负责人代确认该成员：任务自动回已完成。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "completed",
            "actual_hours": 3.0,
        })
        zhang = await _make_token(client, member="张三")
        r1 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1", "status": "completed",
        })
        assert r1.status_code == 200
        assert r1.json()["status"] == "completed"

        # 协作者把已完成改回进行中 → 任务回退进行中
        r2 = await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
        })
        assert r2.status_code == 200
        assert r2.json()["status"] == "in_progress"

        # 负责人代确认协作者 → 负责人已 completed、协作者 confirmed，
        # 任务应自动回已完成（主页面同步）
        r3 = await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1",
            "status": "completed", "member": "李四",
        })
        assert r3.status_code == 200
        assert r3.json()["status"] == "completed"

        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        assert t1["status"] == "completed"
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["status"] == "confirmed"


@pytest.mark.asyncio
async def test_report_photo_and_note_survive_status_changes(saved_plan):
    """照片与备注不随成员后续改状态/负责人代确认而消失。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        li = await _make_token(client, member="李四")
        # 协作者上传照片+备注
        up = await client.post(
            "/api/report/photo",
            data={"token": li, "task_id": "T1"},
            files={"file": ("p.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert up.status_code == 200
        photo_name = up.json()["photo"]

        # 协作者再改一次自己的状态（不带照片/备注）
        await client.post("/api/report/update", json={
            "token": li, "task_id": "T1", "status": "in_progress",
        })

        zhang = await _make_token(client, member="张三")
        st = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1 = next(t for t in st["tasks"] if t["id"] == "T1")
        li_row = next(m for m in t1["members"] if m["name"] == "李四")
        assert li_row["photo"] == photo_name  # 改状态后照片仍在

        # 负责人代确认后照片仍在
        await client.post("/api/report/update", json={
            "token": zhang, "task_id": "T1",
            "status": "completed", "member": "李四",
        })
        st2 = (await client.get(
            "/api/report/state", params={"token": zhang})).json()
        t1b = next(t for t in st2["tasks"] if t["id"] == "T1")
        li_row2 = next(m for m in t1b["members"] if m["name"] == "李四")
        assert li_row2["photo"] == photo_name
        assert li_row2["status"] == "confirmed"

        try:
            (MEMORY_DIR / "attachments" / photo_name).unlink()
        except OSError:
            pass


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


@pytest.mark.asyncio
async def test_meeting_video_watches_and_listens(monkeypatch):
    """会议旁听支持录像：抽帧看画面 + 抽音频听内容，边看边听整理任务。"""
    import app.services.media_analysis as media
    import app.services.realtime_client as rt

    def fake_frames(content, max_frames):
        return [b"JPEG1", b"JPEG2"]

    def fake_audio(content):
        return b"pcm"

    def fake_run(parts, max_tokens, omni_mode, timeout=180):
        assert any(p["type"] == "image" for p in parts)
        return "屏幕显示项目排期表，张三正在讲解"

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text=(
            "【总结】\n确定视频理解演示流程\n"
            "【任务】\n- 录制演示视频 | 张三 | 本周五\n"
            "【风险】\nA3 资源紧张需提前预检"))

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_run)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/realtime/meeting",
            files={"file": ("meeting.mp4", b"fake-video", "video/mp4")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "确定视频理解演示流程"
    assert data["tasks"][0]["name"] == "录制演示视频"
    assert data["tasks"][0]["deadline"] == "本周五"
    assert "第 1 帧" in data["visual"]
    assert "排期表" in data["visual"]
    assert data["has_video"] is True
    assert data["risks"]


@pytest.mark.asyncio
async def test_meeting_video_without_audio_synthesizes_from_frames(monkeypatch):
    """无声轨录屏视频：仅凭画面理解也能整理出会议要点与任务。"""
    import app.services.media_analysis as media
    import app.services.realtime_client as rt

    def fake_frames(content, max_frames):
        return [b"JPEG1"]

    def fake_audio(content):
        return None

    def fake_run(parts, max_tokens, omni_mode, timeout=180):
        return "白板上写着三步计划：调研、开发、联调"

    async def fake_chat(self, **kwargs):
        messages = kwargs.get("messages") or []
        text = messages[0].get("content") if messages else ""
        assert "画面理解" in str(text)  # 走画面合成路径
        return RealtimeChatResult(text=(
            "【总结】\n白板三步计划\n"
            "【任务】\n- 完成调研 | 无 | 无\n"
            "【风险】\n无"))

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_run)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/realtime/meeting",
            files={"file": ("screencast.mp4", b"fake-video", "video/mp4")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == "白板三步计划"
    assert data["tasks"][0]["name"] == "完成调研"
    assert "第 1 帧" in data["visual"]
    assert data["has_video"] is True
