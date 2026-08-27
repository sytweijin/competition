"""团队成员变动与计划重算路由。"""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import config
from app.agents.scoring import assign_with_balance
from app.agents.timeline import TimelineAgent, sync_task_dates
from app.models.schemas import FullPlan, TeamMember

logger = logging.getLogger(__name__)
router = APIRouter()


class MemberEditRequest(BaseModel):
    plan: FullPlan
    removed_members: list[str] = Field(
        default_factory=list, description="要移除的成员名"
    )
    updated_members: dict[str, float] = Field(
        default_factory=dict, description="更新的每日工时 {姓名: 新工时}"
    )
    member_roles: dict[str, str] = Field(
        default_factory=dict, description="更新的角色 {姓名: 角色}"
    )
    member_managers: dict[str, str] = Field(
        default_factory=dict, description="更新的上级 {姓名: 上级姓名}"
    )
    member_unavailable_dates: dict[str, list[date]] = Field(
        default_factory=dict, description="更新的不可用日期 {姓名: [日期]}"
    )
    added_members: list = Field(
        default_factory=list,
        description="新加入的成员 [{name, daily_available_hours}, ...]",
    )


@router.post("/edit-members", response_model=FullPlan)
def edit_members_endpoint(req: MemberEditRequest):
    """处理成员增删和工时变动，然后重算分工、排期与报告。"""
    fp = req.plan
    remaining = max(1, (fp.input.deadline - config.today()).days)
    new_members = []

    for member in fp.input.members:
        if member.name in req.removed_members:
            continue
        if member.name in req.updated_members:
            daily_hours = max(0.5, req.updated_members[member.name])
            member = member.model_copy(update={
                "daily_available_hours": daily_hours,
                "available_hours": max(daily_hours, daily_hours * remaining),
            })
        if member.name in req.member_roles:
            role = (req.member_roles[member.name] or "执行成员").strip()
            if role:
                member = member.model_copy(update={"role": role})
        if member.name in req.member_managers:
            manager = (
                (req.member_managers[member.name] or "").strip()
                if fp.input.project_mode == "large_project" else ""
            )
            member = member.model_copy(update={"manager": manager})
        if member.name in req.member_unavailable_dates:
            member = member.model_copy(update={
                "unavailable_dates": sorted(set(
                    req.member_unavailable_dates[member.name]
                )),
            })
        new_members.append(member)

    for added in req.added_members:
        name = added.get("name", "").strip()
        if not name:
            continue
        daily_hours = max(0.5, float(added.get("daily_available_hours", 4)))
        skill_tags = added.get("skill_tags", [])
        if isinstance(skill_tags, str):
            skill_tags = [tag.strip() for tag in skill_tags.split(",") if tag.strip()]
        new_members.append(TeamMember(
            name=name,
            role=added.get("role") or "执行成员",
            manager=(added.get("manager") or "")
            if fp.input.project_mode == "large_project" else "",
            daily_available_hours=daily_hours,
            available_hours=max(daily_hours, daily_hours * remaining),
            skill_tags=skill_tags or [],
            unavailable_dates=added.get("unavailable_dates") or [],
        ))

    if not new_members:
        raise HTTPException(status_code=400, detail="不能删除所有成员")

    new_input = fp.input.model_copy(update={"members": new_members})
    # 移除成员后同步清理模块负责人引用，避免旧模块 assignee 指向已移除成员，
    # 否则后续"确认最终分工"会因成员校验失败或 KeyError 而 500。
    new_member_names = {member.name for member in new_members}
    updated_modules = [
        module.model_copy(update={
            "assignee_id": (
                module.assignee_id
                if module.assignee_id in new_member_names
                else None
            ),
        })
        for module in fp.plan.modules
    ]
    qa_matrix = assign_with_balance(fp.plan, new_members)

    assignments_by_task = {item.task_id: item for item in qa_matrix.assignments}
    updated_tasks = [
        task.model_copy(update={
            "assignee_id": (
                assignments_by_task[task.id].presenter
                if task.id in assignments_by_task else None
            ),
            "collaborator_ids": (
                ([assignments_by_task[task.id].qa_primary]
                 if assignments_by_task[task.id].qa_primary else [])
                + list(assignments_by_task[task.id].qa_support or [])
            ) if task.id in assignments_by_task else [],
        })
        for task in fp.plan.tasks
    ]
    updated_plan = fp.plan.model_copy(update={
        "tasks": updated_tasks,
        "modules": updated_modules,
    })

    timeline_assignments = {}
    for item in qa_matrix.assignments:
        people = [item.presenter] if item.presenter else []
        if item.qa_primary and item.qa_primary not in people:
            people.append(item.qa_primary)
        for supporter in item.qa_support or []:
            if supporter not in people:
                people.append(supporter)
        timeline_assignments[item.task_id] = people

    timeline = TimelineAgent().run(
        plan=updated_plan,
        deadline=fp.input.deadline.isoformat(),
        assignments=timeline_assignments,
        members=new_members,
    )
    updated_plan = sync_task_dates(updated_plan, timeline)

    return FullPlan(
        input=new_input,
        plan=updated_plan,
        timeline=timeline,
        qa_matrix=qa_matrix,
        report=fp.report.model_copy(update={
            "summary": "", "timeline_section": "",
            "qa_matrix_section": "", "risk_note": "",
        }),
        volunteer_pool=fp.volunteer_pool,
    )
