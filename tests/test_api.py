"""API 集成测试"""
from httpx import AsyncClient, ASGITransport
import pytest

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


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