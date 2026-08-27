"""
FastAPI 路由（A5：简易只读 Web + B2 Memory + B4 动态编辑）
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.config import MEMORY_DIR
from app.coordinator import Coordinator
from app.editor import EditError, edit_plan
from app.agents.interview_sim import InterviewSimAgent
from app.models.schemas import (
    AssignmentInput, CourseInfo, EditPlanRequest, FullPlan, PlanOutput, QAOutput, TeamMember,
    DraftRequest, DraftResponse, ConfirmDraftRequest, ManualAssignmentRequest,
    RequirementAnalysis, DraftMutationRequest, Volunteer,
)
from app.services.project_service import (
    ProjectServiceError, apply_manual_assignment, confirm_draft as confirm_draft_service,
    generate_draft, mutate_draft, record_task_actual, resource_calendar,
    recompute_plan as recompute_plan_service,
    update_volunteer_pool, update_task_participants, workload_snapshot,
)
from app.services.plan_io import (
    parse_task_file,
)
from app.services.audit_store import (
    compare_versions, list_version_tree, list_versions, rollback_plan, save_version,
)
from app.services.auth_store import (
    accessible_filenames, add_editor, auth_enabled, can_read, can_write,
    get_acl, set_acl,
)
from app.services.collab import (
    knowledge_search, org_review, reminders, save_experience,
)
from app.services.knowledge_agent import ask as agent_ask_service
from app.services.notifier import notify_reminders
from app.services.share_store import (
    create_share, get_share_entry, share_status,
)
from app.web.routers.exports import router as export_router
from app.web.routers.members import router as member_router
from app.web.routers.realtime import router as realtime_router
from app.web.routers.report import router as report_router
from app.web.routers.system import router as system_router

logger = logging.getLogger(__name__)
router = APIRouter()

# 文件需求分析的进程内缓存：同一内容（按 sha1）只做一次真实的
# 图片理解 / 音频转写 / PDF 提取。此前每新增一个语音/拍照需求，
# 前端都会把全部文件重新上传并重新识别一遍，旧文件白白再跑一轮模型。
_FILE_TEXT_CACHE: dict[str, str] = {}
_FILE_TEXT_CACHE_LOCK = threading.Lock()
_FILE_TEXT_CACHE_MAX = 200


def _cached_extract_text(filename: str, raw: bytes) -> str:
    """提取文件文本，命中内容哈希缓存时直接复用，不重复调用视觉/语音模型。"""
    key = hashlib.sha1(raw).hexdigest() + "|" + filename
    with _FILE_TEXT_CACHE_LOCK:
        cached = _FILE_TEXT_CACHE.get(key)
        if cached is not None:
            return cached
    from app.file_analysis import extract_text

    text = extract_text(filename, raw)
    with _FILE_TEXT_CACHE_LOCK:
        if len(_FILE_TEXT_CACHE) >= _FILE_TEXT_CACHE_MAX:
            # 简单淘汰：缓存超限时整体清空，避免内存无界增长
            _FILE_TEXT_CACHE.clear()
        _FILE_TEXT_CACHE[key] = text
    return text

# 聚合按业务域拆分的子路由；保留本模块作为外部兼容入口。
router.include_router(system_router)
router.include_router(export_router)
router.include_router(member_router)
router.include_router(realtime_router)
router.include_router(report_router)


def _file_status(text: str) -> str:
    """按提取结果给文件一个明确的用户可读状态。"""
    if text.startswith("[图片文件]") or text.startswith("[音频文件]"):
        return "needs_confirmation"
    return "ok"


@router.post("/analyze-files")
async def analyze_files(files: list[UploadFile] = File(...), background: str = Form("")):
    """提取文件文字后汇总分析；仅记录文件元数据，不落盘、不输出原文日志。"""
    from app.file_analysis import MAX_FILE_SIZE, analyze_locally
    texts, metadata, errors = [], [], []
    for upload in files[:8]:
        raw = await upload.read(MAX_FILE_SIZE + 1)
        if len(raw) > MAX_FILE_SIZE:
            errors.append({
                "name": upload.filename,
                "status": "too_large",
                "error": "文件超过 15MB 限制",
            })
            continue
        try:
            text = await asyncio.to_thread(
                _cached_extract_text, upload.filename or "upload", raw)
            texts.append(text)
            metadata.append({
                "name": upload.filename,
                "size": len(raw),
                "status": _file_status(text),
            })
        except ValueError as exc:
            errors.append({
                "name": upload.filename,
                "status": "unreadable",
                "error": str(exc),
            })
    if not texts:
        raise HTTPException(status_code=400, detail=errors[0]["error"] if errors else "没有可分析文件")
    merged = (background + "\n" + "\n".join(texts))[:60000]
    # 文件阶段只做本地提取、清理和事实归类；随后 Planner 只调用一次 LLM。
    # 避免“文件分析 LLM + 任务拆解 LLM”串行造成一分钟以上等待。
    analysis = analyze_locally(merged)
    return {"files": metadata, "errors": errors, "analysis": analysis}


@router.post("/interview/materials")
async def analyze_interview_materials(files: list[UploadFile] = File(...)):
    """临时提取答辩稿/PPT文字，仅返回本次模拟使用，不写入项目记录。"""
    from app.file_analysis import MAX_FILE_SIZE, extract_text

    allowed = {".txt", ".md", ".pdf", ".docx", ".pptx"}
    texts, metadata, errors = [], [], []
    for upload in files[:4]:
        filename = upload.filename or "答辩材料"
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed:
            errors.append({
                "name": filename,
                "status": "unreadable",
                "error": "仅支持 PPTX、PDF、Word、TXT、Markdown",
            })
            continue
        raw = await upload.read(MAX_FILE_SIZE + 1)
        if len(raw) > MAX_FILE_SIZE:
            errors.append({
                "name": filename,
                "status": "too_large",
                "error": "文件超过 15MB 限制",
            })
            continue
        try:
            text = await asyncio.to_thread(extract_text, filename, raw)
            texts.append(f"【{filename}】\n{text}")
            metadata.append({
                "name": filename,
                "size": len(raw),
                "status": _file_status(text),
            })
        except ValueError as exc:
            errors.append({
                "name": filename,
                "status": "unreadable",
                "error": str(exc),
            })
    if not texts:
        raise HTTPException(
            status_code=400,
            detail=errors[0]["error"] if errors else "没有可分析的答辩材料",
        )
    return {
        "files": metadata,
        "errors": errors,
        "material_text": "\n\n".join(texts)[:50000],
    }


@router.post("/import/tasks")
async def import_tasks(
    file: UploadFile = File(...),
    project_mode: str = Form("small_group"),
):
    """从 CSV/Excel 导入任务草稿。"""
    content = await file.read()
    try:
        plan = parse_task_file(content, file.filename or "", project_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"plan": plan}


@router.post("/draft", response_model=DraftResponse)
def create_draft(req: DraftRequest):
    """只生成可编辑任务草案，不分工。"""
    plan = generate_draft(req.input, use_ai=req.use_ai)
    warnings: list[str] = []
    if req.use_ai:
        reasoning = plan.reasoning or ""
        is_fallback = (
            "LLM 规划失败" in reasoning
            or "LLM 不可用" in reasoning
            or "AI 拆解本次未成功" in reasoning
            or "兜底" in (plan.summary or "")
        )
    else:
        is_fallback = False
    if is_fallback:
        warnings.append(
            "AI 本次未返回可用草案，已改用确定性兜底蓝图；"
            "可重新生成，或直接在草稿页手动编辑"
        )
    return DraftResponse(input=req.input, plan=plan, warnings=warnings)


@router.post("/confirm-draft", response_model=FullPlan)
def confirm_draft(req: ConfirmDraftRequest):
    """确认拆解后使用确定性规则快速完成分工与风险检查。"""
    try:
        return confirm_draft_service(
            req.input, req.plan, use_ai_reflection=False)
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/draft/mutate", response_model=PlanOutput)
def mutate_draft_endpoint(req: DraftMutationRequest):
    """结构化修改草案，供网页与未来自然语言 Agent 共用。"""
    try:
        return mutate_draft(req.plan, req.operations)
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/manual-assignment", response_model=FullPlan)
def manual_assignment(req: ManualAssignmentRequest):
    """保存用户拖拽后的负责人/协作者并重算排期与报告。"""
    try:
        return apply_manual_assignment(req)
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/workload")
def workload(req: FullPlan):
    """返回统一工作量统计与建议，不让页面自行复制业务规则。"""
    return workload_snapshot(req)


@router.post("/resource-calendar")
def calendar(req: FullPlan):
    """Return resource calendar with daily load and conflict warnings."""
    return resource_calendar(req)


class VolunteerPoolRequest(BaseModel):
    plan: FullPlan
    volunteers: list[Volunteer] = Field(default_factory=list)


@router.post("/volunteers", response_model=FullPlan)
def update_volunteers(req: VolunteerPoolRequest):
    """保存大型项目模式的志愿者招募池（整池替换式 upsert）。"""
    try:
        return update_volunteer_pool(req.plan, req.volunteers)
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class TaskActualRequest(BaseModel):
    plan: FullPlan
    task_id: str
    actual_hours: float | None = None
    actual_end_date: date | None = None


@router.post("/task-actual", response_model=FullPlan)
def task_actual(req: TaskActualRequest):
    """Record actual hours/end date and persist review feedback."""
    try:
        return record_task_actual(
            req.plan,
            req.task_id,
            actual_hours=req.actual_hours,
            actual_end_date=req.actual_end_date,
        )
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class TaskParticipantsRequest(BaseModel):
    plan: FullPlan
    task_id: str
    participants: list[dict] = Field(default_factory=list)


@router.post("/task-participants", response_model=FullPlan)
def task_participants(req: TaskParticipantsRequest):
    """保存任务级参与清单，并同步负责人/协作者/志愿者数量。"""
    try:
        return update_task_participants(
            req.plan, req.task_id, req.participants)
    except ProjectServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ChatMessage(BaseModel):
    """单条对话消息（多轮记忆用）。"""
    role: str = Field(..., description="user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    message: str
    plan: FullPlan | None = None
    draft: PlanOutput | None = None
    input: AssignmentInput | None = None
    # 多轮记忆：前端维护的对话历史（最近 N 轮），每条 {role, content}
    history: list[ChatMessage] | None = None
    # 方案增量变更描述（自然语言），自上次 baseline 后累积的调整
    delta: str | None = None


def _build_chat_context(req: "ChatRequest") -> str:
    """构建结构化方案摘要，避免截断 JSON 导致 LLM 读到残缺数据。"""
    lines: list[str] = []
    if req.plan:
        fp = req.plan
        course = fp.input.course
        lines.append(f"项目：{course.name}")
        lines.append(f"背景：{course.description[:200]}")
        lines.append(f"截止日期：{fp.input.deadline}")
        members = fp.input.members
        lines.append("团队成员：" + "、".join(
            f"{m.name}(技能:{','.join(m.skill_tags) or '无'}，{m.daily_available_hours:g}h/天)"
            for m in members))
        if fp.input.project_mode == "large_project" and fp.plan.modules:
            lines.append(f"模块共 {len(fp.plan.modules)} 个：")
            for module in fp.plan.modules:
                module_tasks = [t for t in fp.plan.tasks
                                if t.module_id == module.id]
                owner = module.assignee_id or "未认领"
                lines.append(
                    f"  {module.id} {module.name}（负责人：{owner}，"
                    f"子任务 {len(module_tasks)} 项）")
        lines.append(f"任务共 {len(fp.plan.tasks)} 项，总工时 "
                     f"{sum(t.estimated_hours for t in fp.plan.tasks):g}h")
        for t in fp.plan.tasks:
            status = f"[{t.status}]" if t.status != "pending" else ""
            assignee = t.assignee_id or "未分配"
            lines.append(
                f"  {t.id} {t.name}"
                f"({'模块' + t.module_id + ' ' if t.module_id else ''}"
                f"{t.estimated_hours:g}h，{assignee}，"
                f"需:{','.join(t.required_skills) or '通用'}){status}")
        if fp.timeline.tasks:
            cp = " -> ".join(fp.timeline.critical_path) if fp.timeline.critical_path else "无"
            lines.append(f"总工期 {fp.timeline.total_days} 天，关键路径：{cp}")
        if fp.qa_matrix.assignments:
            lines.append("分工：")
            for a in fp.qa_matrix.assignments:
                support = "、".join([a.qa_primary] + (a.qa_support or []))
                lines.append(f"  {a.task_name}：{a.presenter or '未分配'}"
                             f"{'/' + support if support else ''}（匹配{int((a.score or 0) * 100)}%）")
        if fp.report.risk_note:
            lines.append(f"风险提示：{fp.report.risk_note[:200]}")
        return "\n".join(lines)
    if req.draft:
        tasks = req.draft.tasks
        if req.input and req.input.project_mode == "large_project" \
                and req.draft.modules:
            lines.append(f"模块共 {len(req.draft.modules)} 个：")
            for module in req.draft.modules:
                module_tasks = [t for t in tasks if t.module_id == module.id]
                owner = module.assignee_id or "未认领"
                lines.append(
                    f"  {module.id} {module.name}（负责人：{owner}，"
                    f"子任务 {len(module_tasks)} 项）")
        lines.append(f"任务草案共 {len(tasks)} 项：")
        for t in tasks:
            lines.append(
                f"  {t.id} {t.name}"
                f"({'模块' + t.module_id + ' ' if t.module_id else ''}"
                f"{t.estimated_hours:g}h)")
        if req.input:
            lines.append(f"项目：{req.input.course.name}")
            lines.append("成员：" + "、".join(m.name for m in req.input.members))
        return "\n".join(lines)
    return "尚未生成方案"


@router.post("/chat")
async def project_chat(req: ChatRequest):
    """基于当前方案回答用户的自然语言提问，支持多轮记忆。

    记忆策略（前端配合）：
    - 打开抽屉时前端做一次完整方案快照 baseline，作为首轮 user 消息喂给 LLM。
    - 后续对话只传 history（最近 N 轮）+ delta（方案增量变更描述）+ message。
    - 重新生成方案时前端清空 history 并重建 baseline。
    """
    import asyncio
    from app.llm.client import LLMClient
    context = _build_chat_context(req)
    system_prompt = (
        "你是协作分工助手，帮助团队做任务拆解、分工和排期。"
        "当用户问你是谁时，介绍自己是协作分工助手，绝不要介绍背后的模型、厂商或技术。\n\n"
        "你是项目协作助手，像一个懂项目管理的同事，陪用户一起看当前的任务分工，自然、口语地聊天。\n\n"
        "【仅供你判断，绝不写进回答】\n"
        "- 当前分工综合考虑了多个因素，不只是技能标签是否对口：相关能力、各阶段负载是否均匀、成员可用工时、任务之间的串行依赖都参与了权衡。所以某项任务看起来技能标签不完全匹配，不代表分配有问题——可能那个阶段他工时充裕，或整体能力足以覆盖。\n"
        "- 你看不到完整的负载与产能数据，所以你的定位是帮用户看清现状、指出值得留意的地方，而不是替用户拍板‘谁该做什么’。\n"
        "- 判断成员是否适合某项任务，要看他完整的能力描述，别只逐个比对技能标签——比如‘文学素养’‘沟通协调’‘擅长规划’这类综合能力，对应到文字撰写、沟通、组织类任务都是合理的。\n\n"
        "【你该聊什么】\n"
        "- 整体观察最有用：工时分布是否均衡、关键路径上哪些任务串在一起、哪里有跨人交接、截止日期留没留余地。\n"
        "- 用户问某项安排，可以客观说说特点（成员能力和任务的契合点、可能的压力，比如他连续承接多个任务），把判断权留给用户。\n"
        "- 用户想自己调整就支持他：看板可以拖拽任务卡换负责人，也能一键‘恢复自动分工’。这是介绍功能，不是提醒或劝阻。\n\n"
        "【绝对不要出现在回答里】\n"
        "- ‘不建议你手动重新分工’‘请不要自己重新分工’‘否则会打破负载均衡’这类劝阻或警告的话——用户当然可以自己手动调整。\n"
        "- ‘多因子算法’‘负载均衡’‘阶段负载’‘剩余产能’这些内部术语——它们只是你判断的依据，不要向用户解释。\n"
        "- ‘谁应该接哪个任务’的重新分配清单——你手头没有完整的产能与依赖数据，自己另排反而容易失衡，所以只做分析、不做拍板。\n\n"
        "【关于方案变更】\n"
        "- 对话期间用户可能拖拽调整了任务负责人或工时，这些变更会以‘方案变更’的形式告诉你。你回答时应基于最新方案，若用户问的与变更相关，要体现出你知道变更内容。\n"
        "- 若用户重新生成了方案，对话会重新开始，你不需要记得之前的方案。"
    )

    # 构造多轮 messages：baseline 作为首轮 user，后续 history，最后是本轮 message
    messages: list[dict] = []
    # 首轮：把完整方案快照作为 baseline 注入
    baseline_text = f"【当前方案快照】\n{context}"
    if req.delta:
        baseline_text += f"\n\n【方案变更】\n{req.delta}"
    messages.append({"role": "user", "content": baseline_text})
    # 确认收到 baseline，让 LLM 进入对话状态
    messages.append({"role": "assistant", "content": "好的，我已了解当前方案。你想让我重点看哪方面？工时分布、关键路径、还是某个成员的任务安排？"})
    # 追加历史对话（跳过 baseline 那轮，因为它已注入）
    if req.history:
        for msg in req.history:
            messages.append({"role": msg.role, "content": msg.content})
    # 本轮用户消息
    messages.append({"role": "user", "content": req.message})

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                LLMClient.get_shared().chat_messages,
                system_prompt,
                messages,
                0.2,
            ),
            timeout=40,
        )
    except TimeoutError:
        return {"reply": "AI 响应超过 40 秒。建议先在任务拆解或分工看板中直接调整；我已停止本次等待。"}
    if hasattr(result, "error_type"):
        tasks = req.plan.plan.tasks if req.plan else (req.draft.tasks if req.draft else [])
        total = sum(task.estimated_hours for task in tasks)
        if not req.plan and not req.draft:
            return {"reply": (
                "当前模型服务暂时不可用，且尚未生成方案。"
                "请先在首页填写项目信息并生成任务草案，"
                "模型恢复后我就能基于同一份方案回答你的问题。"
            )}
        preview = "；".join(
            f"{task.name}（{task.estimated_hours:g}h，建议{task.suggested_people}人）"
            for task in tasks[:8])
        return {"reply": (
            f"当前模型服务不可用，但我已读取到当前{'最终方案' if req.plan else '任务草案'}："
            f"共 {len(tasks)} 项、预计 {total:g} 小时。{preview or '暂无任务'}。"
            "你可以继续告诉我希望重点检查工时、人数、依赖还是负责人；模型恢复后会基于同一份方案回答。"
        )}
    return {"reply": result}


def _safe_filepath(filename: str) -> Path:
    """构造安全的 memory 目录文件路径，防止路径穿越。"""
    # 只取文件名部分，去掉任何目录分隔符
    safe = Path(filename).name
    if not safe or safe in (".", "..") or not safe.endswith(".json"):
        raise HTTPException(status_code=400, detail="非法文件名")
    fp = (MEMORY_DIR / safe).resolve()
    # 确保解析后的路径仍在 MEMORY_DIR 内
    if not str(fp).startswith(str(MEMORY_DIR.resolve())):
        raise HTTPException(status_code=400, detail="非法文件名")
    return fp


def _download_disposition(name: str) -> str:
    """构造兼容中文文件名的 Content-Disposition（RFC 5987 filename*）。

    HTTP 头只支持 latin-1，中文文件名直接放 filename 会触发
    UnicodeEncodeError 导致 500；用 filename*=UTF-8'' 百分号编码即可。
    """
    ascii_fallback = (
        name.encode("ascii", "replace").decode("ascii") or "download")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(name)}"
    )


class RunRequest(BaseModel):
    course: CourseInfo
    members: list[TeamMember]
    deadline: str
    additional_requirements: str = ""
    background: str = ""
    requirements: str = ""
    uploaded_files: list[dict] = Field(default_factory=list)
    requirement_analysis: dict = Field(default_factory=dict)
    default_start_date: str | None = None
    default_end_date: str | None = None
    project_mode: str = "small_group"


@router.post("/run", response_model=FullPlan)
def run_plan(req: RunRequest):
    """执行完整的 Agent 链路并返回结果。"""
    try:
        # 校验：至少 1 个有姓名的成员（P1-16）
        valid_members = [m for m in req.members if m.name.strip()]
        if not valid_members and req.project_mode != "large_project":
            raise HTTPException(status_code=400, detail="至少需要 1 名有姓名的团队成员")
        inp = AssignmentInput(
            course=req.course,
            members=valid_members,
            deadline=date.fromisoformat(req.deadline),
            additional_requirements=req.additional_requirements,
            background=req.background,
            requirements=req.requirements,
            uploaded_files=req.uploaded_files,
            requirement_analysis=req.requirement_analysis,
            default_start_date=date.fromisoformat(req.default_start_date) if req.default_start_date else None,
            default_end_date=date.fromisoformat(req.default_end_date) if req.default_end_date else None,
            project_mode=req.project_mode,
        )
        coordinator = Coordinator()
        return coordinator.run(inp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ──────────── B4：动态编辑 ────────────

@router.post("/edit", response_model=FullPlan)
def edit_plan_endpoint(req: EditPlanRequest):
    """对已有计划应用编辑（add/remove/update）并重算。"""
    try:
        return edit_plan(req)
    except EditError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ──────────── B2：Memory ────────────

@router.post("/save")
async def save_plan(
    request: Request,
    plan: FullPlan,
    filename: str = "",
    base_version: str = "",
):
    """保存计划到 memory 目录。"""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    username = getattr(request.state, "username", None)
    existing = False
    if filename:
        filename = _safe_filepath(filename).name
        filepath = MEMORY_DIR / filename
        existing = filepath.exists()
        if auth_enabled() and existing and not can_write(username, filename):
            raise HTTPException(status_code=403, detail="无权编辑该方案")
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_name = plan.input.course.name or "plan"
        course_name = re.sub(r'[^\w\u4e00-\u9fff._-]', "_", raw_name).strip("_") or "plan"
        filename = f"{ts}_{course_name}.json"
        filepath = MEMORY_DIR / filename
        n = 1
        while filepath.exists():
            filename = f"{ts}_{course_name}_{n}.json"
            filepath = MEMORY_DIR / filename
            n += 1
    if filename and base_version:
        versions = list_versions(filename)
        if versions and versions[0]["version_id"] != base_version:
            raise HTTPException(
                status_code=409,
                detail="方案已被其他人更新，请先载入最新版本再保存",
            )
    filepath.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    if username:
        if existing:
            add_editor(filename, username)
        else:
            set_acl(filename, owner=username)
    version_id = save_version(
        json.loads(filepath.read_text(encoding="utf-8")),
        filename,
        action="保存",
        summary="保存方案",
        parent_version_id=base_version or None,
        parent_filename=filename if base_version else None,
    )
    try:
        save_experience(plan)
    except Exception:
        pass
    logger.info("Plan saved to %s", filepath)
    return {"status": "ok", "filename": filename, "version_id": version_id}


@router.get("/plans")
async def list_plans(request: Request, q: str = ""):
    """List saved plans with optional search filter."""
    files = sorted(MEMORY_DIR.glob("*.json"), reverse=True)
    username = getattr(request.state, "username", None)
    if auth_enabled() and username != "admin":
        allowed = accessible_filenames(username)
        files = [f for f in files if f.name in allowed]
    plans = []
    for f in files:
        if q and q.lower() not in f.name.lower():
            continue
        plans.append({"filename": f.name, "size": f.stat().st_size})
    return {"plans": plans}


@router.get("/plan-history/{filename}")
async def plan_history(request: Request, filename: str):
    """返回某个方案及相似任务方案的版本树。"""
    filepath = _safe_filepath(filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    username = getattr(request.state, "username", None)
    if auth_enabled() and not can_read(username, filename):
        raise HTTPException(status_code=403, detail="无权查看该方案")
    allowed = None
    if auth_enabled() and username != "admin":
        allowed = accessible_filenames(username)
    tree = list_version_tree(filename, allowed_filenames=allowed)
    # versions 保留旧接口字段，供旧前端和第三方调用继续使用。
    tree["versions"] = list_versions(filename)
    return tree


@router.get("/plan-compare")
async def plan_compare(
    request: Request,
    left_filename: str,
    left_version: str,
    right_filename: str,
    right_version: str,
):
    """比较同一版本树中任意两个版本。"""
    left_path = _safe_filepath(left_filename)
    right_path = _safe_filepath(right_filename)
    if not left_path.exists() or not right_path.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    username = getattr(request.state, "username", None)
    if auth_enabled() and (
        not can_read(username, left_filename)
        or not can_read(username, right_filename)
    ):
        raise HTTPException(status_code=403, detail="无权比较该方案")
    try:
        return compare_versions(
            left_filename,
            left_version,
            right_filename,
            right_version,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plan-rollback/{filename}/{version_id}")
async def plan_rollback(
    request: Request,
    filename: str,
    version_id: str,
    source_filename: str = "",
):
    """回滚到指定版本，并在当前方案的版本树中创建分支。"""
    filepath = _safe_filepath(filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    username = getattr(request.state, "username", None)
    if auth_enabled() and not can_write(username, filename):
        raise HTTPException(status_code=403, detail="无权回滚该方案")
    source_name = source_filename or filename
    source_path = _safe_filepath(source_name)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Source plan not found")
    if auth_enabled() and not can_read(username, source_name):
        raise HTTPException(status_code=403, detail="无权读取来源版本")
    try:
        new_filename, data = rollback_plan(
            filename,
            version_id,
            source_filename=source_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    original_acl = get_acl(filename)
    set_acl(
        new_filename,
        owner=original_acl.get("owner") or username or "admin",
        editors=original_acl.get("editors") or [],
        viewers=original_acl.get("viewers") or [],
    )
    versions = list_versions(new_filename)
    return {
        "filename": new_filename,
        "plan": data,
        "version_id": versions[0]["version_id"] if versions else None,
    }


class ShareRequest(BaseModel):
    filename: str


@router.post("/share")
async def create_share_link(request: Request, req: ShareRequest):
    """生成方案只读分享链接。"""
    filepath = _safe_filepath(req.filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    username = getattr(request.state, "username", None)
    if auth_enabled() and not can_write(username, req.filename):
        raise HTTPException(status_code=403, detail="无权分享该方案")
    token = create_share(req.filename)
    return {"token": token, "permission": "read", "expires_at": None}


@router.get("/share/{token}")
async def open_share(token: str):
    """按有效分享 token 读取方案，并通过响应头返回权限和到期时间。"""
    entry = get_share_entry(token)
    if not entry:
        status = share_status(token)
        if status in ("expired", "revoked"):
            raise HTTPException(status_code=410, detail="分享链接已过期或已撤销")
        raise HTTPException(status_code=404, detail="分享链接无效")
    filename = entry["filename"]
    filepath = _safe_filepath(filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Plan not found")
    headers = {
        "X-Share-Created-At": str(entry["created_at"] or ""),
        "Cache-Control": "no-store",
    }
    if entry["expires_at"] is not None:
        headers["X-Share-Expires-At"] = str(entry["expires_at"])
    return JSONResponse(
        content=json.loads(filepath.read_text(encoding="utf-8")),
        headers=headers,
    )


class KnowledgeRequest(BaseModel):
    question: str
    plan: FullPlan | None = None


@router.post("/knowledge")
async def knowledge(request: Request, req: KnowledgeRequest):
    """轻量知识库问答：检索当前方案与历史方案。"""
    username = getattr(request.state, "username", None)
    return knowledge_search(req.question, req.plan, username=username)


class AgentAskRequest(BaseModel):
    question: str
    plan: FullPlan | None = None


@router.post("/agent/ask")
async def agent_ask(request: Request, req: AgentAskRequest):
    """Knowledge Agent：根据问题自主调用工具并合成回答。"""
    username = getattr(request.state, "username", None)
    return agent_ask_service(req.question, req.plan, username=username)


@router.post("/reminders")
async def reminders_endpoint(plan: FullPlan):
    """返回当前方案提醒列表。"""
    return {"reminders": reminders(plan)}


@router.post("/notify")
def notify_endpoint(plan: FullPlan):
    """把当前方案提醒推送到外部 Webhook。"""
    return notify_reminders(plan)


@router.post("/org-review")
async def org_review_endpoint(plan: FullPlan):
    """返回组织级复盘（成员/角色/模块/建议）。"""
    return org_review(plan)


@router.get("/load/{filename}")
async def load_plan(request: Request, filename: str):
    """加载指定计划。"""
    try:
        filepath = _safe_filepath(filename)
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        username = getattr(request.state, "username", None)
        if auth_enabled() and not can_read(username, filename):
            raise HTTPException(status_code=403, detail="无权查看该方案")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return data
    except HTTPException:
        raise


@router.delete("/plans/{filename}")
async def delete_plan(request: Request, filename: str):
    """删除指定计划。"""
    try:
        filepath = _safe_filepath(filename)
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        username = getattr(request.state, "username", None)
        if auth_enabled() and not can_write(username, filename):
            raise HTTPException(status_code=403, detail="无权删除该方案")
        filepath.unlink()
        return {"status": "ok"}
    except HTTPException:
        raise


class InterviewRequest(BaseModel):
    plan: PlanOutput
    qa_matrix: QAOutput
    user_requirements: str = ""
    project_context: str = ""
    material_text: str = ""
    material_names: list[str] = Field(default_factory=list)


def _fallback_interview_questions(req: InterviewRequest) -> list[str]:
    """LLM 生成失败时的确定性兜底问题：优先基于材料与评委关注点，
    不再是与材料无关的通用提问；也不再把"请概括核心观点"这类元问题
    甩回给答辩者（材料已有的结论应直接追问依据）。"""
    questions: list[str] = []
    material = (req.material_text or "").strip()
    focus = (req.user_requirements or "").strip()
    context = (req.project_context or "").strip()
    tasks = req.plan.tasks if req.plan else []

    if material:
        lines = [line.strip() for line in material.splitlines() if line.strip()]
        first_line = lines[0] if lines else ""
        snippet = first_line[:48] + ("…" if len(first_line) > 48 else "")
        questions.append(
            f"根据你提交的答辩材料（「{snippet}」），"
            "请说明这项成果最核心的创新点是什么？")
        # 直接针对材料中的第二个具体内容追问依据，避免模板化的
        # "请概括你最希望评委理解的核心观点"式元问题。
        if len(lines) >= 2:
            second = lines[1][:48] + ("…" if len(lines[1]) > 48 else "")
            questions.append(
                f"材料中提到「{second}」，请说明支持这一结论的数据或依据是什么？")
        else:
            questions.append(
                "材料中最重要的数据或证据是什么？它如何支撑你的核心结论？")
    elif tasks:
        questions.append(
            f"请介绍「{tasks[0].name}」这项成果的核心思路、"
            "关键决策和交付内容。")
    if focus:
        focus_snippet = focus[:60] + ("…" if len(focus) > 60 else "")
        questions.append(
            f"评委重点关注「{focus_snippet}」，请就此展开你的准备与依据。")
    if context:
        questions.append(
            "结合答辩要求，请说明你的方案如何满足其中的关键评价标准？")
    questions.append(
        "这项成果在什么情况下可能不成立，或还需要补充哪些证据？")
    return questions


@router.post("/interview")
def interview_sim(req: InterviewRequest):
    """基于实际答辩要求和用户提交材料生成模拟问题。"""
    agent = InterviewSimAgent()
    result = agent.run(
        plan=req.plan,
        qa_matrix=req.qa_matrix,
        user_requirements=req.user_requirements,
        project_context=req.project_context,
        material_text=req.material_text,
        material_names=req.material_names,
    )
    questions = [
        line.strip().lstrip("-• ")
        for line in str(result or "").splitlines()
        if line.strip()
    ]
    warning = ""
    if not questions:
        questions = _fallback_interview_questions(req)
        warning = "AI 问题生成暂不可用，已根据材料与评委关注点生成基础问题；可重新生成。"
    return {"questions": questions, "warning": warning}


class InterviewChatRequest(BaseModel):
    plan: PlanOutput
    qa_matrix: QAOutput
    user_answer: str = ""
    history: list[dict] = Field(default_factory=list)
    mode: str = "answer"
    user_requirements: str = ""
    project_context: str = ""
    material_text: str = ""
    material_names: list[str] = Field(default_factory=list)


@router.post("/interview/chat")
def interview_chat(req: InterviewChatRequest):
    """多轮互动答辩模拟：点评用户回答并提出下一个问题。"""
    agent = InterviewSimAgent()
    reply = agent.chat_turn(
        plan=req.plan,
        qa_matrix=req.qa_matrix,
        user_answer=req.user_answer,
        history=req.history,
        mode=req.mode,
        user_requirements=req.user_requirements,
        project_context=req.project_context,
        material_text=req.material_text,
        material_names=req.material_names,
    )
    return {"reply": reply}


@router.post("/recompute", response_model=FullPlan)
def recompute_plan(req: FullPlan):
    """基于任务状态/成员变动重新计算时间线和匹配（不重跑 LLM）。

    前端状态切换（completed/blocked 等）或成员变动后调用此端点，
    确保排期与分配与最新状态保持一致。
    """
    return recompute_plan_service(req)

@router.get("/plans/{filename}/export")
async def export_plan(request: Request, filename: str, fmt: str = "markdown"):
    """导出已保存方案为 Markdown 或纯文本；迁移到 /plans/{filename}/export 避免与 POST /api/export/{format} 前缀冲突。"""
    try:
        filepath = _safe_filepath(filename)
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        username = getattr(request.state, "username", None)
        if auth_enabled() and not can_read(username, filename):
            raise HTTPException(status_code=403, detail="无权查看该方案")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        md = _plan_to_markdown(data)
        content_type = "text/markdown; charset=utf-8" if fmt == "markdown" else "text/plain; charset=utf-8"
        ext = ".md" if fmt == "markdown" else ".txt"
        return Response(content=md, media_type=content_type,
                        headers={"Content-Disposition": _download_disposition(
                            f"{filename}{ext}")})
    except HTTPException:
        raise


def _plan_to_markdown(data: dict) -> str:
    """Convert a FullPlan dict to readable Markdown."""
    lines = []
    inp = data.get("input", {})
    course = inp.get("course", {})
    lines.append(f"# {course.get('name', '未命名项目')} - 项目计划")
    lines.append(f"")
    lines.append(f"**项目要求：** {course.get('description', '')}")
    lines.append(f"**截止日期：** {inp.get('deadline', '')}")
    members = inp.get("members", [])
    if members:
        lines.append(f"**团队成员：** {', '.join(m.get('name','') for m in members)}")
    lines.append("")

    if members:
        lines.append("## 组织树")
        for m in members:
            manager = m.get("manager") or "顶层"
            lines.append(
                f"- {m.get('name')}（{m.get('role', '执行成员')}，上级：{manager}）")
        lines.append("")

    plan = data.get("plan", {})
    if plan.get("summary"):
        lines.append(f"## Summary")
        lines.append(plan["summary"])
        lines.append("")
    tasks = plan.get("tasks", [])
    if tasks:
        modules = plan.get("modules", [])
        if inp.get("project_mode") == "large_project" and modules:
            lines.append("## 模块拆解")
            for module in sorted(modules, key=lambda m: m.get("order", 0)):
                module_tasks = [
                    t for t in tasks if t.get("module_id") == module.get("id")
                ]
                owner = module.get("assignee_id") or "待认领"
                lines.append(f"### {module.get('id', '')} {module.get('name', '')}（负责人：{owner}）")
                if module.get("description"):
                    lines.append(module["description"])
                lines.append("")
                lines.append("| 编号 | 任务 | 工时 | 依赖 | 技能 | 需招募 |")
                lines.append("|---|---|---|---|---|---|")
                for t in module_tasks:
                    deps = ", ".join(t.get("dependencies", []))
                    skills = ", ".join(t.get("required_skills", []))
                    need = t.get("extra_helpers_needed", 0) or 0
                    lines.append(
                        f"| {t['id']} | {t['name']} | {t.get('estimated_hours',0)}h | "
                        f"{deps or '-'} | {skills or '-'} | {need} |")
                lines.append("")
        else:
            lines.append("## 任务列表")
            lines.append("| 编号 | 任务 | 工时 | 依赖 | 技能 |")
            lines.append("|---|---|---|---|---|")
            for t in tasks:
                deps = ", ".join(t.get("dependencies", []))
                skills = ", ".join(t.get("required_skills", []))
                lines.append(f"| {t['id']} | {t['name']} | {t.get('estimated_hours',0)}h | {deps} | {skills} |")
        lines.append("")

    with_actual = [t for t in tasks if t.get("actual_hours") is not None]
    if with_actual:
        lines.append("## 实际工时复盘")
        lines.append("| 任务 | 计划工时 | 实际工时 | 偏差 | 实际完成 | 状态 |")
        lines.append("|---|---|---|---|---|---|")
        for t in with_actual:
            est = t.get("estimated_hours", 0) or 0
            act = t.get("actual_hours", 0) or 0
            dev = act - est
            dev_text = f"{dev:+.1f}h" if abs(dev) >= 0.05 else "持平"
            end_date = t.get("actual_end_date") or "-"
            status = t.get("status", "")
            lines.append(
                f"| {t['name']} | {est}h | {act}h | {dev_text} | {end_date} | {status} |"
            )
        lines.append("")

    participant_tasks = [t for t in tasks if t.get("participants")]
    if participant_tasks:
        lines.append("## 任务参与清单")
        for t in participant_tasks:
            lines.append(f"### {t['id']} {t['name']}")
            lines.append("| 参与者 | 角色 | 投入工时 | 类型 |")
            lines.append("|---|---|---|---|")
            for p in t["participants"]:
                kind = "志愿者 / 外部协作者" if p.get("is_volunteer") else "内部成员"
                lines.append(
                    f"| {p.get('name')} | {p.get('role', '执行成员')} | "
                    f"{p.get('contribution_hours', 0)}h | {kind} |")
            lines.append("")

    tl = data.get("timeline", {})
    if tl.get("tasks"):
        lines.append("## 时间线")
        lines.append(f"**总工期：** {tl.get('total_days', 0)} 天")
        cp = tl.get("critical_path", [])
        if cp:
            lines.append(f"**关键路径：** {' -> '.join(cp)}")
        lines.append("")
        lines.append("| 任务 | 开始 | 结束 | 关键 | 浮动 |")
        lines.append("|---|---|---|---|---|")
        for t in tl["tasks"]:
            crit = "是" if t.get("is_critical") else ""
            lines.append(f"| {t['task_id']} {t['name']} | {t['start_date']} | {t['end_date']} | {crit} | {t.get('float_days',0)}天 |")
        lines.append("")

    qa = data.get("qa_matrix", {})
    if qa.get("assignments"):
        lines.append("## 责任分工")
        lines.append("| 任务 | 负责人 | 主要协助 | 辅助协助 | 匹配度 |")
        lines.append("|---|---|---|---|---|")
        for a in qa["assignments"]:
            support = ", ".join(a.get("qa_support", []))
            score = f"{a.get('score',0)*100:.0f}%" if a.get("score") else "-"
            lines.append(f"| {a['task_name']} | {a['presenter']} | {a['qa_primary']} | {support} | {score} |")
        lines.append("")

    if inp.get("project_mode") == "large_project":
        volunteer_tasks = [
            t for t in tasks if (t.get("extra_helpers_needed") or 0) > 0
        ]
        if volunteer_tasks:
            pool = data.get("volunteer_pool", [])
            by_task = defaultdict(list)
            for volunteer in pool:
                by_task[volunteer.get("task_id", "")].append(volunteer)
            lines.append("## 志愿者招募计划")
            lines.append("| 任务 | 需招募 | 已确认 | 待确认 | 已婉拒 | 进度 |")
            lines.append("|---|---|---|---|---|---|")
            for t in volunteer_tasks:
                rows = by_task.get(t["id"], [])
                confirmed = sum(1 for v in rows if v.get("status") == "已确认")
                pending = sum(1 for v in rows if v.get("status") == "待确认")
                declined = sum(1 for v in rows if v.get("status") == "已婉拒")
                need = int(t.get("extra_helpers_needed") or 0)
                lines.append(
                    f"| {t['id']} {t['name']} | {need} | {confirmed} | "
                    f"{pending} | {declined} | {confirmed + pending}/{need} |")
            if pool:
                lines.append("")
                lines.append("### 认领明细")
                lines.append("| 志愿者 | 任务 | 状态 | 联系方式 | 备注 |")
                lines.append("|---|---|---|---|---|")
                for volunteer in pool:
                    task_name = next(
                        (t["name"] for t in tasks if t["id"] == volunteer.get("task_id")),
                        volunteer.get("task_id", ""))
                    lines.append(
                        f"| {volunteer.get('name', '')} | {task_name} | "
                        f"{volunteer.get('status', '待确认')} | "
                        f"{volunteer.get('contact', '') or '-'} | "
                        f"{volunteer.get('note', '') or '-'} |")
            lines.append("")

    report = data.get("report", {})
    if report.get("risk_note"):
        lines.append("## 风险提示")
        lines.append(report["risk_note"])
        lines.append("")

    return "\n".join(lines)
