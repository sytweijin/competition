"""API 集成测试。

覆盖核心业务端点（不依赖 LLM 的路径）。
"""
from httpx import AsyncClient, ASGITransport
import pytest

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _sample_course_input(members=None, name="测试课程"):
    if members is None:
        members = [
            {"name": "张三", "skill_tags": ["开发", "文案"], "available_hours": 140, "daily_available_hours": 4},
            {"name": "李四", "skill_tags": ["摄影", "设计"], "available_hours": 100, "daily_available_hours": 4},
        ]
    return {
        "course": {"name": name, "description": "测试课程描述 包含实践和推送"},
        "members": members,
        "deadline": "2026-08-20",
        "additional_requirements": "需提交推送成果",
        "background": "测试背景",
        "requirements": "需提交推送成果",
        "default_start_date": "2026-07-01",
        "default_end_date": "2026-08-20",
        "uploaded_files": [],
        "requirement_analysis": {},
    }


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def _full_plan_payload():
    """构造一个 3 任务串行的完整 FullPlan（用于 recompute 测试）。"""
    return {
        "input": {
            "course": {"name": "测试课程", "description": "测试"},
            "members": [{"name": "张三", "skill_tags": ["开发"], "available_hours": 140, "daily_available_hours": 4}],
            "deadline": "2026-08-20",
            "additional_requirements": "",
        },
        "plan": {
            "tasks": [
                {"id": "T1", "name": "A", "estimated_hours": 8.0, "dependencies": [], "status": "pending"},
                {"id": "T2", "name": "B", "estimated_hours": 8.0, "dependencies": ["T1"], "status": "pending"},
                {"id": "T3", "name": "C", "estimated_hours": 8.0, "dependencies": ["T2"], "status": "pending"},
            ],
            "summary": "测试计划", "reasoning": "",
        },
        "timeline": {"tasks": [], "critical_path": [], "total_days": 0, "note": "", "reasoning": ""},
        "qa_matrix": {"assignments": [], "workload": {}, "note": ""},
        "report": {"summary": "", "timeline_section": "", "qa_matrix_section": "", "risk_note": ""},
        "version": "2.0",
    }


@pytest.mark.asyncio
async def test_recompute_shortens_duration_when_completed(client):
    """标 T1 完成后，总工期应缩短。"""
    payload = _full_plan_payload()
    resp = await client.post("/api/recompute", json=payload)
    assert resp.status_code == 200
    base_days = resp.json()["timeline"]["total_days"]

    # 把 T1 标完成，重算
    payload["plan"]["tasks"][0]["status"] = "completed"
    resp2 = await client.post("/api/recompute", json=payload)
    assert resp2.status_code == 200
    after_days = resp2.json()["timeline"]["total_days"]
    assert after_days < base_days, f"完成 T1 后工期应缩短: {after_days} >= {base_days}"

def _minimal_full_plan():
    return {
        "input": {
            "course": {"name": "保存测试", "description": "d"},
            "members": [{"name": "张三", "skill_tags": [], "available_hours": 20, "daily_available_hours": 4}],
            "deadline": "2026-08-20", "additional_requirements": "",
        },
        "plan": {"tasks": [{"id": "T1", "name": "A", "estimated_hours": 4, "dependencies": [], "status": "pending"}], "summary": "s", "reasoning": ""},
        "timeline": {"tasks": [], "critical_path": [], "total_days": 1, "note": "", "reasoning": ""},
        "qa_matrix": {"assignments": [], "workload": {}, "note": ""},
        "report": {"summary": "", "timeline_section": "", "qa_matrix_section": "", "risk_note": ""},
        "version": "2.0",
    }


@pytest.mark.asyncio
async def test_save_endpoint(client):
    """保存接口不应因 import re 缺失而 500（P0 回归测试）。"""
    resp = await client.post("/api/save", json=_minimal_full_plan())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["filename"].endswith(".json")


# ──────────── Draft（快速模式，不依赖 LLM） ────────────


