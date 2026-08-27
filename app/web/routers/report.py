"""成员轻量汇报页：token 绑定 方案+成员，语音/拍照/状态更新闭环。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse

from app.config import MEMORY_DIR
from app.models.schemas import FullPlan, TaskStatus
from app.services.notifier import notify_event
from app.services.project_service import recompute_plan, record_task_actual
from app.services.audit_store import list_versions
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


class TaskStatusRequest(BaseModel):
    plan: FullPlan
    task_id: str = Field(..., min_length=1)
    status: str = Field(..., description="pending/in_progress/completed/blocked")
    confirm_members: list[str] = Field(
        default_factory=list,
        description="强制完成时一并确认的成员姓名",
    )
    filename: str = Field(
        default="", description="已保存的方案文件名（空则不落盘）"
    )
    base_version: str = Field(default="", description="并发校验用版本号")
    base_fingerprint: str = Field(default="", description="并发校验用内容指纹")


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
    """写回方案文件，保证成员汇报页与主页面读到最新状态。

    版本树只记录用户主动"保存方案"（/api/save）的检查点；成员汇报/状态
    变更只更新方案文件，不滚动版本——版本不应被状态微调刷屏。
    """
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


def _unfinished_member_list(plan: FullPlan, filename: str, task) -> list[dict]:
    """返回任务完成前仍需确认的成员（含负责人与已确认志愿者）。

    - 有未完成上报记录（pending/in_progress/blocked）的成员一律要求确认；
    - 已确认志愿者即使从未上报也要求确认——志愿者是认领了任务并招募进来的
      正式参与者，主页面完成任务必须覆盖他们，否则任务完成而志愿者行悬挂
      "待开始"；
    - 其他成员（骨干/协作者）从未上报不要求确认。
    """
    latest: dict[str, dict] = {}
    for act in get_report_activities(filename, task.id):
        if act["member"]:
            latest[act["member"]] = act
    volunteer_names = {
        v.name for v in (plan.volunteer_pool or [])
        if v.task_id == task.id and v.status == "已确认"
    }
    out: list[dict] = []
    for name in sorted(_task_member_names(plan, task)):
        act = latest.get(name)
        if not act or not act.get("status"):
            if name in volunteer_names:
                out.append({"name": name, "status": "pending"})
            continue  # 从未上报：仅已确认志愿者要求确认
        status = str(act["status"])
        if status in ("pending", "in_progress", "blocked"):
            out.append({"name": name, "status": status})
    return out


def _blocked_member_list(plan: FullPlan, filename: str, task) -> list[dict]:
    """返回任务里仍报告阻塞的成员（含负责人），用于解除阻塞时的确认。"""
    latest: dict[str, dict] = {}
    for act in get_report_activities(filename, task.id):
        if act["member"]:
            latest[act["member"]] = act
    out: list[dict] = []
    for name in sorted(_task_member_names(plan, task)):
        act = latest.get(name)
        if act and act.get("status") == "blocked":
            out.append({"name": name, "status": "blocked"})
    return out


def _other_members_active(
    plan: FullPlan, filename: str, task, except_name: str,
) -> bool:
    """除 except_name 外，是否有成员的最新状态仍处于活跃。

    活跃 = 进行中 / 已完成 / 已确认 / 阻塞；未上报的成员按"未开始"算，
    不视为活跃。用于协作者把任务"拉回待开始"时判断任务整体是否还有人
    在做——若没有任何人活跃，任务应回退 pending，而不是卡在被撤回的
    "进行中"上。
    """
    latest: dict[str, str] = {}
    for act in get_report_activities(filename, task.id):
        if act["member"] and act.get("status"):
            latest[act["member"]] = str(act["status"])
    for name in _task_member_names(plan, task):
        if name == except_name:
            continue
        if latest.get(name, "pending") in (
                "in_progress", "completed", "confirmed", "blocked"):
            return True
    return False


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

    def member_photos(name: str) -> list[str]:
        """该成员全部交付物照片（按上传顺序），支持一人多张。"""
        return [
            str(act["photo"])
            for act in activities
            if act.get("member") == name and act.get("photo")
        ]

    members = []
    for name in sorted(_task_member_names(plan, task)):
        role = (
            "负责人" if name == task.assignee_id
            else ("志愿者" if name in volunteer_names else "协作者")
        )
        act = latest.get(name)
        if role == "负责人":
            # 负责人行只反映负责人自己的上报状态（与协作者同规则），
            # 不被任务整体状态劫持：协作者报阻塞/主页面改状态都不会
            # 强加给负责人行，避免"负责人没动却显示阻塞/进行中"。
            # 负责人本人在汇报页上报时，其上报状态即任务状态（is_owner
            # 直接设置），因此负责人上报后两处仍一致。
            status = act["status"] if act and act["status"] else "pending"
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
            "photos": member_photos(name),
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
        if member_status == "confirmed":
            # 负责人代确认该成员完成：若负责人自己也已完成（completed/
            # confirmed）且其余成员无未完成记录，任务整体自动完成，
            # 保证主页面与汇报页同步（否则代确认后任务仍卡在进行中）。
            owner = task.assignee_id
            owner_done = False
            for act in reversed(get_report_activities(filename, task_id)):
                if act["member"] == owner:
                    owner_done = str(act["status"]) in (
                        "completed", "confirmed")
                    break
            if owner_done and not _unfinished_member_list(
                    plan, filename, task):
                task.status = TaskStatus("completed")
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
            if status == "blocked":
                # 阻塞优先：任何成员报告阻塞，任务整体立即置为阻塞
                #（负责人/主页面可随后显式调整）。已完成任务被协作者
                # 改回阻塞时视为回退，通知负责人重新处理。
                if current == "completed":
                    reverted = True
                    if not note:
                        note = ("报告阻塞（任务已由负责人确认完成，"
                                "任务状态已回退为阻塞，等待负责人重新处理）")
                task.status = TaskStatus("blocked")
            elif current == "pending":
                # 协作者开始或完成自己的部分：任务至少显示"进行中"
                if status in ("in_progress", "completed"):
                    task.status = TaskStatus("in_progress")
            elif current == "completed" and status in ("pending", "in_progress"):
                # 协作者把已完成改回未完成：任务不能保持"已完成"，回退进行中
                task.status = TaskStatus("in_progress")
                reverted = True
                if not note:
                    note = ("改回未完成（任务状态已回退为进行中，"
                            "等待负责人重新确认）")
            elif current == "in_progress" and status == "pending":
                # 协作者把自己的部分改回未开始：若没有其他成员仍在进行/
                # 完成/阻塞，任务回退 pending，避免任务状态卡在被撤回的
                # "进行中"上（负责人行跟随任务状态，同步回退）。
                if not _other_members_active(plan, filename, task, member):
                    task.status = TaskStatus("pending")
                    if not note:
                        note = "改回待开始（自己的部分暂停）"

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
    change_parts = []
    if status:
        change_parts.append(f"状态→{status}")
    if actual_hours:
        change_parts.append(f"上报 {actual_hours:g}h")
    if photo:
        change_parts.append("上传交付物照片")
    if note:
        change_parts.append("备注")
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
        if status == "blocked":
            msg = (f"{member} 报告「{task.name}」自己的部分遇到阻塞"
                   "（任务状态已回退为阻塞，等待负责人重新处理）")
        else:
            msg = (f"{member} 将「{task.name}」改回未完成"
                   "（任务状态已回退为进行中，等待负责人重新确认）")
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
    # 时间戳 + uuid 双保险：同一秒内连续上传（或并发上传）也不会互相覆盖
    attach_path = ATTACH_DIR / (
        f"{filename.replace('.json', '')}_{safe_id}_"
        f"{int(time.time())}_{uuid.uuid4().hex[:12]}{ext}"
    )
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
def report_attachment(token: str, task_id: str, photo: str = ""):
    """查看任务交付物照片：仅任务成员可用，且只服务该任务记录过的附件。

    photo 参数可指定具体照片文件名（支持一人多张）；缺省返回最近一张，
    保持旧调用兼容。
    """
    entry = get_report_token(token)
    if not entry:
        raise HTTPException(status_code=404, detail="汇报链接无效或已过期")
    filename, _member = _authorize(entry, task_id)
    activities = get_report_activities(filename, task_id)
    allowed = {
        str(act["photo"])
        for act in activities
        if act.get("photo")
    }
    if photo:
        photo_name = photo
    else:
        photo_name = ""
        for act in reversed(activities):
            if act.get("photo"):
                photo_name = str(act["photo"])
                break
    if not photo_name:
        raise HTTPException(status_code=404, detail="该任务暂无交付物照片")
    safe = Path(photo_name).name
    if safe not in allowed:
        raise HTTPException(status_code=404, detail="交付物照片不存在")
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


@router.post("/task-status")
def update_task_status(req: TaskStatusRequest):
    """主页面直接修改任务状态（整体状态，管理员/负责人视角）。

    - 非 completed：直接设置状态并落盘（不生成版本——版本只在"保存方案"时生成）；
    - completed：任务完成 ⟺ 全员完成——有成员仍未完成/未确认时返回 400
      与成员清单，前端确认后带 confirm_members 重发，把这些成员标记为
      "已确认"（活动历史保留）后再完成；
    - 落盘前做 base_version 并发校验，防止覆盖汇报页刚写入的进度。
    """
    task = next((t for t in req.plan.plan.tasks if t.id == req.task_id), None)
    if task is None:
        raise HTTPException(status_code=400, detail=f"任务不存在：{req.task_id}")
    if req.status not in ("pending", "in_progress", "completed", "blocked"):
        raise HTTPException(status_code=400, detail=f"状态不合法：{req.status}")

    plan = req.plan
    filename = req.filename or ""
    if req.base_version and filename:
        versions = list_versions(filename)
        if versions and versions[0]["version_id"] != req.base_version:
            raise HTTPException(
                status_code=409,
                detail="方案已被其他人更新（如成员汇报），请刷新后再改状态",
            )
    if filename and req.base_fingerprint:
        from app.services.plan_io import plan_fingerprint
        filepath = _safe_plan_path(filename)
        if filepath.exists() and plan_fingerprint(
                filepath.read_text(encoding="utf-8")) != req.base_fingerprint:
            raise HTTPException(
                status_code=409,
                detail="方案已被其他人更新（如成员汇报），请刷新后再改状态",
            )

    unfinished = (
        _unfinished_member_list(plan, filename, task) if filename else [])
    blocked = (
        _blocked_member_list(plan, filename, task) if filename else [])
    if req.status == "completed" and filename:
        unfinished_names = {item["name"] for item in unfinished}
        missing = unfinished_names - set(req.confirm_members)
        if missing:
            detail = (
                "以下成员仍显示未完成："
                + "、".join(
                    f"{item['name']}（{_status_str(item['status'])}）"
                    for item in unfinished)
                + "；确认完成将同时确认其完成"
            )
            return JSONResponse(
                status_code=400,
                content={
                    "detail": detail,
                    "unfinished_members": unfinished,
                },
            )
        # 确认成员：写入 confirmed 活动，保留其此前上报的历史（含阻塞）
        for name in req.confirm_members:
            if name in unfinished_names:
                add_report_activity(
                    filename, task.id, name,
                    status="confirmed",
                    note="主页面确认任务完成时由管理员确认",
                )
    elif (req.status in ("in_progress", "pending")
            and _status_str(task.status) == "blocked" and filename):
        # 解除阻塞同样需要确认：把任务从"阻塞"改为进行中/待开始，等于
        # 单方面否定成员的阻塞报告，必须提示并确认，不能静默覆盖。
        blocked_names = {item["name"] for item in blocked}
        missing = blocked_names - set(req.confirm_members)
        if missing:
            label = "进行中" if req.status == "in_progress" else "待开始"
            detail = (
                "以下成员仍报告阻塞："
                + "、".join(item["name"] for item in blocked)
                + f"；确认后其状态将标记为已处理（{label}）"
            )
            return JSONResponse(
                status_code=400,
                content={"detail": detail, "blocked_members": blocked},
            )
        for name in req.confirm_members:
            if name in blocked_names:
                label = "进行中" if req.status == "in_progress" else "待开始"
                add_report_activity(
                    filename, task.id, name,
                    status=req.status,
                    note=(f"主页面将任务改为{label}时确认阻塞已处理"
                          "（原上报：阻塞）"),
                )

    task.status = TaskStatus(req.status)
    if filename and task.assignee_id:
        # 主页面改状态 = 管理员/负责人确认任务状态：写负责人活动，
        # 让负责人行同步（协作者上报只影响任务整体，不碰负责人行）。
        # 保留负责人此前上报的工时，避免 status-only 活动误清工时。
        owner = task.assignee_id
        owner_hours = None
        for act in reversed(get_report_activities(filename, task.id)):
            if act["member"] == owner and act.get("actual_hours") is not None:
                owner_hours = act["actual_hours"]
                break
        status_label = {
            "pending": "待开始", "in_progress": "进行中",
            "completed": "已完成", "blocked": "阻塞",
        }.get(req.status, req.status)
        add_report_activity(
            filename, task.id, owner,
            status=req.status,
            actual_hours=owner_hours,
            note=f"主页面将任务改为{status_label}（管理员/负责人确认）",
        )
    plan = recompute_plan(plan)
    if filename:
        _save_plan(filename, plan)
    from app.services.plan_io import plan_fingerprint
    fingerprint = None
    if filename:
        filepath = _safe_plan_path(filename)
        fingerprint = plan_fingerprint(filepath.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "plan": plan,
        "unfinished_members": unfinished,
        "blocked_members": blocked,
        "fingerprint": fingerprint,
    }
