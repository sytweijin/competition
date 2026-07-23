from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.coordinator import Coordinator
from app.file_analysis import analyze_locally
from app.main import app
from app.models.schemas import AgentError, AssignmentInput, CourseInfo, TeamMember
from app.services.project_service import generate_draft


def _input():
    return AssignmentInput(
        course=CourseInfo(name="社会实践推送", description="制作暑期社会实践总结秀米推送"),
        background="实践前定框架，实践中摄影记录，实践后完成文案、选图、排版、审核和发布。",
        requirements="需要秀米推送",
        members=[
            TeamMember(name="文案同学", skill_tags=["文案撰写"]),
            TeamMember(name="摄影同学", skill_tags=["摄影"]),
            TeamMember(name="排版同学", skill_tags=["秀米排版"]),
        ],
        deadline=date(2026, 8, 20),
        default_start_date=date(2026, 7, 20),
        default_end_date=date(2026, 8, 20),
    )


def test_draft_does_not_assign_and_has_professional_tasks(monkeypatch):
    coordinator = Coordinator()
    monkeypatch.setattr(coordinator, "_step_planner", lambda inp: coordinator._fallback_plan(inp))
    draft = coordinator.draft(_input())
    assert len(draft.tasks) == 10
    assert all(t.assignee_id is None for t in draft.tasks)
    assert {"文案", "摄影", "排版"}.issubset({t.category for t in draft.tasks})
    assert all(t.estimated_hours > 0 and t.execution_stage for t in draft.tasks)
    assert all(t.suggested_people >= 1 for t in draft.tasks)


def test_domain_fallback_uses_project_keywords():
    inp = AssignmentInput(
        course=CourseInfo(name="调研汇报", description="开展社会调研并完成报告和答辩PPT"),
        background="实践中访谈和收集资料，实践后分析数据、撰写总结并汇报。",
        members=[TeamMember(name="甲", skill_tags=["调研"])],
        deadline=date(2026, 8, 20),
    )
    plan = Coordinator._fallback_plan(inp, "timeout")
    names = {task.name for task in plan.tasks}
    assert "开展调研与资料采集" in names
    assert "撰写报告或总结正文" in names
    assert "制作演示文稿与视觉排版" in names
    assert len(plan.tasks) >= 7


def test_fast_draft_does_not_call_llm(monkeypatch):
    coordinator_called = {"value": False}

    def fail_if_called(*args, **kwargs):
        coordinator_called["value"] = True
        raise AssertionError("快速草案不应调用 LLM")

    monkeypatch.setattr(Coordinator, "draft", fail_if_called)
    plan = generate_draft(_input(), use_ai=False)
    assert not coordinator_called["value"]
    assert len(plan.tasks) == 10


def test_planner_receives_confirmed_and_file_requirements(monkeypatch):
    coordinator = Coordinator()
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return coordinator._fallback_plan(_input())

    monkeypatch.setattr(coordinator.planner, "run", fake_run)
    inp = _input().model_copy(update={
        "requirements": "用户确认：必须发布秀米推送",
        "requirement_analysis": {"summary": "文件要求：实践后提交"},
    })
    coordinator.draft(inp)
    assert "必须发布秀米推送" in captured["extra"]
    assert "实践后提交" in captured["extra"]
    assert "文件要求提炼" in captured["extra"]


def test_file_requirements_create_specific_fast_tasks():
    inp = AssignmentInput(
        course=CourseInfo(name="社区垃圾分类宣传", description="开展社区实践活动"),
        background="到社区开展现场实践",
        members=[TeamMember(name="甲", skill_tags=["宣讲"])],
        deadline=date(2026, 8, 20),
        uploaded_files=[{"name": "任务书.docx", "status": "ok"}],
        requirement_analysis={
            "core_tasks": ["任务：开展垃圾分类宣讲并回收居民签到表"],
            "deliverables": ["交付物：提交带日期水印的现场照片"],
            "constraints": ["照片不少于 12 张，统一 JPG 格式"],
        },
    )
    plan = Coordinator._fallback_plan(inp, "快速模式")
    names = {task.name for task in plan.tasks}
    assert "开展垃圾分类宣讲并回收居民签到表" in names
    assert "提交带日期水印的现场照片" in names
    assert "现场执行与过程协调" not in names
    file_task = next(task for task in plan.tasks if "签到表" in task.name)
    assert "依据文件要求" in file_task.description
    assert "数量、格式、时间和质量条件" in file_task.description
    photo_task = next(task for task in plan.tasks if "日期水印" in task.name)
    assert photo_task.execution_stage == "收尾"


def test_draft_editor_uses_handle_only_dragging_and_always_analyzes_files():
    html = Path("app/web/templates/index.html").read_text(encoding="utf-8")
    assert '<article class="task-edit-card" data-id="' in html
    draggable_card = """class="task-edit-card" data-id="'+esc(task.id)+'" draggable="""
    assert draggable_card not in html
    assert 'class="drag-handle" draggable="true"' in html
    assert "handle.ondragstart" in html
    assert "if(state.files.length)await analyzeFiles()" in html
    assert "state.files.length&&useAi!==true" not in html
    assert "AI 本次未返回可用草案，已改用文件任务蓝图" in html


