"""
B4：协作图动态编辑。
负责人：B

对一个已生成的 FullPlan 应用一系列任务编辑（add/remove/update），
然后按需用 Timeline(CPM) 与 Matcher(B3) 重算，实现「计划随现实变化而重算」。
"""

from __future__ import annotations

import logging

from app.agents.scoring import assign_with_balance
from app.agents.timeline import TimelineAgent
from app.agents.validation import PlanValidationError, validate_plan
from app.models.schemas import (
    EditPlanRequest, FullPlan, PlanOutput, SubTask, TaskEdit,
)

logger = logging.getLogger(__name__)


class EditError(ValueError):
    """编辑操作非法。"""


def apply_edits(plan: PlanOutput, edits: list[TaskEdit]) -> PlanOutput:
    """对计划应用一系列编辑，返回新计划（未校验）。"""
    task_map: dict[str, SubTask] = {t.id: t for t in plan.tasks}

    for i, e in enumerate(edits):
        op = e.op.strip().lower()
        if op == "add":
            if e.task is None:
                raise EditError(f"edit#{i} add 缺少 task")
            if e.task.id in task_map:
                raise EditError(f"edit#{i} add: 任务 {e.task.id} 已存在")
            task_map[e.task.id] = e.task
        elif op == "remove":
            if not e.task_id or e.task_id not in task_map:
                raise EditError(f"edit#{i} remove: 任务 {e.task_id} 不存在")
            del task_map[e.task_id]
            # 清理指向它的依赖
            for t in task_map.values():
                if e.task_id in t.dependencies:
                    t = t.model_copy(
                        update={"dependencies": [d for d in t.dependencies
                                                 if d != e.task_id]})
                    task_map[t.id] = t
        elif op == "update":
            if e.task is None:
                raise EditError(f"edit#{i} update 缺少 task")
            tid = e.task_id or e.task.id
            if tid not in task_map:
                raise EditError(f"edit#{i} update: 任务 {tid} 不存在")
            # 保持原 id，更新其余字段
            new_task = e.task.model_copy(update={"id": tid})
            task_map[tid] = new_task
        else:
            raise EditError(f"edit#{i} 未知操作: {e.op}")

    return PlanOutput(
        tasks=list(task_map.values()),
        summary=plan.summary,
        reasoning=plan.reasoning + "（已应用编辑）",
    )


def edit_plan(req: EditPlanRequest) -> FullPlan:
    """应用编辑并重算，返回新的 FullPlan。"""
    original = req.plan
    new_plan = apply_edits(original.plan, req.edits)

    try:
        new_plan = validate_plan(new_plan)
    except PlanValidationError as e:
        raise EditError(f"编辑后计划非法：{e}") from e

    # 重算 timeline
    timeline = original.timeline
    if req.recompute_timeline:
        # 复用原 QA 分配回填负责人
        assignments: dict[str, list[str]] = {}
        for a in original.qa_matrix.assignments:
            people = [a.presenter] if a.presenter else []
            if a.qa_primary and a.qa_primary not in people:
                people.append(a.qa_primary)
            assignments[a.task_id] = people
        timeline = TimelineAgent().run(
            plan=new_plan,
            deadline=original.input.deadline.isoformat(),
            assignments=assignments,
            members=original.input.members,
        )

    # 重算 matcher（B3 确定性，保证编辑后即时可见）
    qa_matrix = original.qa_matrix
    if req.recompute_matcher:
        qa_matrix = assign_with_balance(new_plan, original.input.members)

    return FullPlan(
        input=original.input,
        plan=new_plan,
        timeline=timeline,
        qa_matrix=qa_matrix,
        report=original.report,  # 报告不自动重算，按需单独触发
        version="1.0",
    )