@pytest.mark.asyncio
async def test_draft_fast_mode_returns_plan(client):
    """use_ai=false 应返回确定性快速草案，不调 LLM。"""
    payload = {"input": _sample_course_input(), "use_ai": False}
    resp = await client.post("/api/draft", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert len(data["plan"]["tasks"]) > 0


@pytest.mark.asyncio
async def test_draft_ai_mode_without_key_still_returns_plan(client):
    """use_ai=true 但无 API key 时自动降级为兜底，不抛 500。"""
    payload = {"input": _sample_course_input(), "use_ai": True}
    resp = await client.post("/api/draft", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert len(data["plan"]["tasks"]) > 0


# ──────────── Draft Mutate（增删改排序） ────────────


@pytest.mark.asyncio
async def test_draft_mutate_add_task(client):
    """新增任务应出现在返回草案中。"""
    payload = {"input": _sample_course_input(), "use_ai": False}
    draft_resp = await client.post("/api/draft", json=payload)
    draft = draft_resp.json()["plan"]

    mutate_payload = {
        "plan": draft,
        "operations": [
            {"op": "add", "task": {
                "id": "T99", "name": "新增测试任务", "estimated_hours": 2.0,
                "dependencies": [], "required_skills": ["测试"],
                "order": 99,
            }}
        ],
    }
    resp = await client.post("/api/draft/mutate", json=mutate_payload)
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    ids = [t["id"] for t in tasks]
    assert "T99" in ids


@pytest.mark.asyncio
async def test_draft_mutate_remove_task(client):
    """删除任务后不应再出现。"""
    payload = {"input": _sample_course_input(), "use_ai": False}
    draft_resp = await client.post("/api/draft", json=payload)
    draft = draft_resp.json()["plan"]
    task_id = draft["tasks"][0]["id"]

    mutate_payload = {
        "plan": draft,
        "operations": [{"op": "remove", "task_id": task_id}],
    }
    resp = await client.post("/api/draft/mutate", json=mutate_payload)
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert task_id not in ids


# ──────────── Confirm Draft ────────────


@pytest.mark.asyncio
async def test_confirm_draft_assigns_members(client):
    """确认草案后应自动分配负责人并返回 FullPlan。"""
    payload = {"input": _sample_course_input(), "use_ai": False}
    draft_resp = await client.post("/api/draft", json=payload)
    draft = draft_resp.json()["plan"]

    confirm_payload = {
        "input": _sample_course_input(),
        "plan": draft,
    }
    resp = await client.post("/api/confirm-draft", json=confirm_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "qa_matrix" in data
    assert "timeline" in data
    assert "report" in data


# ──────────── Workload ────────────


@pytest.mark.asyncio
async def test_workload_returns_snapshot(client):
    """工作量快照应正确统计各成员工时。"""
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": (await (await client.post("/api/draft", json={
            "input": _sample_course_input(), "use_ai": False
        })).json())["plan"],
    }
    plan_resp = await client.post("/api/confirm-draft", json=confirm_payload)
    full_plan = plan_resp.json()

    resp = await client.post("/api/workload", json=full_plan)
    assert resp.status_code == 200
    data = resp.json()
    assert "members" in data
    assert "average_hours" in data


# ──────────── Manual Assignment ────────────


@pytest.mark.asyncio
async def test_manual_assignment_updates_owner(client):
    """手动指定负责人后应正确反映在结果中。"""
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": (await (await client.post("/api/draft", json={
            "input": _sample_course_input(), "use_ai": False
        })).json())["plan"],
    }
    plan_resp = await client.post("/api/confirm-draft", json=confirm_payload)
    full_plan = plan_resp.json()
    task_id = full_plan["plan"]["tasks"][0]["id"]

    assign_payload = {
        "plan": full_plan,
        "assignees": {task_id: "张三"},
        "collaborators": {},
    }
    resp = await client.post("/api/manual-assignment", json=assign_payload)
    assert resp.status_code == 200
    data = resp.json()
    qa = data["qa_matrix"]["assignments"]
    assigned = [a for a in qa if a["task_id"] == task_id]
    assert len(assigned) == 1
    assert assigned[0]["presenter"] == "张三"


# ──────────── Export ────────────


@pytest.mark.asyncio
async def test_export_markdown_returns_text(client):
    """导出 Markdown 应返回纯文本/不抛 500。"""
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": (await (await client.post("/api/draft", json={
            "input": _sample_course_input(), "use_ai": False
        })).json())["plan"],
    }
    plan_resp = await client.post("/api/confirm-draft", json=confirm_payload)
    full_plan = plan_resp.json()

    resp = await client.post("/api/export/markdown", json=full_plan)
    assert resp.status_code == 200
    assert len(resp.text) > 50