def test_file_analysis_txt():
    client = TestClient(app)
    response = client.post(
        "/api/analyze-files",
        data={"background": "社会实践总结"},
        files={"files": ("requirements.txt", "目标：发布总结推送。截止：8月20日。".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["files"][0]["status"] == "ok"
    assert response.json()["analysis"]["time_requirements"]
    assert "发布总结推送" in response.json()["analysis"]["summary"]


def test_ideology_handbook_extracts_requirements_not_teaching_descriptions():
    text = """
    “思政实践”课程手册
    本课程设计了“4 阶段”教学环节，在纵向上循序递进。
    第 4 讲：调查研究方法与调研报告撰写方法。
    实践前围绕实践主题聚焦选题，制作《支队实践手册》，设计调研提纲。
    物资准备包括记录设备、队旗、队服等。
    调研时间不少于 4 天（32 学时），不含途中时间。
    出行期间举行 1 次支队理论讲座（2 学时），召开 3 次支队研讨会，
    每次研讨会需要有专人记录，形成文字版研讨记录。
    实践后形成 1 份支队调研报告，每个学生一人一份个人总结报告。
    支队调研报告不多于 10000 字，个人总结报告不多于 3000 字。
    建议实践后制作实践总结推送，鼓励用视频形式记录实践过程与成果。
    """
    analysis = analyze_locally(text)
    assert analysis["document_type"] == "思政实践课程手册"
    assert "本课程设计了“4 阶段”教学环节" not in analysis["core_tasks"]
    names = {item["name"] for item in analysis["task_blueprint"]}
    assert "确定调研主题与核心问题" in names
    assert "设计并定制队旗、队服等支队物资" in names
    assert "组织第 3 次支队研讨会并形成记录" in names
    assert "撰写实践总结推送文案（建议项）" in names
    assert "剪辑实践 Vlog 并完成审核（鼓励项）" in names
    assert analysis["required_deliverables"]
    assert analysis["recommended_deliverables"]


def test_handbook_blueprint_expands_reports_per_member_and_explains_fallback():
    analysis = analyze_locally("""
    思政实践课程手册：支队研讨 3 次。实践后提交 1 份支队调研报告，
    每个学生一人一份个人总结报告。物资包括队旗、队服。
    调研不少于 4 天。建议总结推送，鼓励视频。
    """)
    inp = _input().model_copy(update={"requirement_analysis": analysis})
    plan = Coordinator._fallback_plan(
        inp, "LLM 调用失败：Connection timeout")
    names = {task.name for task in plan.tasks}
    assert {
        "文案同学撰写个人总结报告",
        "摄影同学撰写个人总结报告",
        "排版同学撰写个人总结报告",
    }.issubset(names)
    assert "策划实践总结推送结构（建议项）" in names
    assert "AI 拆解本次未成功" in plan.summary
    assert "不代表文件解析失败" in plan.reasoning
    assert "连接或响应超时" in plan.reasoning


def test_constraints_are_attached_to_task_not_created_as_tasks():
    analysis = analyze_locally("""
    作业要求：实现文件加密和解密功能；
    制作命令行界面即可，不要求使用图形界面）
    """)
    assert analysis["core_tasks"] == ["实现文件加密和解密功能"]
    assert any("不要求使用图形界面" in item
               for item in analysis["constraints"])
    mapping = analysis["task_requirements"][0]
    assert mapping["task"] == "实现文件加密和解密功能"
    assert not any("即可" in task for task in analysis["core_tasks"])

    inp = AssignmentInput(
        course=CourseInfo(name="文件加密程序", description="完成编程作业"),
        members=[TeamMember(name="甲", skill_tags=["Python"])],
        deadline=date(2026, 8, 20),
        requirement_analysis=analysis,
    )
    plan = Coordinator._fallback_plan(inp, "快速模式")
    assert not any(
        "即可" in task.name or "图形界面" in task.name or "）" in task.name
        for task in plan.tasks)
    encryption = next(task for task in plan.tasks if "加密和解密" in task.name)
    assert "不要求使用图形界面" in encryption.description


def test_chat_can_read_draft_without_full_plan(monkeypatch):
    from app.llm.client import LLMClient
    monkeypatch.setattr(
        LLMClient, "chat_text",
        lambda *args, **kwargs: AgentError(
            agent="test", error_type="timeout", message="offline"))
    client = TestClient(app)
    response = client.post("/api/chat", json={
        "message": "当前有哪些任务？",
        "draft": {
            "tasks": [{
                "id": "T1", "name": "文案撰写", "estimated_hours": 5,
                "suggested_people": 1, "dependencies": [],
            }],
            "summary": "草案",
        },
    })
    assert response.status_code == 200
    assert "文案撰写" in response.json()["reply"]
