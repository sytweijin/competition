"""
计划输出校验工具。
供 Planner、B4 编辑、Coordinator 共用，确保 task id 唯一、依赖指向存在、无环。
"""

from __future__ import annotations

from collections import Counter, deque

from app.models.schemas import PlanOutput, ProjectModule, SubTask


class PlanValidationError(ValueError):
    """计划校验失败。"""


def validate_plan(
    plan: PlanOutput,
    tolerate_cycle: bool = True,
    preserve_empty_modules: bool = False,
) -> PlanOutput:
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
        cyclic_ids = {tid for tid in in_degree if in_degree[tid] > 0}
        if not tolerate_cycle:
            raise PlanValidationError("任务依赖中存在环，无法排期")
        # 环容错：断开仍在环中的任务的入环依赖（保留任务本身，不丢弃 LLM 结果）
        tasks = [
            t.model_copy(update={"dependencies": []})
            if t.id in cyclic_ids else t
            for t in tasks
        ]

    # 模块校验：大型项目草案中的任务必须归属某个存在的模块，孤儿模块移除。
    modules = [m.model_copy(deep=True) for m in plan.modules]
    has_module_data = bool(modules) or any(t.module_id for t in tasks)
    if has_module_data:
        seen_module: dict[str, int] = {}
        deduped_modules: list[ProjectModule] = []
        for module in modules:
            if module.id in seen_module:
                seen_module[module.id] += 1
                module = module.model_copy(update={"id": f"{module.id}_{seen_module[module.id]}"})
            else:
                seen_module[module.id] = 0
            deduped_modules.append(module)
        modules = deduped_modules
        valid_module_ids = {m.id for m in modules}
        tasks = [
            t.model_copy(update={
                "module_id": t.module_id if t.module_id in valid_module_ids else None
            })
            for t in tasks
        ]
        used_module_ids = {t.module_id for t in tasks if t.module_id}
        modules = [
            m.model_copy(update={
                "order": index + 1,
                "status": _module_status_for(m, tasks),
            })
            for index, m in enumerate(modules)
            if preserve_empty_modules or m.id in used_module_ids
        ]

    return plan.model_copy(update={"tasks": tasks, "modules": modules})


def ensure_large_project_structure(
    plan: PlanOutput,
    preserve_empty_modules: bool = False,
) -> PlanOutput:
    """为大型项目草案补齐模块结构，保证「模块 → 子任务」层级完整。

    - 没有模块时按执行阶段分组，但模块名从子任务的技能/名称推导；
    - 有模块但存在未归属子任务时，把它们归入最匹配的模块；
    - 默认清理没有任何子任务的孤儿模块；用户手工新增模块时保留空模块。
    """
    tasks = [t.model_copy(deep=True) for t in plan.tasks]
    modules = [m.model_copy(deep=True) for m in plan.modules]
    module_ids = {m.id for m in modules}

    unassigned = [t for t in tasks if not t.module_id or t.module_id not in module_ids]
    if not modules and tasks:
        # 按执行阶段分组；同一阶段数量过多时切成不超过 4 项的小块。
        stage_order = ("准备", "执行", "收尾", "自定义", "其他")
        grouped: dict[str, list[SubTask]] = {}
        for task in tasks:
            stage = task.execution_stage or "其他"
            grouped.setdefault(stage, []).append(task)
        new_modules: list[ProjectModule] = []
        task_module: dict[str, str] = {}
        for stage in stage_order:
            stage_tasks = grouped.pop(stage, [])
            for chunk_index in range(0, len(stage_tasks), 4):
                chunk = stage_tasks[chunk_index:chunk_index + 4]
                module_id = f"M{len(new_modules) + 1}"
                new_modules.append(ProjectModule(
                    id=module_id,
                    name=_derive_module_name_from_tasks(chunk, stage),
                    description=_derive_module_description_from_tasks(chunk, stage),
                    order=len(new_modules) + 1,
                ))
                for task in chunk:
                    task_module[task.id] = module_id
        for stage, stage_tasks in grouped.items():
            for chunk_index in range(0, len(stage_tasks), 4):
                chunk = stage_tasks[chunk_index:chunk_index + 4]
                module_id = f"M{len(new_modules) + 1}"
                new_modules.append(ProjectModule(
                    id=module_id,
                    name=_derive_module_name_from_tasks(chunk, stage),
                    description=_derive_module_description_from_tasks(chunk, stage),
                    order=len(new_modules) + 1,
                ))
                for task in chunk:
                    task_module[task.id] = module_id
        modules = new_modules
        tasks = [
            t.model_copy(update={"module_id": task_module.get(t.id, t.module_id)})
            for t in tasks
        ]
    elif unassigned:
        # 有模块但存在未归属任务：优先并入执行阶段模块，否则并入第一个模块。
        fallback_module = next(
            (m for m in modules if (m.name or "").find("执行") >= 0),
            modules[0] if modules else None)
        if fallback_module is None:
            fallback_module = ProjectModule(
                id="M1", name="其他任务", description="未归入既有模块的子任务。",
                order=1)
            modules = [fallback_module] + modules
        tasks = [
            t.model_copy(update={
                "module_id": fallback_module.id
                if not t.module_id or t.module_id not in module_ids
                else t.module_id
            })
            for t in tasks
        ]

    used_ids = {t.module_id for t in tasks if t.module_id}
    modules = [
        m.model_copy(update={"order": index + 1})
        for index, m in enumerate(modules)
        if preserve_empty_modules or m.id in used_ids
    ]
    module_order = {m.id: m.order or index + 1 for index, m in enumerate(modules)}
    tasks = sorted(tasks, key=lambda t: (t.order or 10**9, t.id))
    tasks = [
        t.model_copy(update={
            "order": index + 1,
            "module_id": t.module_id if t.module_id in module_order else (
                next(iter(module_order), None)),
        })
        for index, t in enumerate(tasks)
    ]
    return plan.model_copy(update={"tasks": tasks, "modules": modules})


