"""项目协作核心业务。

本模块不依赖 FastAPI 或页面状态。任何界面/协议适配层都应调用
这里，而不是复制任务编辑、分工或负载计算逻辑。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from app.agents.timeline import sync_task_dates
from app.agents.validation import (
    PlanValidationError, ensure_large_project_structure, validate_plan,
)
from app.coordinator import Coordinator
from app.models.schemas import (
    AssignmentInput, DraftOperation, FullPlan, ManualAssignmentRequest,
    PlanOutput, ProjectModule, QAAssignment, QAOutput, ReportOutput, SubTask,
    TaskParticipant, Volunteer,
)
from app.services.duration_estimator import (
    calibrate_plan_estimates, record_duration_feedback,
)


def _is_weekend(day: date) -> bool:
    """周六(5)或周日(6)。资源日历分摊负载时把周末视为空档。"""
    return day.weekday() >= 5


class ProjectServiceError(ValueError):
    pass


def generate_draft(inp: AssignmentInput, use_ai: bool = True) -> PlanOutput:
    if use_ai:
        return Coordinator().draft(inp)
    if inp.project_mode == "large_project":
        return ensure_large_project_structure(calibrate_plan_estimates(
            Coordinator._fallback_large_project_plan(inp, "快速模式")))
    return calibrate_plan_estimates(
        Coordinator._fallback_plan(inp, "快速模式"))


def mutate_draft(plan: PlanOutput, operations: list[DraftOperation]) -> PlanOutput:
    """以结构化指令修改草案；未来自然语言只需先转换成 DraftOperation。"""
    tasks = [t.model_copy(deep=True) for t in plan.tasks]
    modules = [m.model_copy(deep=True) for m in plan.modules]
    has_module_structure = bool(modules) or any(t.module_id for t in tasks)

    for operation in operations:
        by_id = {t.id: t for t in tasks}
        if operation.op == "add":
            task = operation.task
            if task is None:
                next_no = max((t.order for t in tasks), default=0) + 1
                task = SubTask(
                    id=_next_id(tasks), name="新任务", description="",
                    estimated_hours=2, order=next_no)
            if task.id in by_id:
                raise ProjectServiceError(f"任务 ID 已存在：{task.id}")
            if has_module_structure and not task.module_id:
                module_id = operation.module_id or (modules[0].id if modules else None)
                if module_id:
                    task = task.model_copy(update={"module_id": module_id})
            tasks.append(task)
        elif operation.op == "update":
            if operation.task_id not in by_id or operation.task is None:
                raise ProjectServiceError("修改任务时必须提供有效 task_id 和 task")
            original = by_id[operation.task_id]
            updated = operation.task
            if not updated.module_id:
                updated = updated.model_copy(update={
                    "module_id": operation.module_id or original.module_id,
                })
            if record_duration_feedback(original, updated):
                updated = updated.model_copy(update={
                    "estimate_reason": (
                        f"用户已将知识库建议的 {original.estimated_hours:g}h"
                        f"调整为 {updated.estimated_hours:g}h；本次采用用户值。"),
                    "estimate_confidence": "用户已确认",
                })
            tasks = [updated if t.id == operation.task_id else t for t in tasks]
        elif operation.op == "remove":
            if operation.task_id not in by_id:
                raise ProjectServiceError(f"任务不存在：{operation.task_id}")
            tasks = [t for t in tasks if t.id != operation.task_id]
            tasks = [t.model_copy(update={
                "dependencies": [d for d in t.dependencies if d != operation.task_id]
            }) for t in tasks]
        elif operation.op == "split":
            original = by_id.get(operation.task_id)
            if original is None:
                raise ProjectServiceError(f"任务不存在：{operation.task_id}")
            replacements = operation.tasks or _default_split(original, tasks)
            replacements = [
                t.model_copy(update={"module_id": operation.module_id or original.module_id})
                for t in replacements
            ]
            tasks = [t for t in tasks if t.id != original.id]
            insert_at = min(original.order, len(tasks))
            for offset, task in enumerate(replacements):
                tasks.insert(insert_at + offset, task)
            replacement_ids = [t.id for t in replacements]
            tasks = [t.model_copy(update={
                "dependencies": _replace_dependency(
                    t.dependencies, original.id, replacement_ids[-1])
            }) for t in tasks]
        elif operation.op == "merge":
            ids = operation.task_ids
            selected = [t for t in tasks if t.id in ids]
            if len(selected) < 2:
                raise ProjectServiceError("合并任务至少需要两项")
            merged = operation.task or _default_merge(selected)
            if not merged.module_id:
                merged = merged.model_copy(update={"module_id": selected[0].module_id})
            tasks = [t for t in tasks if t.id not in ids]
            tasks.append(merged)
            tasks = [t.model_copy(update={
                "dependencies": _merge_dependencies(t.dependencies, ids, merged.id)
            }) for t in tasks]
        elif operation.op == "reorder":
            if set(operation.ordered_ids) != {t.id for t in tasks}:
                raise ProjectServiceError("排序列表必须包含全部任务且不能重复")
            order = {task_id: index + 1 for index, task_id in enumerate(operation.ordered_ids)}
            tasks = [t.model_copy(update={"order": order[t.id]}) for t in tasks]
        elif operation.op == "add_module":
            by_module = {m.id: m for m in modules}
            if operation.module is None:
                module = ProjectModule(
                    id=_next_module_id(modules), name="新模块", description="",
                    order=len(modules) + 1)
            else:
                module = operation.module
                if not module.id:
                    module = module.model_copy(update={"id": _next_module_id(modules)})
                if module.id in by_module:
                    raise ProjectServiceError(f"模块 ID 已存在：{module.id}")
                module = module.model_copy(update={"order": len(modules) + 1})
            modules.append(module)
        elif operation.op == "update_module":
            by_module = {m.id: m for m in modules}
            if operation.module is None or operation.module_id not in by_module:
                raise ProjectServiceError("修改模块时必须提供有效 module_id 和 module")
            module = operation.module.model_copy(update={
                "id": operation.module_id,
                "order": by_module[operation.module_id].order,
            })
            modules = [module if m.id == operation.module_id else m for m in modules]
        elif operation.op == "remove_module":
            if operation.module_id not in {m.id for m in modules}:
                raise ProjectServiceError(f"模块不存在：{operation.module_id}")
            if any(t.module_id == operation.module_id for t in tasks):
                raise ProjectServiceError("请先移动或删除该模块下的子任务，再删除模块")
            modules = [m for m in modules if m.id != operation.module_id]
        elif operation.op == "reorder_modules":
            if set(operation.ordered_module_ids) != {m.id for m in modules}:
                raise ProjectServiceError("模块排序列表必须包含全部模块且不能重复")
            order = {mid: index + 1 for index, mid in enumerate(operation.ordered_module_ids)}
            modules = [m.model_copy(update={"order": order[m.id]}) for m in modules]
        elif operation.op == "merge_modules":
            merge_ids = operation.module_ids
            if len(merge_ids) < 2:
                raise ProjectServiceError("合并模块至少需要两个模块")
            merge_set = set(merge_ids)
            if not merge_set.issubset({m.id for m in modules}):
                raise ProjectServiceError("合并模块 ID 不存在")
            target = next(m for m in modules if m.id == merge_ids[0])
            others = [m for m in modules if m.id in merge_set and m.id != target.id]
            for m in others:
                merged_name = target.name if target.name else m.name
                merged_desc = " / ".join(d for d in [target.description, m.description] if d)
                target = target.model_copy(update={
                    "name": merged_name,
                    "description": merged_desc,
                })
            for t in tasks:
                if t.module_id in merge_set and t.module_id != target.id:
                    t = t.model_copy(update={"module_id": target.id})
            tasks = [
                t.model_copy(update={
                    "module_id": target.id if t.module_id in merge_set else t.module_id
                }) for t in tasks
            ]
            modules = [m for m in modules if m.id not in {oid for oid in merge_ids[1:]}]
            modules = [target if m.id == target.id else m for m in modules]
        else:
            raise ProjectServiceError(f"未知任务操作：{operation.op}")

    tasks.sort(key=lambda task: (task.order or 10**9, task.id))
    modules.sort(key=lambda module: (module.order or 10**9, module.id))
    tasks = [task.model_copy(update={"order": index + 1}) for index, task in enumerate(tasks)]
    try:
        checked = validate_plan(
            plan.model_copy(update={"tasks": tasks, "modules": modules}),
            preserve_empty_modules=True)
        if has_module_structure or modules:
            return ensure_large_project_structure(
                checked, preserve_empty_modules=True)
        return checked
    except PlanValidationError as exc:
        raise ProjectServiceError(str(exc)) from exc


def confirm_draft(
    inp: AssignmentInput,
    plan: PlanOutput,
    *,
    use_ai_reflection: bool = True,
) -> FullPlan:
    if inp.project_mode == "large_project":
        plan = ensure_large_project_structure(plan)
    try:
        checked = validate_plan(plan)
    except PlanValidationError as exc:
        raise ProjectServiceError(str(exc)) from exc
    return Coordinator().confirm(
        inp, checked, use_ai_reflection=use_ai_reflection)


def update_volunteer_pool(plan: FullPlan, volunteers: list[Volunteer]) -> FullPlan:
    """更新大型项目模式的志愿者招募池（整池替换式 upsert）。

    校验规则：
    - 仅 large_project 模式允许志愿者招募；
    - 志愿者姓名不能为空、不能与团队成员重名、池内不能重复；
    - task_id 必须存在且该任务确实需要外部志愿者；
    - 每个任务"待确认 + 已确认"人数不能超过需求。
    """
    if plan.input.project_mode != "large_project":
        raise ProjectServiceError("志愿者招募仅适用于大型项目模式")
    member_names = {member.name for member in plan.input.members}
    task_by_id = {task.id: task for task in plan.plan.tasks}
    seen_names: set[str] = set()
    active_counts: dict[str, int] = defaultdict(int)
    for volunteer in volunteers:
        name = (volunteer.name or "").strip()
        if not name:
            raise ProjectServiceError("志愿者姓名不能为空")
        if name in member_names:
            raise ProjectServiceError(f"志愿者姓名不能与团队成员重复：{name}")
        if name in seen_names:
            raise ProjectServiceError(f"志愿者姓名重复：{name}")
        seen_names.add(name)
        task = task_by_id.get(volunteer.task_id)
        if task is None:
            raise ProjectServiceError(f"任务不存在：{volunteer.task_id}")
        if (task.extra_helpers_needed or 0) <= 0:
            raise ProjectServiceError(f"任务 {task.id} 不需要招募志愿者")
        if volunteer.status not in ("待确认", "已确认", "已婉拒"):
            raise ProjectServiceError(f"志愿者状态不合法：{volunteer.status}")
        if volunteer.status != "已婉拒":
            active_counts[volunteer.task_id] += 1
            if active_counts[volunteer.task_id] > task.extra_helpers_needed:
                raise ProjectServiceError(
                    f"任务 {task.id} 已招募 {active_counts[volunteer.task_id]} 人，"
                    f"超过需求 {task.extra_helpers_needed} 人")
    return plan.model_copy(update={"volunteer_pool": list(volunteers)})


def record_task_actual(
    plan: FullPlan,
    task_id: str,
    actual_hours: float | None = None,
    actual_end_date=None,
) -> FullPlan:
    """记录任务实际工时/实际完成日期，并把明显偏差沉淀回工时知识库。"""
    original = next((t for t in plan.plan.tasks if t.id == task_id), None)
    if original is None:
        raise ProjectServiceError(f"任务不存在：{task_id}")
    updates: dict = {}
    if actual_hours is not None:
        updates["actual_hours"] = round(max(0.0, float(actual_hours)), 2)
    if actual_end_date is not None:
        updates["actual_end_date"] = actual_end_date
    updated = original.model_copy(update=updates)
    tasks = [updated if t.id == task_id else t for t in plan.plan.tasks]

    if (
        updated.actual_hours is not None
        and not updated.actual_feedback_recorded
        and abs(updated.estimated_hours - updated.actual_hours) >= 0.5
        and original.estimate_reason
    ):
        corrected = updated.model_copy(update={
            "estimated_hours": updated.actual_hours,
        })
        if record_duration_feedback(original, corrected):
            updated = updated.model_copy(update={"actual_feedback_recorded": True})
            tasks = [updated if t.id == task_id else t for t in tasks]

    return plan.model_copy(update={
        "plan": plan.plan.model_copy(update={"tasks": tasks}),
    })


def update_task_participants(
    plan: FullPlan,
    task_id: str,
    participants: list[dict],
) -> FullPlan:
    """保存任务级参与清单，并同步 assignee/collaborator/志愿者数量。"""
    task = next((t for t in plan.plan.tasks if t.id == task_id), None)
    if task is None:
        raise ProjectServiceError(f"任务不存在：{task_id}")
    normalized: list[TaskParticipant] = []
    for item in participants or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = str(item.get("role") or "执行成员").strip() or "执行成员"
        contribution = max(0.0, float(item.get("contribution_hours") or 0))
        is_volunteer = bool(item.get("is_volunteer"))
        normalized.append(TaskParticipant(
            name=name,
            role=role,
            contribution_hours=round(contribution, 2),
            is_volunteer=is_volunteer,
            status=str(item.get("status") or "已确认"),
        ))
    updates: dict = {"participants": normalized}
    if normalized:
        internals = [p for p in normalized if not p.is_volunteer]
        volunteers = [p for p in normalized if p.is_volunteer]
        updates["assignee_id"] = internals[0].name if internals else None
        updates["collaborator_ids"] = [p.name for p in internals[1:]]
        updates["extra_helpers_needed"] = len(volunteers)
    updated = task.model_copy(update=updates)
    tasks = [updated if t.id == task_id else t for t in plan.plan.tasks]
    volunteer_pool = [
        v for v in (plan.volunteer_pool or [])
        if v.task_id != task_id
    ]
    for participant in normalized:
        if participant.is_volunteer:
            volunteer_pool.append(Volunteer(
                name=participant.name,
                task_id=task_id,
                status=participant.status or "已确认",
                contact="",
                note="",
            ))
    return plan.model_copy(update={
        "plan": plan.plan.model_copy(update={"tasks": tasks}),
        "volunteer_pool": volunteer_pool,
    })


def apply_manual_assignment(req: ManualAssignmentRequest) -> FullPlan:
    """保存负责人/协作者并重算排期；Web 与未来对话指令共用。"""
    from app.agents.scoring import _work_from, skill_score
    from app.agents.timeline import TimelineAgent

    fp = req.plan
    member_map = {
        member.name: member for member in fp.input.members
        if "志愿者" not in member.role and "外部协作者" not in member.role
    }
    module_map = {module.id: module for module in fp.plan.modules}
    module_assignees = req.module_assignees or {}
    for module_id, owner in module_assignees.items():
        if module_id not in module_map:
            raise ProjectServiceError(f"模块不存在：{module_id}")
        if owner and owner not in member_map:
            raise ProjectServiceError(f"模块负责人不在团队成员中：{owner}")
    updated_modules = [
        module.model_copy(update={
            "assignee_id": module_assignees.get(module.id, module.assignee_id) or None,
        })
        for module in fp.plan.modules
    ]
    module_owner = {module.id: module.assignee_id for module in updated_modules}
    assignments: list[QAAssignment] = []
    updated_tasks: list[SubTask] = []
    for task in fp.plan.tasks:
        # 已完成任务保持占位，跳过验证与重分配
        if task.status == "completed":
            updated_tasks.append(task)
            assignments.append(QAAssignment(
                task_id=task.id, task_name=task.name, chapter="",
                presenter="(已完成)", qa_primary="", qa_support=[],
                score=0.0, reasoning="任务已完成",
            ))
            continue
        # 优先沿用任务已有负责人，并校验其是否仍在当前成员名单内
        owner = task.assignee_id if task.assignee_id in member_map else None
        if req.assignees.get(task.id):
            owner = req.assignees.get(task.id)
        if owner and owner not in member_map:
            owner = None
        # 骨干认领模块后，模块下未单独指定负责人的子任务默认归模块负责人
        if not owner and task.module_id:
            owner = module_owner.get(task.module_id)
        collaborators = [
            name for name in req.collaborators.get(task.id, task.collaborator_ids)
            if name in member_map and name != owner
        ]
        score = skill_score(member_map[owner], task.required_skills) if owner else 0.0
        assignments.append(QAAssignment(
            task_id=task.id, task_name=task.name, presenter=owner or "",
            qa_primary=collaborators[0] if collaborators else "",
            qa_support=collaborators[1:], score=score,
            reasoning="用户手动调整并确认" if owner else "尚未设置负责人"))
        updated_tasks.append(task.model_copy(update={
            "assignee_id": owner or None, "collaborator_ids": collaborators}))

    hours = {task.id: task.estimated_hours for task in updated_tasks}
    workload = _work_from(assignments, hours, fp.input.members)
    qa = QAOutput(assignments=assignments, workload=workload, note="用户确认的手动分工")
    assignment_map: dict[str, list[str]] = {}
    for item in assignments:
        people = [item.presenter] if item.presenter else []
        if item.qa_primary and item.qa_primary not in people:
            people.append(item.qa_primary)
        for supporter in item.qa_support or []:
            if supporter and supporter not in people:
                people.append(supporter)
        assignment_map[item.task_id] = people
    plan = fp.plan.model_copy(
        update={"tasks": updated_tasks, "modules": updated_modules})
    timeline = TimelineAgent().run(
        plan, fp.input.deadline.isoformat(), assignment_map, fp.input.members)
    plan = sync_task_dates(plan, timeline)
    # 基于实际负载和工期计算真实风险，而不是把 note 当作 risk_note
    risk_note = _build_manual_risk_note(plan, timeline, workload, fp.input.members)
    report = fp.report.model_copy(update={
        "summary": plan.summary,
        "qa_matrix_section": "\n".join(
            f"{item.task_name}：{item.presenter or '未分配'}" for item in assignments),
        "risk_note": risk_note,
    })
    return FullPlan(
        input=fp.input, plan=plan, timeline=timeline, qa_matrix=qa,
        report=report, volunteer_pool=fp.volunteer_pool, version=fp.version)


def _build_manual_risk_note(plan: PlanOutput, timeline, workload: dict,
                           members) -> str:
    """基于实际负载和工期计算真实风险提示，供手动分工后的报告使用。"""
    risks: list[str] = []
    # 1. 负载不均衡风险
    values = [v for v in workload.values() if v > 0]
    if values:
        avg = sum(values) / len(values)
        for name, hours in workload.items():
            if hours == 0:
                risks.append(f"{name} 尚未分配任务，可能影响全员参与度")
            elif hours > avg * 1.35 and hours - avg > 2:
                risks.append(f"{name} 负载偏重（{hours:g}h，高于团队平均 {avg:.1f}h）")
    # 2. 工期紧张风险
    total_hours = sum(t.estimated_hours for t in plan.tasks if t.status != "completed")
    capacity = sum(m.available_hours for m in members) or 1
    if total_hours > capacity * 1.1:
        risks.append(f"总工时 {total_hours:g}h 接近或超出团队产能 {capacity:g}h，建议缩减范围或延期")
    # 3. 关键路径风险
    if timeline.critical_path and len(timeline.critical_path) >= len(plan.tasks) * 0.7:
        risks.append("关键路径占比较高，任务串行依赖多，单点延期易影响整体")
    # 4. 未分配负责人
    unassigned = [t for t in plan.tasks if not t.assignee_id]
    if unassigned:
        risks.append(f"{len(unassigned)} 项任务尚未设置负责人")
    if not risks:
        return "当前分工未发现明显风险，负载分布和工期安排合理。"
    return "\n".join(risks)


def workload_snapshot(plan: FullPlan) -> dict:
    """统一的工作量与提示计算，避免页面自行复制业务规则。"""
    # 角色与志愿者折算为通用能力，不区分大/小项目。
    from datetime import date as _date
    from app.agents.scoring import (
        QA_PRIMARY_RATIO, QA_SUPPORT_RATIO)

    PROJECT_LEAD_OVERHEAD_RATIO = 0.10
    MODULE_LEAD_OVERHEAD_HOURS = 1.0
    VOLUNTEER_RATIO = 0.5

    qa_by_task = {a.task_id: a for a in (plan.qa_matrix.assignments
                                         if plan.qa_matrix else [])}
    member_map = {m.name: m for m in plan.input.members}
    work = {name: 0.0 for name in member_map}
    stage_work = {name: defaultdict(float) for name in member_map}
    counts = {name: 0 for name in member_map}
    assist_counts = {name: 0 for name in member_map}
    participant_tasks = {name: [] for name in member_map}
    warnings: list[str] = []
    active_tasks = [t for t in plan.plan.tasks if t.status != "completed"]
    active_total = sum(t.estimated_hours for t in active_tasks)

    volunteer_work: dict[str, float] = {}
    volunteer_stage: dict[str, defaultdict] = {}
    volunteer_counts: dict[str, int] = {}

    def as_date(value):
        if isinstance(value, _date):
            return value
        return _date.fromisoformat(str(value)[:10])

    def overlap(a, b):
        if not (a.start_date and a.end_date and b.start_date and b.end_date):
            return False
        return (as_date(a.start_date) <= as_date(b.end_date)
                and as_date(b.start_date) <= as_date(a.end_date))

    for task in active_tasks:
        participants = task.participants or []
        if participants:
            for participant in participants:
                name = participant.name
                hours = participant.contribution_hours or 0.0
                if name in work:
                    work[name] += hours
                    counts[name] += 1
                    stage_work[name][task.execution_stage] += hours
                    participant_tasks[name].append(task)
                else:
                    volunteer_work[name] = volunteer_work.get(name, 0.0) + hours
                    volunteer_counts[name] = volunteer_counts.get(name, 0) + 1
                    volunteer_stage.setdefault(
                        name, defaultdict(float))[task.execution_stage] += hours
            continue
        owner = task.assignee_id
        if not owner or owner not in work:
            warnings.append(f"{task.name} 尚未设置负责人")
            continue
        h = task.estimated_hours
        work[owner] += h
        counts[owner] += 1
        stage_work[owner][task.execution_stage] += h
        participant_tasks[owner].append(task)

        qa = qa_by_task.get(task.id)
        if qa is not None:
            collaborators = []
            if qa.qa_primary and qa.qa_primary not in (owner, ""):
                collaborators.append((qa.qa_primary, QA_PRIMARY_RATIO))
            for s in (qa.qa_support or []):
                if s not in (owner, qa.qa_primary) and s:
                    collaborators.append((s, QA_SUPPORT_RATIO))
        else:
            collaborators = [
                (c, QA_PRIMARY_RATIO) for c in (task.collaborator_ids or [])
                if c != owner]
        for cname, ratio in collaborators:
            if cname not in work:
                continue
            share = h * ratio
            work[cname] += share
            assist_counts[cname] += 1
            stage_work[cname][task.execution_stage] += share
            participant_tasks[cname].append(task)

        for volunteer in (plan.volunteer_pool or []):
            if volunteer.task_id != task.id or volunteer.status != "已确认":
                continue
            share = h * VOLUNTEER_RATIO
            volunteer_work[volunteer.name] = volunteer_work.get(volunteer.name, 0.0) + share
            volunteer_counts[volunteer.name] = volunteer_counts.get(volunteer.name, 0) + 1
            volunteer_stage.setdefault(
                volunteer.name, defaultdict(float))[task.execution_stage] += share

    module_owner_counts: dict[str, int] = defaultdict(int)
    for module in (plan.plan.modules or []):
        if module.assignee_id:
            module_owner_counts[module.assignee_id] += 1

    for member in plan.input.members:
        role = (member.role or "执行成员").strip()
        overhead = 0.0
        if role == "项目负责人":
            overhead += active_total * PROJECT_LEAD_OVERHEAD_RATIO
        if "骨干" in role or "模块负责人" in role:
            overhead += MODULE_LEAD_OVERHEAD_HOURS * module_owner_counts.get(member.name, 0)
        if overhead:
            work[member.name] += overhead
            stage_work[member.name]["统筹"] += overhead

    average = sum(work.values()) / max(1, len(work))
    warning_seen: set[str] = set()

    def add_warning(text):
        if text not in warning_seen:
            warning_seen.add(text)
            warnings.append(text)

    for name, member in member_map.items():
        if counts[name] == 0 and assist_counts[name] == 0:
            add_warning(f"{name} 尚未分配任务")
        if member.available_hours and work[name] > member.available_hours + 0.01:
            add_warning(f"{name} 负载 {work[name]:.1f}h 超过可用 {member.available_hours:.1f}h")
        hours = work[name]
        if hours > average * 1.35 and hours - average > 2:
            add_warning(f"{name} 总工时明显高于团队平均")
        for stage, stage_hours in stage_work[name].items():
            if stage_hours > max(8, average):
                stage_label = stage if str(stage).endswith("阶段") else f"{stage}阶段"
                add_warning(f"{name} 在{stage_label}任务较集中")

        tasks = participant_tasks[name]
        for task in tasks:
            if not (task.start_date and task.end_date):
                continue
            start, end = as_date(task.start_date), as_date(task.end_date)
            for unavailable in (member.unavailable_dates or []):
                if start <= as_date(unavailable) <= end:
                    add_warning(f"{name} 在 {task.name} 排期内有不可用日期（{as_date(unavailable)}）")
        for i in range(len(tasks)):
            for j in range(i + 1, len(tasks)):
                if tasks[i].id != tasks[j].id and overlap(tasks[i], tasks[j]):
                    add_warning(f"{name} 同时参与 {tasks[i].name} 和 {tasks[j].name}，排期重叠")

    total_load = sum(work.values()) + sum(volunteer_work.values())
    members_out = {}
    for name in work:
        members_out[name] = {
            "role": member_map[name].role or "执行成员",
            "task_count": counts[name],
            "assist_count": assist_counts[name],
            "total_hours": round(work[name], 2),
            "share": round(work[name] / max(1, total_load), 4),
            "stage_hours": dict(stage_work[name]),
        }
    volunteers_out = [
        {
            "name": name,
            "role": "志愿者 / 外部协作者",
            "task_count": volunteer_counts.get(name, 0),
            "total_hours": round(volunteer_work[name], 2),
            "share": round(volunteer_work[name] / max(1, total_load), 4),
            "stage_hours": dict(volunteer_stage.get(name, {})),
        }
        for name in sorted(volunteer_work, key=lambda n: -volunteer_work[n])
    ]
    return {
        "members": members_out,
        "volunteers": volunteers_out,
        "average_hours": round(average, 2),
        "warnings": warnings,
    }


def resource_calendar(plan: FullPlan) -> dict:
    """资源日历：把任务工时按天摊到参与者，检测每日超载与不可用日期冲突。"""
    from app.agents.scoring import QA_PRIMARY_RATIO, QA_SUPPORT_RATIO

    member_map = {m.name: m for m in plan.input.members}
    active_tasks = [t for t in plan.plan.tasks if t.status != "completed"]

    def as_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    timeline_map = {}
    if plan.timeline and plan.timeline.tasks:
        for tl in plan.timeline.tasks:
            timeline_map[tl.task_id] = tl
    dated = []
    for task in active_tasks:
        tl = timeline_map.get(task.id)
        # 时间线是排期的事实来源：优先使用时间线日期，任务自身日期仅在
        # 时间线缺失时兜底（如旧存档或手动指定排期的场景）。
        if tl is not None:
            start = getattr(tl, "start_date", None)
            end = getattr(tl, "end_date", None)
        else:
            start = task.start_date
            end = task.end_date
        if start and end:
            dated.append(task.model_copy(update={
                "start_date": as_date(start),
                "end_date": as_date(end),
            }))
    if not dated:
        return {
            "days": [],
            "members": {},
            "volunteers": {},
            "warnings": ["暂无带排期的任务"],
        }

    min_date = min(as_date(t.start_date) for t in dated)
    max_date = max(as_date(t.end_date) for t in dated)
    days = [
        min_date + timedelta(days=i)
        for i in range((max_date - min_date).days + 1)
    ]
    day_keys = [d.isoformat() for d in days]
    member_load = {name: {k: 0.0 for k in day_keys} for name in member_map}
    volunteer_load: dict[str, dict] = {}
    member_tasks = {name: [] for name in member_map}
    volunteer_tasks: dict[str, list] = {}
    warnings: list[str] = []

    def participants_for(task):
        if task.participants:
            return [
                (p.name, p.contribution_hours or 0.0, p.is_volunteer)
                for p in task.participants
            ]
        out = []
        if task.assignee_id:
            out.append((task.assignee_id, task.estimated_hours or 0.0, False))
        qa = None
        if plan.qa_matrix:
            qa = next(
                (a for a in plan.qa_matrix.assignments if a.task_id == task.id),
                None,
            )
        if qa is not None:
            collabs = []
            if qa.qa_primary and qa.qa_primary != task.assignee_id:
                collabs.append((qa.qa_primary, QA_PRIMARY_RATIO))
            for s in (qa.qa_support or []):
                if s != task.assignee_id and s != qa.qa_primary:
                    collabs.append((s, QA_SUPPORT_RATIO))
            for cname, ratio in collabs:
                out.append((cname, (task.estimated_hours or 0.0) * ratio, False))
        else:
            for cname in (task.collaborator_ids or []):
                if cname != task.assignee_id:
                    out.append(
                        (cname, (task.estimated_hours or 0.0) * QA_PRIMARY_RATIO, False)
                    )
        for volunteer in (plan.volunteer_pool or []):
            if volunteer.task_id == task.id and volunteer.status == "已确认":
                out.append(
                    (volunteer.name, (task.estimated_hours or 0.0) * 0.5, True)
                )
        return out

    for task in dated:
        start = as_date(task.start_date)
        end = as_date(task.end_date)
        calendar_span = max(1, (end - start).days + 1)
        for name, hours, is_vol in participants_for(task):
            # 每个参与者只在“真正可用的工作日”上分摊工时：周末与其本人
            # 不可用日算作空档，不产生负载。日历窗口可能因这些空档被拉长，
            # 但成员当天不应显示任何任务，否则就会出现“不可用日仍有任务”。
            if is_vol or name not in member_map:
                member = None
            else:
                member = member_map.get(name)
            unavailable = set(member.unavailable_dates or []) if member else set()
            workday_keys = [
                (start + timedelta(days=i)).isoformat()
                for i in range(calendar_span)
                if not _is_weekend(start + timedelta(days=i))
                and (start + timedelta(days=i)) not in unavailable
            ]
            daily = hours / len(workday_keys) if workday_keys else 0.0
            for i in range(calendar_span):
                key = (start + timedelta(days=i)).isoformat()
                if key not in day_keys or key not in workday_keys:
                    continue
                if is_vol or name not in member_map:
                    volunteer_load.setdefault(name, {k: 0.0 for k in day_keys})
                    volunteer_load[name][key] += daily
                    if not any(x["id"] == task.id for x in volunteer_tasks.setdefault(name, [])):
                        volunteer_tasks[name].append({
                            "id": task.id,
                            "name": task.name,
                            "start": task.start_date.isoformat(),
                            "end": task.end_date.isoformat(),
                            "hours": round(hours, 2),
                        })
                else:
                    member_load[name][key] += daily
                    if not any(x["id"] == task.id for x in member_tasks[name]):
                        member_tasks[name].append({
                            "id": task.id,
                            "name": task.name,
                            "start": task.start_date.isoformat(),
                            "end": task.end_date.isoformat(),
                            "hours": round(hours, 2),
                        })

    for name, member in member_map.items():
        for key in day_keys:
            load = member_load[name][key]
            if load <= 0:
                continue
            day = as_date(key)
            if day in (member.unavailable_dates or []):
                warnings.append(f"{key} {name} 在不可用日期仍有任务（{load:.1f}h）")
            available = member.daily_available_hours or 0
            if load > available + 0.01:
                warnings.append(
                    f"{key} {name} 每日负载 {load:.1f}h 超过可用 {available:.1f}h"
                )

    return {
        "days": day_keys,
        "members": {
            name: {
                "role": member.role or "执行成员",
                "daily_available_hours": member.daily_available_hours,
                "unavailable_dates": [str(d) for d in (member.unavailable_dates or [])],
                "daily_load": member_load[name],
                "tasks": member_tasks[name],
            }
            for name, member in member_map.items()
        },
        "volunteers": {
            name: {
                "daily_load": volunteer_load[name],
                "tasks": volunteer_tasks[name],
            }
            for name in volunteer_load
        },
        "warnings": warnings,
    }


def _next_id(tasks: list[SubTask]) -> str:
    used = {task.id for task in tasks}
    number = 1
    while f"T{number}" in used:
        number += 1
    return f"T{number}"


def _next_module_id(modules: list[ProjectModule]) -> str:
    used = {module.id for module in modules}
    number = 1
    while f"M{number}" in used:
        number += 1
    return f"M{number}"


def _default_split(task: SubTask, existing: list[SubTask]) -> list[SubTask]:
    first_id = _next_id(existing)
    second_id = _next_id(existing + [task.model_copy(update={"id": first_id})])
    hours = max(0.5, round(task.estimated_hours / 2, 2))
    first = task.model_copy(update={
        "id": first_id, "name": f"{task.name}（第一部分）",
        "estimated_hours": hours})
    second = task.model_copy(update={
        "id": second_id, "name": f"{task.name}（第二部分）",
        "estimated_hours": max(0.5, round(task.estimated_hours - hours, 2)),
        "dependencies": [first_id]})
    return [first, second]


def _default_merge(tasks: list[SubTask]) -> SubTask:
    first = min(tasks, key=lambda task: task.order)
    ids = {task.id for task in tasks}
    dependencies = list(dict.fromkeys(
        dependency for task in tasks for dependency in task.dependencies
        if dependency not in ids))
    return first.model_copy(update={
        "name": " + ".join(task.name for task in tasks),
        "description": "；".join(task.description for task in tasks if task.description),
        "estimated_hours": sum(task.estimated_hours for task in tasks),
        "required_skills": list(dict.fromkeys(
            skill for task in tasks for skill in task.required_skills)),
        "dependencies": dependencies,
    })


def _replace_dependency(dependencies: list[str], old: str, new: str) -> list[str]:
    return list(dict.fromkeys(new if dependency == old else dependency
                              for dependency in dependencies))


def _merge_dependencies(dependencies: list[str], old_ids: list[str], new_id: str) -> list[str]:
    return list(dict.fromkeys(new_id if dependency in old_ids else dependency
                              for dependency in dependencies if dependency != new_id))


def recompute_plan(plan: FullPlan) -> FullPlan:
    """基于任务状态/成员变动重新计算时间线和匹配（不重跑 LLM）。

    供工作台 /api/recompute 与成员轻量汇报页共用，保证两处行为一致。
    """
    from app.agents.scoring import recompute_preserve
    from app.agents.timeline import TimelineAgent

    members = plan.input.members
    qa_matrix = recompute_preserve(plan.plan, plan.qa_matrix, members)

    # 回填负责人，重算 Timeline（会读取 task.status）
    assignments: dict[str, list[str]] = {}
    for a in qa_matrix.assignments:
        people = [a.presenter] if a.presenter else []
        if a.qa_primary and a.qa_primary not in people:
            people.append(a.qa_primary)
        for s in (a.qa_support or []):
            if s not in people:
                people.append(s)
        assignments[a.task_id] = people

    timeline = TimelineAgent().run(
        plan=plan.plan,
        deadline=plan.input.deadline.isoformat(),
        assignments=assignments,
        members=members,
    )

    # 状态切换是高频操作，只用本地结果更新报告，避免等待 LLM。
    risk_note = Coordinator._build_risk_note(
        plan.plan, timeline, qa_matrix, members, plan.input.deadline)
    report = plan.report.model_copy(update={
        "timeline_section": timeline.note,
        "qa_matrix_section": "\n".join(
            f"{item.task_name}：{item.presenter or '未分配'}"
            for item in qa_matrix.assignments),
        "risk_note": risk_note,
    })

    return FullPlan(
        input=plan.input,
        plan=plan.plan,
        timeline=timeline,
        qa_matrix=qa_matrix,
        report=report,
        volunteer_pool=plan.volunteer_pool,
    )
