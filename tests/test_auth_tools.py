"""Auth, tools, save-conflict and multimodal file tests."""

from datetime import date

from fastapi.testclient import TestClient

from app.file_analysis import extract_text
from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)
from app.services.tools import call_tool


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试", description=""),
            members=[TeamMember(name="小文")],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(
            tasks=[SubTask(id="T1", name="调研", estimated_hours=2)],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_tools_call_workload_and_unknown():
    plan = _plan()
    result = call_tool("workload", {}, plan)
    assert "members" in result
    try:
        call_tool("unknown", {}, plan)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown tool should fail")


def test_auth_middleware(monkeypatch):
    import app.config as config
    import app.main as main
    import app.services.auth_store as auth

    monkeypatch.setattr(config, "APP_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "secret")
    client = TestClient(main.app)
    assert client.get("/api/plans").status_code == 401
    r = client.get("/api/plans", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert client.get("/api/health").status_code == 200


def test_save_conflict_detected(tmp_path, monkeypatch):
    import app.main as main
    import app.services.audit_store as store
    import app.web.routes as routes

    monkeypatch.setattr(main, "APP_ADMIN_TOKEN", "")
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(routes, "MEMORY_DIR", tmp_path / "memory")
    client = TestClient(main.app)
    plan = _plan().model_dump(mode="json")
    r1 = client.post("/api/save?filename=test.json", json=plan)
    assert r1.status_code == 200
    v1 = r1.json()["version_id"]
    r2 = client.post(
        "/api/save?filename=test.json&base_version=" + v1, json=plan)
    assert r2.status_code == 200
    r3 = client.post(
        "/api/save?filename=test.json&base_version=" + v1, json=plan)
    assert r3.status_code == 409


def test_multimodal_files_extract_metadata():
    assert "图片文件" in extract_text("photo.png", b"abc")
    assert "音频文件" in extract_text("voice.mp3", b"abc")


def test_share_mode_blocks_writes(tmp_path, monkeypatch):
    import app.main as main
    import app.services.audit_store as store
    import app.web.routes as routes

    monkeypatch.setattr(main, "APP_ADMIN_TOKEN", "")
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(routes, "MEMORY_DIR", tmp_path / "memory")
    client = TestClient(main.app)
    plan = _plan().model_dump(mode="json")
    r = client.post("/api/save", json=plan, headers={"X-Share-Token": "1"})
    assert r.status_code == 403
