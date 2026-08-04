"""Route-level multi-user ACL regression tests."""

import json
from datetime import date

from fastapi.testclient import TestClient

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)


def _plan(name="AlicePlan"):
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name=name, description="alice 私有方案"),
            members=[TeamMember(name="小文")],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(tasks=[SubTask(id="T1", name="调研", estimated_hours=2)], summary="alice 私有"),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def _setup(tmp_path, monkeypatch):
    import app.config as config
    import app.main as main
    import app.services.auth_store as auth
    import app.services.collab as collab
    import app.web.routes as routes

    users = json.dumps([
        {"username": "alice", "password": "pw-alice", "role": "admin"},
        {"username": "bob", "password": "pw-bob", "role": "editor"},
        {"username": "carol", "password": "pw-carol", "role": "viewer"},
    ])
    monkeypatch.setattr(config, "APP_USERS_JSON", users)
    monkeypatch.setattr(config, "APP_ADMIN_TOKEN", "")
    monkeypatch.setattr(auth, "APP_USERS_JSON", users)
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "")
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(auth, "ACL_FILE", tmp_path / "acl.json")
    monkeypatch.setattr(routes, "MEMORY_DIR", tmp_path / "memory")
    store_audit(tmp_path, monkeypatch)
    monkeypatch.setattr(collab, "MEMORY_DIR", tmp_path / "memory")
    return TestClient(main.app)


def store_audit(tmp_path, monkeypatch):
    import app.services.audit_store as store
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")
    return store


def test_acl_blocks_history_export_share_and_rollback(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    token_a = client.post("/api/auth/login", json={"username": "alice", "password": "pw-alice"}).json()["token"]
    token_b = client.post("/api/auth/login", json={"username": "bob", "password": "pw-bob"}).json()["token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    saved = client.post("/api/save", json=_plan().model_dump(mode="json"), headers=headers_a).json()
    filename = saved["filename"]

    assert client.get(f"/api/load/{filename}", headers=headers_b).status_code == 403
    assert client.get(f"/api/plan-history/{filename}", headers=headers_b).status_code == 403
    assert client.get(f"/api/plans/{filename}/export", headers=headers_b).status_code == 403
    assert client.post("/api/share", json={"filename": filename}, headers=headers_b).status_code == 403
    assert client.post(f"/api/plan-rollback/{filename}/x", headers=headers_b).status_code == 403

    result = client.post("/api/knowledge", json={"question": "alice 私有方案"}, headers=headers_b).json()
    assert all("AlicePlan" not in item["name"] for item in result.get("sources", []))

    tools = client.post("/api/tools/call", json={
        "tool": "knowledge", "args": {"question": "alice 私有方案"}, "plan": None,
    }, headers=headers_b).json()
    assert all("AlicePlan" not in item["name"] for item in tools.get("result", {}).get("sources", []))

    agent = client.post("/api/agent/ask", json={
        "question": "alice 私有方案知识", "plan": None,
    }, headers=headers_b).json()
    assert "AlicePlan" not in agent["answer"]

    import app.services.auth_store as auth
    auth.set_acl(filename, owner="alice", editors=["bob"])
    save2 = client.post("/api/save", json=_plan().model_dump(mode="json"),
                        headers=headers_b, params={"filename": filename})
    assert save2.status_code == 200
    acl = auth.get_acl(filename)
    assert acl["owner"] == "alice"
    assert "bob" in acl["editors"]
    assert client.get(f"/api/load/{filename}", headers=headers_a).status_code == 200

    auth.set_acl(filename, owner="alice", editors=["bob"], viewers=["carol"])
    history = client.get(f"/api/plan-history/{filename}", headers=headers_a).json()
    version_id = history["versions"][0]["version_id"]
    rolled = client.post(
        f"/api/plan-rollback/{filename}/{version_id}", headers=headers_a).json()
    rolled_acl = auth.get_acl(rolled["filename"])
    assert rolled_acl["owner"] == "alice"
    assert "carol" in rolled_acl.get("viewers", [])
