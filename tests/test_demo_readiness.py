from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.scoring import skill_score
from app.main import app
from app.models.schemas import TeamMember


def test_descriptive_and_cross_language_skill_aliases_match():
    writer = TeamMember(name="文案", skill_tags=["文学素养", "文案写作"])
    designer = TeamMember(name="设计", skill_tags=["PPT", "视觉设计"])

    assert skill_score(writer, ["报告撰写"]) >= 0.9
    assert skill_score(designer, ["幻灯片制作"]) >= 0.9


def test_demo_ui_has_full_showcase_flow():
    html = Path("app/web/templates/index.html").read_text(encoding="utf-8")
    css = Path("app/web/static/style.css").read_text(encoding="utf-8")
    js = Path("app/web/static/app.js").read_text(encoding="utf-8")

    assert 'id="demoCaseBtn"' in html
    assert 'id="exportMdBtn"' in html
    assert 'id="exportDocxBtn"' in html
    assert 'id="exportPdfBtn"' in html
    assert "function projectDays()" in js
    assert "daily*30" not in js
    assert "data-delete" in js
    assert "excel:'xlsx'" in js
    assert "Content-Disposition" in js
    assert "关键路径" in js and "紧急" in js and "浮动充足" in js
    assert ".gantt-track i.blocked" in css
    assert html.count('data-tab="') == 7
    assert 'data-tab="knowledge"' not in html
    assert 'data-tab="schedule"' in html
    assert 'data-tab="collaboration"' in html
    assert 'aria-label="打开 AI 调整建议"' in html
    assert 'aria-label="关闭 AI 调整建议"' in html
    assert 'class="assistant-button-label">AI 建议</span>' in html
    assert "function positionAssistantDrawer" in js
    assert "requestAnimationFrame(function()" in js
    assert "function fileStatusLabel" in js
    assert "function renderFileList" in js
    assert ".file-status.is-too_large" in css
    assert "var builtins=['项目负责人','骨干 / 模块负责人','执行成员']" in js
    assert "'志愿者 / 外部协作者'" not in js.split('function roleOptionsHtml', 1)[1].split('function isVolunteerRole', 1)[0]
    assert "查看全部 '+items.length+' 条信息" in js
    assert "setDefaultDates()" in js
    assert "function alertPanelHtml" in js
    assert "function alertMessages" in js
    assert "warnings.map(esc).join('；')" not in js
    assert "查看全部 '+items.length+' 条信息" in js
    assert "items.slice(1)" not in js
    assert ".compact-alert" in css
    assert "small-project-mode" in js
    assert "is-minimal-volunteer" in js
    assert "（志愿者）" in js
    assert ".small-project-mode .member-row .member-manager" in css
    assert 'id="defenseTabBtn"' in html
    assert "function shouldShowDefense" in js
    assert "function renderDefensePanel" in js
    assert "/api/interview/materials" in js
    assert "material_text:ivChat.materialText" in js
    assert ".defense-materials" in css


def test_demo_main_flow_and_three_exports():
    client = TestClient(app)
    project_input = {
        "course": {
            "name": "校园低碳生活倡议发布",
            "description": "完成调研、内容策划、视觉物料、活动和复盘报告",
        },
        "background": "面向全校同学策划低碳生活倡议活动",
        "requirements": "交付调研摘要、宣传图文和复盘报告",
        "members": [
            {"name": "林悦", "skill_tags": ["调研", "数据分析"]},
            {"name": "陈曦", "skill_tags": ["文案写作", "报告撰写"]},
            {"name": "周航", "skill_tags": ["视觉设计", "PPT", "摄影"]},
        ],
        "deadline": "2026-08-20",
        "default_start_date": "2026-08-05",
        "default_end_date": "2026-08-20",
    }
    draft_response = client.post(
        "/api/draft", json={"input": project_input, "use_ai": False})
    assert draft_response.status_code == 200
    draft = draft_response.json()["plan"]
    assert len(draft["tasks"]) >= 5

    confirm_response = client.post(
        "/api/confirm-draft", json={"input": project_input, "plan": draft})
    assert confirm_response.status_code == 200
    plan = confirm_response.json()
    assert plan["timeline"]["tasks"]
    assert all(task.get("assignee_id") for task in plan["plan"]["tasks"])

    assignees = {
        task["id"]: task["assignee_id"] for task in plan["plan"]["tasks"]}
    collaborators = {
        task["id"]: task.get("collaborator_ids", [])
        for task in plan["plan"]["tasks"]
    }
    manual_response = client.post("/api/manual-assignment", json={
        "plan": plan, "assignees": assignees, "collaborators": collaborators,
    })
    assert manual_response.status_code == 200
    final_plan = manual_response.json()

    for format_name in ("markdown", "docx", "pdf"):
        response = client.post(f"/api/export/{format_name}", json=final_plan)
        assert response.status_code == 200
        assert response.content

    excel_response = client.post("/api/export/excel", json=final_plan)
    assert excel_response.status_code == 200
    assert "plan_export.xlsx" in excel_response.headers["content-disposition"]
    assert excel_response.content.startswith(b"PK")


def test_golden_demo_and_three_runbooks_exist():
    root = Path(__file__).resolve().parents[1]
    golden = (root / "docs" / "项目说明书.md").read_text(encoding="utf-8")
    assert "拍照立项" in golden
    assert "语音或照片汇报" in golden
    assert "群通知" in golden
    for name in (
        "复现文档.md",
        "功能验证清单.md",
        "部署与回退清单.md",
    ):
        assert (root / "docs" / name).exists()
