"""API 集成测试。

覆盖核心业务端点（不依赖 LLM 的路径）。
"""
from httpx import AsyncClient, ASGITransport
import pytest

from app.main import app
import app.web.routes as web_routes


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
    assert set(resp.json()["checks"]) == {
        "storage", "llm_configured", "vision_model_configured",
        "asr_model_configured", "realtime_configured",
        "realtime_backend",
    }


@pytest.mark.asyncio
async def test_ready_ok_with_local_storage(client, monkeypatch):
    """local 存储后端下 /api/ready 应判定存储可用（200），不再恒 503。"""
    import app.config as config
    import app.web.routers.system as system

    monkeypatch.setattr(config, "APP_MODEL_MODE", "minicpm")
    monkeypatch.setattr(system, "APP_MODEL_MODE", "minicpm")
    monkeypatch.setattr(system, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(system, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(system, "STORAGE_BACKEND", "local")
    resp = await client.get("/api/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["storage_backend"] == "local"
    assert data["checks"]["storage_ok"] is True


@pytest.mark.asyncio
async def test_request_metrics_endpoint_tracks_requests(client):
    await client.get("/api/health")
    resp = await client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["errors"] >= 0
    assert "by_status" in data
    assert "by_path" in data
    assert "/api/health" in data["by_path"]


@pytest.mark.asyncio
async def test_analyze_files_returns_per_file_statuses(client, monkeypatch):
    import app.file_analysis as file_analysis

    monkeypatch.setattr(file_analysis, "MAX_FILE_SIZE", 1024)
    response = await client.post(
        "/api/analyze-files",
        files=[
            ("files", ("要求.txt", "项目：校园调研报告", "text/plain")),
            ("files", ("损坏.pdf", b"not-a-pdf", "application/pdf")),
            ("files", ("超大.txt", b"a" * 2048, "text/plain")),
            ("files", ("录音.mp3", b"fake-audio", "audio/mpeg")),
        ],
        data={"background": ""},
    )

    assert response.status_code == 200
    payload = response.json()
    by_name = {
        item["name"]: item
        for item in payload["files"] + payload["errors"]
    }
    assert by_name["要求.txt"]["status"] == "ok"
    assert by_name["损坏.pdf"]["status"] == "unreadable"
    assert by_name["超大.txt"]["status"] == "too_large"
    assert by_name["录音.mp3"]["status"] == "needs_confirmation"


@pytest.mark.asyncio
async def test_report_is_generated_only_on_explicit_request(client, monkeypatch):
    from app.agents.reporter import ReporterAgent
    from app.models.schemas import ReportOutput

    calls = {"count": 0}

    def fake_report(self, **kwargs):
        calls["count"] += 1
        return ReportOutput(summary="按需报告")

    monkeypatch.setattr(ReporterAgent, "run", fake_report)
    payload = _minimal_full_plan()
    response = await client.post("/api/report", json=payload)
    assert response.status_code == 200
    assert response.json()["report"]["summary"] == "按需报告"
    assert calls["count"] == 1

    # 已生成报告再次请求时直接复用，不重复调用模型。
    response2 = await client.post("/api/report", json=response.json())
    assert response2.status_code == 200
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_llm_performance_endpoint_has_no_sensitive_payload(client):
    response = await client.get("/api/performance/llm")
    assert response.status_code == 200
    data = response.json()
    assert "stages" in data
    assert "prompt" not in str(data).lower()


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


def _large_plan_payload():
    """大型项目模式的最小 FullPlan：T1 需招募 2 名志愿者。"""
    payload = _minimal_full_plan()
    payload["input"]["project_mode"] = "large_project"
    payload["plan"]["tasks"][0]["extra_helpers_needed"] = 2
    payload["plan"]["tasks"][0]["suggested_people"] = 3
    return payload


@pytest.mark.asyncio
async def test_volunteers_endpoint_saves_pool(client):
    payload = _large_plan_payload()
    resp = await client.post("/api/volunteers", json={
        "plan": payload,
        "volunteers": [
            {"name": "小王", "task_id": "T1", "status": "待确认", "contact": "wx"},
            {"name": "小李", "task_id": "T1", "status": "已确认", "note": "可周末"},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["volunteer_pool"]) == 2
    assert data["volunteer_pool"][0]["name"] == "小王"
    assert data["volunteer_pool"][1]["status"] == "已确认"


@pytest.mark.asyncio
async def test_volunteers_endpoint_rejects_overflow(client):
    payload = _large_plan_payload()
    payload["plan"]["tasks"][0]["extra_helpers_needed"] = 1
    resp = await client.post("/api/volunteers", json={
        "plan": payload,
        "volunteers": [
            {"name": "小王", "task_id": "T1", "status": "已确认"},
            {"name": "小李", "task_id": "T1", "status": "已确认"},
        ],
    })
    assert resp.status_code == 400
    assert "超过需求" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_volunteers_endpoint_rejects_small_group(client):
    resp = await client.post("/api/volunteers", json={
        "plan": _minimal_full_plan(),
        "volunteers": [{"name": "小王", "task_id": "T1"}],
    })
    assert resp.status_code == 400
    assert "仅适用于大型项目模式" in resp.json()["detail"]


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
async def test_index_html_not_cached(client):
    """index.html 必须带 no-cache，避免浏览器缓存旧页面导致前端修复不生效。"""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_draft_ai_mode_without_key_still_returns_plan(client, monkeypatch):
    """use_ai=true 但无 API key 时自动降级为兜底，不抛 500。"""
    import app.llm.client as llm_client

    monkeypatch.setattr(llm_client, "LLM_API_KEY", "")
    monkeypatch.setattr(
        llm_client.LLMClient, "get_shared", lambda: llm_client.LLMClient())
    payload = {"input": _sample_course_input(), "use_ai": True}
    resp = await client.post("/api/draft", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert len(data["plan"]["tasks"]) > 0
    assert data["warnings"], "兜底草案应带 warnings 提示"
    assert any("兜底" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_draft_large_fallback_has_warnings(client, monkeypatch):
    """大型项目 LLM 失败降级为确定性兜底时，响应带 warnings 提示。"""
    import app.llm.client as llm_client

    monkeypatch.setattr(llm_client, "LLM_API_KEY", "")
    monkeypatch.setattr(
        llm_client.LLMClient, "get_shared", lambda: llm_client.LLMClient())
    inp = _sample_course_input()
    inp["project_mode"] = "large_project"
    payload = {"input": inp, "use_ai": True}
    resp = await client.post("/api/draft", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"]
    assert any("兜底" in w for w in data["warnings"])


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


@pytest.mark.asyncio
async def test_confirm_draft_skips_second_ai_reflection(client, monkeypatch):
    """交互式确认分工不应再等待第二次 AI 复盘。"""
    captured = {}

    def fake_confirm(inp, plan, *, use_ai_reflection=True):
        captured["use_ai_reflection"] = use_ai_reflection
        raise web_routes.ProjectServiceError("captured")

    monkeypatch.setattr(web_routes, "confirm_draft_service", fake_confirm)
    draft_resp = await client.post(
        "/api/draft", json={"input": _sample_course_input(), "use_ai": False})
    resp = await client.post("/api/confirm-draft", json={
        "input": _sample_course_input(), "plan": draft_resp.json()["plan"]})

    assert resp.status_code == 400
    assert captured["use_ai_reflection"] is False


# ──────────── Workload ────────────


@pytest.mark.asyncio
async def test_workload_returns_snapshot(client):
    """工作量快照应正确统计各成员工时。"""
    draft_resp = await client.post("/api/draft", json={
        "input": _sample_course_input(), "use_ai": False
    })
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": draft_resp.json()["plan"],
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
    draft_resp = await client.post("/api/draft", json={
        "input": _sample_course_input(), "use_ai": False
    })
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": draft_resp.json()["plan"],
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
    draft_resp = await client.post("/api/draft", json={
        "input": _sample_course_input(), "use_ai": False
    })
    confirm_payload = {
        "input": _sample_course_input(),
        "plan": draft_resp.json()["plan"],
    }
    plan_resp = await client.post("/api/confirm-draft", json=confirm_payload)
    full_plan = plan_resp.json()

    resp = await client.post("/api/export/markdown", json=full_plan)
    assert resp.status_code == 200
    assert len(resp.text) > 50


def test_download_disposition_encodes_chinese_filename():
    """中文文件名导出：Content-Disposition 必须兼容 latin-1（RFC 5987）。"""
    header = web_routes._download_disposition("审计测试方案.json.md")
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header
    assert "%E5%AE%A1%E8%AE%A1" in header  # “审计” 的 UTF-8 百分号编码
    header.encode("latin-1")  # 可被 HTTP 头编码，不再触发 500
