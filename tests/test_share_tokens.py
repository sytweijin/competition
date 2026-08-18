"""只读分享令牌的创建、读取与过期回归测试。"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)


def _plan(name: str = "只读分享测试") -> FullPlan:
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name=name, description="测试"),
            members=[TeamMember(name="小林", available_hours=20)],
            deadline=date(2026, 8, 30),
        ),
        plan=PlanOutput(
            tasks=[SubTask(id="T1", name="调研", estimated_hours=2)],
            summary="测试计划",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[], workload={"小林": 2}),
        report=ReportOutput(summary=""),
    )


def _client():
    import app.main
    return TestClient(app.main.app)


def test_share_store_create_and_read(tmp_path, monkeypatch):
    from app.services import share_store

    monkeypatch.setattr(share_store, "SHARE_FILE", tmp_path / "shares.json")
    monkeypatch.setattr(share_store, "get_object_storage", lambda: None)

    token = share_store.create_share("demo.json")
    assert token
    entry = share_store.get_share_entry(token)
    assert entry["filename"] == "demo.json"
    assert share_store.get_share_filename(token) == "demo.json"
    assert share_store.share_status(token) == "active"


def test_share_store_expiry(tmp_path, monkeypatch):
    from app.services import share_store

    monkeypatch.setattr(share_store, "SHARE_FILE", tmp_path / "shares.json")
    monkeypatch.setattr(share_store, "get_object_storage", lambda: None)

    token = share_store.create_share("demo.json", ttl_seconds=60)
    assert share_store.get_share_entry(token, now=10**12) is None
    assert share_store.share_status(token, now=10**12) == "expired"


def test_read_share_link_loads_plan():
    client = _client()
    saved = client.post("/api/save", json=_plan().model_dump(mode="json"))
    assert saved.status_code == 200
    filename = saved.json()["filename"]

    shared = client.post("/api/share", json={"filename": filename})
    assert shared.status_code == 200
    token = shared.json()["token"]
    assert shared.json()["permission"] == "read"

    opened = client.get(f"/api/share/{token}")
    assert opened.status_code == 200
    assert opened.headers.get("x-share-created-at")
    assert opened.json()["plan"]["tasks"][0]["id"] == "T1"


def test_invalid_share_link_is_404():
    client = _client()
    assert client.get("/api/share/does-not-exist").status_code == 404
