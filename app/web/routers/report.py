"""成员轻量汇报页：token 绑定 方案+成员，语音/拍照/状态更新闭环。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import MEMORY_DIR
from app.models.schemas import FullPlan, TaskStatus
from app.services.notifier import notify_event
from app.services.project_service import recompute_plan, record_task_actual
from app.services.report_link import (
    add_report_note,
    create_report_token,
    get_report_notes,
    get_report_token,
)

router = APIRouter()

MAX_REPORT_MEDIA = 15 * 1024 * 1024
ATTACH_DIR = MEMORY_DIR / "attachments"

REPORT_VOICE_PROMPT = (
    "这是成员对任务状态的语音汇报。任务名称：{task}。\n"
    "请只输出一行，格式：状态|工时|备注。\n"
    "状态只能是：完成/进行中/阻塞/未开始；工时写数字或\"无\"；备注一句话或\"无\"。\n"
    "示例：完成|6|数据已归档。不要输出其他内容。"
)


class ReportLinkRequest(BaseModel):
    filename: str = Field(..., min_length=1, description="已保存的方案文件名")
    member: str = Field(..., min_length=1, description="成员姓名")


class ReportUpdateRequest(BaseModel):
    token: str = Field(..., min_length=1)
    task_id: str = Field(..., min_length=1)
    status: str | None = Field(default=None)
    actual_hours: float | None = Field(default=None, ge=0)
    actual_end_date: str | None = Field(default=None)
    note: str | None = Field(default=None)


def _safe_plan_path(filename: str) -> Path:
    safe = Path(filename).name
    if not safe or safe in (".", "..") or not safe.endswith(".json"):
        raise HTTPException(status_code=400, detail="非法文件名")
    return MEMORY_DIR / safe


def _load_plan(filename: str) -> FullPlan:
    path = _safe_plan_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="方案不存在，请先保存方案")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return FullPlan.model_validate(data)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"方案读取失败：{type(exc).__name__}") from exc


def _save_plan(filename: str, plan: FullPlan) -> None:
    _safe_plan_path(filename).write_text(
        plan.model_dump_json(indent=2), encoding="utf-8")


def _task_member_names(task) -> set[str]:
    names = set()
    if task.assignee_id:
        names.add(task.assignee_id)
    names.update(task.collaborator_ids or [])
    for p in (task.participants or []):
        names.add(p.name)
    return names


def _authorize(entry: dict, task_id: str) -> tuple[str, str]:
    filename = entry.get("filename") or ""
    member = entry.get("member") or ""
    if not filename or not member:
        raise HTTPException(status_code=400, detail="汇报链接无效")
    plan = _load_plan(filename)
    task = next((t for t in plan.plan.tasks if t.id == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    if member not in _task_member_names(task):
        raise HTTPException(
            status_code=403,
            detail=f"{member} 不是任务 {task_id} 的成员",
        )
    return filename, member


def _member_tasks(plan: FullPlan, filename: str, member: str) -> list[dict]:
    out = []
    for task in plan.plan.tasks:
        if member not in _task_member_names(task):
            continue
        out.append({
            "id": task.id,
            "name": task.name,
            "estimated_hours": task.estimated_hours,
            "status": task.status,
            "actual_hours": task.actual_hours,
            "actual_end_date": (
                task.actual_end_date.isoformat()
                if task.actual_end_date else None),
            "start_date": (
                task.start_date.isoformat() if task.start_date else None),
            "end_date": (
                task.end_date.isoformat() if task.end_date else None),
            "role": "负责人" if task.assignee_id == member else "协作者",
            "notes": get_report_notes(filename, task.id),
        })
    return out


def _apply_update(
    entry: dict, task_id: str, status: str | None,
    actual_hours: float | None, actual_end_date: str | None,
    note: str | None,
) -> dict:
    from datetime import date

    filename, member = _authorize(entry, task_id)
    plan = _load_plan(filename)
    task = next(t for t in plan.plan.tasks if t.id == task_id)
    is_owner = task.assignee_id == member

    if status in ("completed", "blocked") and not is_owner:
        raise HTTPException(
            status_code=403,
            detail="该任务需由负责人确认完成/阻塞状态；协作者可更新进度、工时与交付物",
        )

    if status and status in ("pending", "in_progress", "completed", "blocked"):
        task.status = TaskStatus(status)

    end_date = None
    if actual_end_date:
        try:
            end_date = date.fromisoformat(actual_end_date)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="完成日期格式错误") from exc

    if actual_hours is not None or end_date is not None:
        plan = record_task_actual(
            plan, task_id,
            actual_hours=actual_hours,
            actual_end_date=end_date,
        )

    plan = recompute_plan(plan)
    _save_plan(filename, plan)

    if note:
        add_report_note(filename, task_id, note)

    if status == "completed" and is_owner:
        notify_event(
            plan,
            f"{member} 已完成「{task.name}」"
            + (f"（实际 {actual_hours:g}h）" if actual_hours else ""),
        )
    elif status == "blocked" and is_owner:
        notify_event(plan, f"{member} 报告「{task.name}」遇到阻塞")
    elif status == "in_progress":
        notify_event(
            plan,
            f"{member} 更新了「{task.name}」进度"
            + ("（实际工时 " + f"{actual_hours:g}h）" if actual_hours else ""),
        )

    return {
        "ok": True,
        "project": plan.input.course.name,
        "member": member,
        "task_id": task_id,
        "status": task.status,
    }


@router.post("/report/link")
def report_link(req: ReportLinkRequest):
    """为已保存方案中的某成员生成汇报链接。"""
    plan = _load_plan(req.filename)
    member_names = {m.name for m in plan.input.members}
    if req.member not in member_names:
        raise HTTPException(status_code=400, detail="该成员不在方案成员列表中")
    token = create_report_token(req.filename, req.member)
    return {"token": token, "url": f"/?report={token}"}


@router.get("/report/state")
def report_state(token: str):
    """返回汇报页数据：项目名、成员、我的任务列表。"""
    entry = get_report_token(token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    filename = entry["filename"]
    member = entry["member"]
    plan = _load_plan(filename)
    return {
        "project": plan.input.course.name,
        "member": member,
        "deadline": plan.input.deadline.isoformat(),
        "tasks": _member_tasks(plan, filename, member),
    }


@router.post("/report/voice")
async def report_voice(
    token: str = Form(...),
    task_id: str = Form(...),
    file: UploadFile = File(...),
):
    """听成员语音汇报，解析出 状态/工时/备注（供前端确认后应用）。"""
    import base64

    from app.services.media_analysis import _decode_audio_to_pcm16k
    from app.services.realtime_client import RealtimeClient, RealtimeError

    entry = get_report_token(token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    filename, _member = _authorize(entry, task_id)
    plan = _load_plan(filename)
    task = next(t for t in plan.plan.tasks if t.id == task_id)

    raw = await file.read(MAX_REPORT_MEDIA + 1)
    if len(raw) > MAX_REPORT_MEDIA:
        raise HTTPException(status_code=413, detail="录音文件超过 15MB 限制")
    try:
        pcm = await asyncio.to_thread(_decode_audio_to_pcm16k, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audio_b64 = base64.b64encode(pcm).decode("utf-8")
    prompt = REPORT_VOICE_PROMPT.format(task=task.name)
    from app.services.omni_chat import understand_audio

    try:
        result = await understand_audio(
            audio_b64, "", prompt, 256, 120,
        )
    except RealtimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    parts = [p.strip() for p in (result.text or "").split("|")]
    status = "进行中"
    hours = None
    note = ""
    if len(parts) >= 1:
        first = parts[0]
        if "完成" in first:
            status = "完成"
        elif "阻塞" in first or "卡住" in first or "无法" in first:
            status = "阻塞"
        elif "进行" in first or "开始" in first or "在做" in first:
            status = "进行中"
        elif "未开始" in first or "还没" in first:
            status = "未开始"
    if len(parts) >= 2:
        try:
            hours = float(parts[1].replace("小时", "").replace("h", ""))
        except (TypeError, ValueError):
            hours = None
    if len(parts) >= 3 and parts[2] and parts[2] != "无":
        note = parts[2]

    return {
        "task_id": task_id,
        "parsed": {"status": status, "actual_hours": hours, "note": note},
        "raw": (result.text or "").strip()[:300],
    }


@router.post("/report/photo")
async def report_photo(
    token: str = Form(...),
    task_id: str = Form(...),
    file: UploadFile = File(...),
):
    """成员上传交付物照片：保存证据并标记任务完成。"""
    entry = get_report_token(token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    filename, member = _authorize(entry, task_id)
    raw = await file.read(MAX_REPORT_MEDIA + 1)
    if len(raw) > MAX_REPORT_MEDIA:
        raise HTTPException(status_code=413, detail="照片超过 15MB 限制")
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(
        ch for ch in task_id if ch.isalnum() or ch in "_-") or "task"
    ext = Path(file.filename or "photo.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    attach_path = ATTACH_DIR / f"{filename.replace('.json', '')}_{safe_id}{ext}"
    attach_path.write_bytes(raw)
    note = f"交付物照片已上传（{attach_path.name}）"
    plan = _load_plan(filename)
    task = next(t for t in plan.plan.tasks if t.id == task_id)
    is_owner = task.assignee_id == member
    note += "" if is_owner else "，等待负责人确认完成"
    result = _apply_update(
        entry, task_id,
        "completed" if is_owner else "in_progress",
        None, None, note)
    result["photo"] = attach_path.name
    result["member"] = member
    result["confirmed"] = is_owner
    return result


@router.post("/report/update")
def report_update(req: ReportUpdateRequest):
    """应用成员汇报（状态/工时/备注）并自动重算、保存、通知。"""
    entry = get_report_token(req.token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    return _apply_update(
        entry, req.task_id, req.status,
        req.actual_hours, req.actual_end_date, req.note,
    )
