"""项目协作核心业务。

本模块不依赖 FastAPI、页面状态或清小搭协议。任何界面/协议适配层都应调用
这里，而不是复制任务编辑、分工或负载计算逻辑。
"""

from __future__ import annotations

from collections import defaultdict

from app.agents.validation import PlanValidationError, validate_plan
from app.coordinator import Coordinator
from app.models.schemas import (
    AssignmentInput, DraftOperation, FullPlan, ManualAssignmentRequest,
    PlanOutput, QAAssignment, QAOutput, ReportOutput, SubTask,
)
from app.services.duration_estimator import (
    calibrate_plan_estimates, record_duration_feedback,
)


class ProjectServiceError(ValueError):
    pass


def generate_draft(inp: AssignmentInput, use_ai: bool = True) -> PlanOutput:
    if use_ai:
        return Coordinator().draft(inp)
    return calibrate_plan_estimates(
        Coordinator._fallback_plan(inp, "快速模式"))


def mutate_draft(plan: PlanOutput, operations: list[DraftOperation]) -> PlanOutput:
    """以结构化指令修改草案；未来自然语言只需先转换成 DraftOperation。"""
    tasks = [t.model_copy(deep=True) for t in plan.tasks]

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
            tasks.append(task)
        elif operation.op == "update":
            if operation.task_id not in by_id or operation.task is None:
                raise ProjectServiceError("修改任务时必须提供有效 task_id 和 task")
            original = by_id[operation.task_id]
            updated = operation.task
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
        else:
            raise ProjectServiceError(f"未知任务操作：{operation.op}")

    tasks.sort(key=lambda task: (task.order or 10**9, task.id))
    tasks = [task.model_copy(update={"order": index + 1}) for index, task in enumerate(tasks)]
    try:
        return validate_plan(plan.model_copy(update={"tasks": tasks}))
    except PlanValidationError as exc:
        raise ProjectServiceError(str(exc)) from exc


def confirm_draft(inp: AssignmentInput, plan: PlanOutput) -> FullPlan:
    try:
        checked = validate_plan(plan)
    except PlanValidationError as exc:
        raise ProjectServiceError(str(exc)) from exc
    return Coordinator().confirm(inp, checked)


def apply_manual_assignment(req: ManualAssignmentRequest) -> FullPlan:
    """保存负责人/协作者并重算排期；Web 与未来对话指令共用。"""
    from app.agents.scoring import _work_from, skill_score
    from app.agents.timeline import TimelineAgent

    fp = req.plan
    member_map = {member.name: member for member in fp.input.members}
    assignments: list[QAAssignment] = []
    updated_tasks: list[SubTask] = []
    for task in fp.plan.tasks:
        owner = req.assignees.get(task.id, task.assignee_id or "")
        if owner and owner not in member_map:
            raise ProjectServiceError(f"未知负责人：{owner}")
        collaborators = [
            name for name in req.collaborators.get(task.id, task.collaborator_ids)
            if name in member_map and name != owner
        ]
        score = skill_score(member_map[owner], task.required_skills) if owner else 0.0
        assignments.append(QAAssignment(
            task_id=task.id, task_name=task.name, presenter=owner,
            qa_primary=collaborators[0] if collaborators else "",
            qa_support=collaborators[1:], score=score,
            reasoning="用户手动调整并确认" if owner else "尚未设置负责人"))
        updated_tasks.append(task.model_copy(update={
            "assignee_id": owner or None, "collaborator_ids": collaborators}))

    hours = {task.id: task.estimated_hours for task in updated_tasks}
    workload = _work_from(assignments, hours, fp.input.members)
    qa = QAOutput(assignments=assignments, workload=workload, note="用户确认的手动分工")
    assignment_map = {
        item.task_id: [item.presenter] + ([item.qa_primary] if item.qa_primary else [])
        for item in assignments
    }
    plan = fp.plan.model_copy(update={"tasks": updated_tasks})
    timeline = TimelineAgent().run(
        plan, fp.input.deadline.isoformat(), assignment_map, fp.input.members)
    report = fp.report.model_copy(update={
        "summary": plan.summary,
        "qa_matrix_section": "\n".join(
            f"{item.task_name}：{item.presenter or '未分配'}" for item in assignments),
        "risk_note": qa.note,
    })
    return FullPlan(
        input=fp.input, plan=plan, timeline=timeline, qa_matrix=qa,
        report=report, version=fp.version)


def workload_snapshot(plan: FullPlan) -> dict:
    """统一的工作量与提示计算，避免页面自行复制业务规则。"""
    work = {member.name: 0.0 for member in plan.input.members}
    stage_work = {member.name: defaultdict(float) for member in plan.input.members}
    counts = {member.name: 0 for member in plan.input.members}
    warnings: list[str] = []
    for task in plan.plan.tasks:
        owner = task.assignee_id
        if not owner or owner not in work:
            warnings.append(f"{task.name} 尚未设置负责人")
            continue
        # 已完成的任务不再计入剩余工作量——标记完成后成员条带应缩短
        if task.status == "completed":
            continue
        work[owner] += task.estimated_hours
        counts[owner] += 1
        stage_work[owner][task.execution_stage] += task.estimated_hours
    average = sum(work.values()) / max(1, len(work))
    for name, hours in work.items():
        if counts[name] == 0:
            warnings.append(f"{name} 尚未分配任务")
        if hours > average * 1.35 and hours - average > 2:
            warnings.append(f"{name} 总工时明显高于团队平均")
        for stage, stage_hours in stage_work[name].items():
            if stage_hours > max(8, average):
                stage_label = stage if str(stage).endswith("阶段") else f"{stage}阶段"
                warnings.append(f"{name} 在{stage_label}任务较集中")
    return {
        "members": {
            name: {
                "task_count": counts[name],
                "total_hours": round(work[name], 2),
                "share": round(work[name] / max(1, sum(work.values())), 4),
                "stage_hours": dict(stage_work[name]),
            } for name in work
        },
        "average_hours": round(average, 2),
        "warnings": warnings,
    }


def _next_id(tasks: list[SubTask]) -> str:
    used = {task.id for task in tasks}
    number = 1
    while f"T{number}" in used:
        number += 1
    return f"T{number}"


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
