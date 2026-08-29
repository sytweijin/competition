"""鉴权开启时成员汇报令牌仍可免登录（P1 回归）。"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

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


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="鉴权汇报测试", description=""),
            members=[TeamMember(
                name="张三", role="执行成员",
                daily_available_hours=4, unavailable_dates=[])],
            deadline=date.today() + timedelta(days=14),
        ),
        plan=PlanOutput(
            tasks=[SubTask(
                id="T1", name="调研", estimated_hours=6,
                assignee_id="张三", status="pending")],
            summary="测试",
        ),
        timeline=TimelineOutput(
            tasks=[], critical_path=[], total_days=0, note="", reasoning=""),
        qa_matrix=QAOutput(assignments=[], workload={}, note=""),
        report=ReportOutput(
            summary="", timeline_section="",
            qa_matrix_section="", risk_note=""),
    )


def _setup(tmp_path, monkeypatch):
    """开启 APP_ADMIN_TOKEN，并把全部存储重定向到临时目录。"""
    import app.config as config
    import app.main as main
    import app.services.audit_store as audit_store
    import app.services.auth_store as auth
    import app.services.collab as collab
    import app.services.report_link as report_link
    import app.web.routes as routes
    import app.web.routers.report as report_router

    monkeypatch.setattr(config, "APP_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(config, "APP_USERS_JSON", "")
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(auth, "APP_USERS_JSON", "")
    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(auth, "ACL_FILE", tmp_path / "acl.json")
    monkeypatch.setattr(routes, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(collab, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(audit_store, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(audit_store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(audit_store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(report_link, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(
        report_link, "REPORT_FILE", tmp_path / "memory" / "report_tokens.json")
    monkeypatch.setattr(
        report_link, "NOTES_FILE", tmp_path / "memory" / "report_notes.json")
    monkeypatch.setattr(report_router, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(
        report_router, "ATTACH_DIR", tmp_path / "memory" / "attachments")
    return TestClient(main.app)


def test_report_member_endpoints_work_without_login_when_auth_enabled(
        tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    memory = tmp_path / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    filename = "auth_report_plan.json"
    (memory / filename).write_text(
        _plan().model_dump_json(indent=2), encoding="utf-8")

    import app.services.report_link as report_link
    token = report_link.create_report_token(filename, "张三")

    # 免登录可读状态
    st = client.get("/api/report/state", params={"token": token})
    assert st.status_code == 200
    assert st.json()["member"] == "张三"

    # 免登录可提交 JSON 更新
    up = client.post("/api/report/update", json={
        "token": token,
        "task_id": "T1",
        "status": "in_progress",
        "actual_hours": 2.0,
        "note": "鉴权回归",
    })
    assert up.status_code == 200
    assert up.json()["status"] == "in_progress"

    # 免登录可 multipart 上传：token 从原始字节提取（照片为写入型端点 → 200）
    photo = client.post(
        "/api/report/photo",
        data={"token": token, "task_id": "T1"},
        files={"file": ("photo.jpg", b"fake-jpeg-bytes", "image/jpeg")},
    )
    assert photo.status_code == 200

    # 免登录可 multipart 上传语音（解码失败返回 4xx，但不应是 401）
    voice = client.post(
        "/api/report/voice",
        data={"token": token, "task_id": "T1"},
        files={"file": ("voice.webm", b"not-real-audio", "audio/webm")},
    )
    assert voice.status_code != 401

    # 附件查看免登录
    attach = client.get(
        "/api/report/attachment",
        params={"token": token, "task_id": "T1"},
    )
    assert attach.status_code in (200, 404)  # 有照片则 200，无则 404，但非 401
    assert attach.status_code != 401

    # 无效令牌仍被鉴权拦截
    bad = client.get("/api/report/state", params={"token": "bogus-token"})
    assert bad.status_code == 401

    # 生成令牌的 /api/report/link 仍要求登录
    link = client.post(
        "/api/report/link",
        json={"filename": filename, "member": "张三"},
    )
    assert link.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-admin-token"},
    )
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    link2 = client.post(
        "/api/report/link",
        json={"filename": filename, "member": "张三"},
        headers=headers,
    )
    assert link2.status_code == 200