def _module_status_for(module: ProjectModule, tasks: list[SubTask]) -> str:
    module_tasks = [t for t in tasks if t.module_id == module.id]
    if not module_tasks:
        return module.status
    if all(t.status == "completed" for t in module_tasks):
        return "completed"
    if any(t.status == "in_progress" for t in module_tasks):
        return "in_progress"
    return module.status


# ── 大型项目自动生成模块时，从子任务推导有意义的模块名 ──

# 技能规范名 → 模块领域名
_DOMAIN_BY_CANONICAL_SKILL = {
    "调研分析": "调研与分析",
    "文案撰写": "文案与内容创作",
    "内容策划": "策划与方案设计",
    "平面设计": "视觉设计与物料制作",
    "视频剪辑": "视频与影像制作",
    "PPT制作": "演示材料制作",
    "排版设计": "排版与发布",
    "数据分析": "数据分析与可视化",
    "Python编程": "技术开发",
    "前端开发": "技术开发",
    "后端开发": "技术开发",
    "组织执行": "组织与实施推进",
    "质量审核": "审核与质量保障",
}

# 执行阶段 → 兜底模块名（仅在技能推导全部失败时使用）
_DOMAIN_BY_STAGE = {
    "准备": "前期准备与规划",
    "执行": "核心实施与推进",
    "收尾": "收尾与成果整合",
    "自定义": "自定义工作",
    "其他": "其他工作",
}


def _derive_module_name_from_tasks(tasks: list[SubTask], stage: str = "") -> str:
    """从一组子任务的技能和名称推断模块名。

    优先按技能规范词投票出领域名（如「调研与分析」），其次用任务名
    关键词拼接，最后才回退到执行阶段兜底名。
    """
    from app.agents.scoring import _normalize_tag, _SKILL_SYNONYMS

    domain_counts: Counter[str] = Counter()
    for task in tasks:
        for raw_skill in (task.required_skills or []):
            canonical = _SKILL_SYNONYMS.get(_normalize_tag(raw_skill), raw_skill)
            domain = _DOMAIN_BY_CANONICAL_SKILL.get(canonical)
            if domain:
                domain_counts[domain] += 1
    if domain_counts:
        return domain_counts.most_common(1)[0][0]

    # 无技能命中：从任务名提取关键词
    names = [t.name.strip() for t in tasks if t.name and t.name.strip()]
    if len(names) == 1:
        return names[0][:14]
    if len(names) >= 2:
        combined = f"{names[0]}与{names[1]}"
        return combined[:16]

    return _DOMAIN_BY_STAGE.get(stage, "工作模块")


def _derive_module_description_from_tasks(
        tasks: list[SubTask], stage: str = "") -> str:
    """生成描述：列出该模块包含的具体子任务。"""
    names = [t.name.strip() for t in tasks if t.name and t.name.strip()[:3]]
    preview = "、".join(names[:3])
    suffix = f"等 {len(tasks)} 项子任务" if len(names) > 3 else ""
    stage_hint = f"（{stage}阶段）" if stage and stage != "其他" else ""
    return f"包含：{preview}{suffix}{stage_hint}，由骨干认领推进。"
