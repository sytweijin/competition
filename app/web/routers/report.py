"""成员轻量汇报页：token 绑定 方案+成员，语音/拍照/状态更新闭环。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from app.config import MEMORY_DIR
from app.models.schemas import FullPlan, TaskStatus
from app.services.notifier import notify_event
from app.services.project_service import recompute_plan, record_task_actual
from app.services.report_link import (
    add_report_activity,
    create_report_token,
    get_report_activities,
    get_report_notes,
    get_report_token,
)

router = APIRouter()

MAX_REPORT_MEDIA = 15 * 1024 * 1024
ATTACH_DIR = MEMORY_DIR / "attachments"

REPORT_VOICE_PROMPT = (
    "这是成员对任务状态的语音汇报。任务名称：{task}。\n"
    "请只输出一行，格式：状态|工时|备注。\n"
    "状态只能是：完成/进行中/阻塞/未开始；\n"
    "工时：只写你自己实际花费的小时数（数字），不要写整个团队的总工时，"
    "系统会自动累加所有成员；\n"
    "备注一句话或\"无\"。\n"
    "示例：完成|3|数据分析已做完。不要输出其他内容。"
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
    member: str | None = Field(
        default=None,
        description="负责人代确认的成员姓名（仅任务负责人可用）",
    )


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


def _task_member_names(plan: FullPlan, task) -> set[str]:
    names = set()
    if task.assignee_id:
        names.add(task.assignee_id)
    names.update(task.collaborator_ids or [])
    for p in (task.participants or []):
        names.add(p.name)
    # 大型项目：已确认的志愿者也是任务成员，可汇报自己的部分
    for v in plan.volunteer_pool or []:
        if v.task_id == task.id and v.status == "已确认":
            names.add(v.name)
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
    if member not in _task_member_names(plan, task):
        raise HTTPException(
            status_code=403,
            detail=f"{member} 不是任务 {task_id} 的成员",
        )
    return filename, member


def _member_tasks(plan: FullPlan, filename: str, member: str) -> list[dict]:
    out = []
    for task in plan.plan.tasks:
        if member not in _task_member_names(plan, task):
            continue
        volunteer_names = {
            v.name for v in (plan.volunteer_pool or [])
            if v.task_id == task.id and v.status == "已确认"
        }
        role = (
            "负责人" if task.assignee_id == member
            else ("志愿者" if member in volunteer_names else "协作者")
        )
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
            "role": role,
            "members": _task_members_detail(plan, filename, task),
            "activities": get_report_activities(filename, task.id)[-8:],
            "notes": get_report_notes(filename, task.id),
        })
    return out


def _status_str(status) -> str:
    return str(status.value if hasattr(status, "value") else status)


def _task_members_detail(
    plan: FullPlan, filename: str, task,
) -> list[dict]:
    """某任务的成员进度明细（负责人/协作者/已确认志愿者）。"""
    activities = get_report_activities(filename, task.id)
    latest: dict[str, dict] = {}
    for act in activities:
        if act["member"]:
            latest[act["member"]] = act
    member_hours = _member_latest_hours(activities)
    task_status = _status_str(task.status)
    volunteer_names = {
        v.name for v in (plan.volunteer_pool or [])
        if v.task_id == task.id and v.status == "已确认"
    }

    def latest_value(name: str, key: str) -> str:
        """该成员最近一条带该字段的活动值（照片/备注不随后续状态变更消失）。"""
        for act in reversed(activities):
            if act.get("member") == name and act.get(key):
                return str(act[key])
        return ""

    members = []
    for name in sorted(_task_member_names(plan, task)):
        role = (
            "负责人" if name == task.assignee_id
            else ("志愿者" if name in volunteer_names else "协作者")
        )
        act = latest.get(name)
        if role == "负责人":
            status = act["status"] if act and act["status"] else task_status
            awaiting = False
        else:
            status = act["status"] if act and act["status"] else "pending"
            awaiting = status == "completed" and task_status != "completed"
        members.append({
            "name": name,
            "role": role,
            "status": status,
            "actual_hours": member_hours.get(name),
            "note": latest_value(name, "note"),
            "photo": latest_value(name, "photo"),
            "awaiting_confirm": awaiting,
            "ts": (act or {}).get("ts") or 0,
        })
    return members


def _is_project_leader(plan: FullPlan, member: str) -> bool:
    """大型项目：角色为"项目负责人"，或担任某成员"上级"的成员。"""
    if member not in {m.name for m in plan.input.members}:
        return False
    me = next((m for m in plan.input.members if m.name == member), None)
    if me and me.role == "项目负责人":
        return True
    if any((m.manager or "") == member for m in plan.input.members):
        return True
    return False


def _build_overview(plan: FullPlan, filename: str) -> dict:
    """大型项目团队总览：按模块分组展示任务与成员进度。"""
    tasks = plan.plan.tasks
    total = len(tasks)
    completed = sum(1 for t in tasks if _status_str(t.status) == "completed")
    planned = round(sum(t.estimated_hours or 0 for t in tasks), 1)
    actual = round(sum(t.actual_hours or 0 for t in tasks), 1)
    modules = sorted(plan.plan.modules or [], key=lambda m: m.order or 0)
    mod_out = []
    seen_ids: set[str] = set()
    for m in modules:
        mtasks = [t for t in tasks if t.module_id == m.id]
        seen_ids.update(t.id for t in mtasks)
        m_completed = sum(
            1 for t in mtasks if _status_str(t.status) == "completed")
        mod_out.append({
            "id": m.id,
            "name": m.name,
            "assignee_id": m.assignee_id or "",
            "task_count": len(mtasks),
            "completed_count": m_completed,
            "tasks": [{
                "id": t.id,
                "name": t.name,
                "status": _status_str(t.status),
                "estimated_hours": t.estimated_hours,
                "actual_hours": t.actual_hours,
                "members": _task_members_detail(plan, filename, t),
            } for t in mtasks],
        })
    leftover = [t for t in tasks if t.id not in seen_ids]
    if leftover:
        mod_out.append({
            "id": "其他",
            "name": "未分组任务",
            "assignee_id": "",
            "task_count": len(leftover),
            "completed_count": sum(
                1 for t in leftover if _status_str(t.status) == "completed"),
            "tasks": [{
                "id": t.id,
                "name": t.name,
                "status": _status_str(t.status),
                "estimated_hours": t.estimated_hours,
                "actual_hours": t.actual_hours,
                "members": _task_members_detail(plan, filename, t),
            } for t in leftover],
        })
    return {
        "project": plan.input.course.name,
        "deadline": plan.input.deadline.isoformat(),
        "stats": {
            "completed": completed,
            "total": total,
            "planned_hours": planned,
            "actual_hours": actual,
        },
        "modules": mod_out,
    }


def _member_latest_hours(activities: list[dict]) -> dict[str, float]:
    """每个成员最近一次上报的工时。

    带工时的上报会写入/覆盖该成员工时；纯状态变更（无工时、非照片）会清除
    该成员此前工时——"改回未完成"后其工时不再计入累计。照片上传不清除工时。
    """
    latest: dict[str, float] = {}
    for act in activities:
        name = act.get("member") or ""
        if not name:
            continue
        hours = act.get("actual_hours")
        if hours is not None:
            latest[name] = float(hours)
        elif (act.get("status") and not act.get("photo")
              and act["status"] != "confirmed"):
            latest.pop(name, None)
    return latest


def _member_hours_total(activities: list[dict]) -> float | None:
    """每个成员取最近一次上报的工时，求和作为任务实际总工时。"""
    latest = _member_latest_hours(activities)
    if not latest:
        return None
    return round(sum(latest.values()), 2)


def _apply_update(
    entry: dict, task_id: str, status: str | None,
    actual_hours: float | None, actual_end_date: str | None,
    note: str | None, photo: str | None = None,
    target_member: str | None = None,
) -> dict:
    from datetime import date

    filename, member = _authorize(entry, task_id)
    plan = _load_plan(filename)
    task = next(t for t in plan.plan.tasks if t.id == task_id)
    is_owner = task.assignee_id == member

    if target_member and target_member != member:
        # 负责人代成员确认/标记状态（如志愿者线下完成、由负责人补录）
        if not is_owner:
            raise HTTPException(
                status_code=403,
                detail="只有任务负责人可以确认其他成员的状态",
            )
        if target_member not in _task_member_names(plan, task):
            raise HTTPException(
                status_code=400,
                detail=f"{target_member} 不是任务 {task_id} 的成员",
            )
        member_status = (
            "confirmed" if status in ("completed", "confirmed", None)
            else status
        )
        add_report_activity(
            filename, task_id, target_member,
            status=member_status,
            note=note,
        )
        plan = recompute_plan(plan)
        _save_plan(filename, plan)
        if member_status == "confirmed":
            notify_event(
                plan,
                f"{member} 已确认 {target_member} 完成「{task.name}」"
                + (f"（备注：{note}）" if note else ""),
            )
        return {
            "ok": True,
            "project": plan.input.course.name,
            "member": member,
            "task_id": task_id,
            "status": task.status,
            "member_status": member_status,
            "awaiting_confirm": False,
        }

    reverted = False
    if status and status in ("pending", "in_progress", "completed", "blocked"):
        current = str(
            task.status.value if hasattr(task.status, "value") else task.status)
        if is_owner:
            if status == "completed":
                # 不变量：任务完成 ⟺ 所有成员自己的部分均完成
                latest: dict[str, dict] = {}
                for act in get_report_activities(filename, task_id):
                    if act["member"]:
                        latest[act["member"]] = act
                unfinished = sorted({
                    name for name, act in latest.items()
                    if name != member
                    and act.get("status") in (
                        "pending", "in_progress", "blocked")
                })
                if unfinished:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "还有成员未完成（" + "、".join(unfinished)
                            + "），请先确认其完成再标记任务完成"
                        ),
                    )
            task.status = TaskStatus(status)
        else:
            if current == "pending":
                # 协作者开始或完成自己的部分：任务至少显示"进行中"
                if status in ("in_progress", "completed"):
                    task.status = TaskStatus("in_progress")
            elif current == "completed" and status in (
                    "pending", "in_progress", "blocked"):
                # 协作者把已完成改回未完成：任务不能保持"已完成"，回退进行中
                task.status = TaskStatus("in_progress")
                reverted = True
                if not note:
                    note = (
                        "报告阻塞（任务状态已回退为进行中，等待负责人处理）"
                        if status == "blocked" else
                        "改回未完成（任务状态已回退为进行中，"
                        "等待负责人重新确认）"
                    )

    end_date = None
    if actual_end_date:
        try:
            end_date = date.fromisoformat(actual_end_date)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="完成日期格式错误") from exc
    if not is_owner:
        # 任务级完成日期由负责人设置；协作者只上报自己的状态/工时/交付物
        end_date = None

    activities = add_report_activity(
        filename, task_id, member,
        status=status or "",
        actual_hours=actual_hours,
        note=note,
        photo=photo,
    )
    # 任务实际总工时 = 各成员最近一次上报工时的累加
    total_hours = _member_hours_total(activities)
    if total_hours is not None or end_date is not None:
        plan = record_task_actual(
            plan, task_id,
            actual_hours=total_hours,
            actual_end_date=end_date,
        )

    plan = recompute_plan(plan)
    _save_plan(filename, plan)

    hours_suffix = (
        f"（实际 {actual_hours:g}h）" if actual_hours else "")
    if is_owner:
        if status == "completed":
            notify_event(plan, f"{member} 已完成「{task.name}」" + hours_suffix)
        elif status == "blocked":
            notify_event(plan, f"{member} 报告「{task.name}」遇到阻塞")
        elif status == "in_progress":
            notify_event(
                plan,
                f"{member} 更新了「{task.name}」进度" + hours_suffix,
            )
    elif reverted:
        msg = (
            f"{member} 报告「{task.name}」自己的部分遇到阻塞"
            "（任务状态已回退为进行中）"
            if status == "blocked" else
            f"{member} 将「{task.name}」改回未完成"
            "（任务状态已回退为进行中）"
        )
        notify_event(plan, msg)
    elif status in ("completed", "blocked"):
        verb = "已完成" if status == "completed" else "报告遇到阻塞"
        notify_event(
            plan,
            f"{member} {verb}「{task.name}」自己的部分"
            + ("（等待负责人确认）" if status == "completed" else ""),
        )
    elif status == "in_progress":
        notify_event(
            plan,
            f"{member} 更新了「{task.name}」进度" + hours_suffix,
        )

    return {
        "ok": True,
        "project": plan.input.course.name,
        "member": member,
        "task_id": task_id,
        "status": task.status,
        "member_status": status or "",
        "awaiting_confirm": bool(not is_owner and status == "completed"),
    }


@router.post("/report/link")
def report_link(req: ReportLinkRequest):
    """为已保存方案中的某成员生成汇报链接。"""
    plan = _load_plan(req.filename)
    member_names = {m.name for m in plan.input.members}
    volunteer_names = {
        v.name for v in (plan.volunteer_pool or [])
        if v.status == "已确认"
    }
    if req.member not in member_names and req.member not in volunteer_names:
        raise HTTPException(
            status_code=400,
            detail="该成员不在方案成员或已确认志愿者列表中",
        )
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
    overview = None
    if plan.input.project_mode == "large_project" and _is_project_leader(
            plan, member):
        overview = _build_overview(plan, filename)
    return {
        "project": plan.input.course.name,
        "member": member,
        "deadline": plan.input.deadline.isoformat(),
        "tasks": _member_tasks(plan, filename, member),
        "overview": overview,
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
    status_label = "进行中"
    hours = None
    note = ""
    if len(parts) >= 1:
        first = parts[0]
        if "完成" in first:
            status_label = "完成"
        elif "阻塞" in first or "卡住" in first or "无法" in first:
            status_label = "阻塞"
        elif "进行" in first or "开始" in first or "在做" in first:
            status_label = "进行中"
        elif "未开始" in first or "还没" in first:
            status_label = "未开始"
    status = {
        "完成": "completed",
        "进行中": "in_progress",
        "阻塞": "blocked",
        "未开始": "pending",
    }.get(status_label, "in_progress")
    if len(parts) >= 2:
        try:
            hours = float(parts[1].replace("小时", "").replace("h", ""))
        except (TypeError, ValueError):
            hours = None
    if len(parts) >= 3 and parts[2] and parts[2] != "无":
        note = parts[2]

    return {
        "task_id": task_id,
        "parsed": {
            "status": status,
            "status_label": status_label,
            "actual_hours": hours,
            "note": note,
        },
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
        "completed",
        None, None, note, photo=attach_path.name)
    result["photo"] = attach_path.name
    result["member"] = member
    result["confirmed"] = is_owner
    return result


@router.get("/report/attachment")
def report_attachment(token: str, task_id: str):
    """查看任务交付物照片：仅任务成员可用，且只服务该任务记录过的附件。"""
    entry = get_report_token(token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    filename, _member = _authorize(entry, task_id)
    photo_name = ""
    for act in reversed(get_report_activities(filename, task_id)):
        if act.get("photo"):
            photo_name = act["photo"]
            break
    if not photo_name:
        raise HTTPException(status_code=404, detail="该任务暂无交付物照片")
    safe = Path(photo_name).name
    path = ATTACH_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="交付物文件不存在")
    return FileResponse(
        path, media_type="image/jpeg",
        filename=safe,
    )


@router.post("/report/update")
def report_update(req: ReportUpdateRequest):
    """应用成员汇报（状态/工时/备注）并自动重算、保存、通知。"""
    entry = get_report_token(req.token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    return _apply_update(
        entry, req.task_id, req.status,
        req.actual_hours, req.actual_end_date, req.note,
        target_member=req.member,
    )
