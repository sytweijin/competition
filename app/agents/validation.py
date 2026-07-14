"""
计划输出校验工具。
供 Planner、B4 编辑、Coordinator 共用，确保 task id 唯一、依赖指向存在、无环。
"""

from __future__ import annotations

from collections import deque

from app.models.schemas import PlanOutput, SubTask


class PlanValidationError(ValueError):
    """计划校验失败。"""


def validate_plan(plan: PlanOutput) -> PlanOutput:
    """校验并（必要时）修正一个计划，返回干净的计划。

    检查项：
    - 至少一个任务
    - task id 唯一
    - dependencies 只引用存在的 id（剔除悬空依赖）
    - 无依赖环（Kahn 检测，有环则抛 PlanValidationError）
    """
    tasks = plan.tasks
    if not tasks:
        raise PlanValidationError("计划没有任何任务")

    ids = [t.id for t in tasks]
    # 去重保护：若出现重复 id，按出现顺序加后缀
    seen: dict[str, int] = {}
    deduped: list[SubTask] = []
    for t in tasks:
        if t.id in seen:
            seen[t.id] += 1
            new_id = f"{t.id}_{seen[t.id]}"
        else:
            seen[t.id] = 0
            new_id = t.id
        deduped.append(t.model_copy(update={"id": new_id}))
    tasks = deduped
    valid_ids = {t.id for t in tasks}

    # 剔除悬空依赖
    cleaned: list[SubTask] = []
    for t in tasks:
        deps = [d for d in t.dependencies if d in valid_ids and d != t.id]
        cleaned.append(t.model_copy(update={"dependencies": deps}))
    tasks = cleaned

    # 环检测（Kahn）
    successors: dict[str, list[str]] = {t.id: [] for t in tasks}
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    for t in tasks:
        for dep in t.dependencies:
            successors[dep].append(t.id)
            in_degree[t.id] += 1
    queue = deque(tid for tid, d in in_degree.items() if d == 0)
    visited = 0
    while queue:
        tid = queue.popleft()
        visited += 1
        for s in successors[tid]:
            in_degree[s] -= 1
            if in_degree[s] == 0:
                queue.append(s)
    if visited != len(tasks):
        raise PlanValidationError("任务依赖中存在环，无法排期")

    return plan.model_copy(update={"tasks": tasks})