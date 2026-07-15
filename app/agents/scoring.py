"""
B3：完整角色匹配 —— 基于技能标签的确定性评分引擎。

不依赖 LLM。用「技能覆盖度 + 负载均衡」为每个 (任务, 成员) 打分，
为 Matcher 的 LLM 输出补充可解释的 score 与 workload 负载摘要。
也能在 LLM 不可用时独立生成一份匹配。
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.models.schemas import (
    PlanOutput, TeamMember, QAOutput, QAAssignment,
)


def _normalize_tag(tag: str) -> str:
    """标签归一化：去空格、转小写，便于精确匹配。"""
    return tag.strip().lower().replace(" ", "")


def _similar(a: str, b: str) -> float:
    """两个技能标签的相似度（大小写/空白不敏感，支持包含关系）。"""
    na, nb = _normalize_tag(a), _normalize_tag(b)
    if not na or not nb:
        return 0.0
    # 完全匹配
    if na == nb:
        return 1.0
    # 包含关系（如「前端」vs「前端开发」）给高分
    if na in nb or nb in na:
        return 0.85
    # 退化为字符相似度
    return SequenceMatcher(None, na, nb).ratio()


# 角色投入系数：主讲承担任务全部工时，主答辅助参与折算 30%，辅答各折算 15%。
# 说明：同一任务的工时按角色投入占比分配到各成员，避免重复计数导致负载虚高。
PRESENTER_RATIO = 1.0
QA_PRIMARY_RATIO = 0.3
QA_SUPPORT_RATIO = 0.15


def skill_score(member: TeamMember, required_skills: list[str]) -> float:
    """成员对所需技能的匹配分（0-1）。

    取每个所需技能的最佳匹配相似度后求均值；无所需技能则返回中性 0.5。
    """
    if not required_skills:
        return 0.5
    if not member.skill_tags:
        return 0.0
    total = 0.0
    for req in required_skills:
        best = max((_similar(req, tag) for tag in member.skill_tags),
                   default=0.0)
        total += best
    return round(total / len(required_skills), 3)


def rank_members(task_skills: list[str],
                 members: list[TeamMember]) -> list[tuple[TeamMember, float]]:
    """按匹配分降序返回 (成员, 分数) 列表。"""
    scored = [(m, skill_score(m, task_skills)) for m in members]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def assign_with_balance(plan: PlanOutput,
                        members: list[TeamMember]) -> QAOutput:
    """贪心 + 负载均衡地为每个任务分配主讲/主答/辅答。

    规则：
    - 按「技能分 - 已分配任务数惩罚」挑选主讲
    - 主答选技能分次高者，辅答为其余按分排序取前二
    - 同一人累计分配数越多，越难再被选中（负载均衡）
    """
    if not members or not plan.tasks:
        return QAOutput(assignments=[], note="成员或任务为空，无法匹配")

    load: dict[str, int] = {m.name: 0 for m in members}
    name_to_skills = {m.name: m.skill_tags for m in members}
    assignments: list[QAAssignment] = []

    for t in plan.tasks:
        if t.status == "completed":
            continue
        # 主讲：技能分 - 负载惩罚
        scored = [
            (m.name, skill_score(m, t.required_skills) - 0.25 * load[m.name])
            for m in members
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        presenter = scored[0][0]
        load[presenter] += 1

        # 主答：剩余成员里技能分最高
        rest = [(n, s) for n, s in scored if n != presenter]
        primary = rest[0][0] if rest else presenter
        if primary != presenter:
            load[primary] += 1

        # 辅答：再取两人
        rest2 = [(n, s) for n, s in rest if n not in (primary,)]
        support = [n for n, _ in rest2[:2]]

        best_score = scored[0][1] + 0.25 * load[presenter]
        reasoning = (
            f"{presenter} 技能标签 {_fmt(name_to_skills[presenter])} "
            f"与任务所需 {_fmt(t.required_skills)} 匹配度最高"
            f"（负载均衡后综合分 {best_score:.2f}）。"
        )
        assignments.append(QAAssignment(
            task_id=t.id,
            task_name=t.name,
            chapter="",
            presenter=presenter,
            qa_primary=primary,
            qa_support=support,
            score=round(max(0.0, min(1.0, best_score)), 3),
            reasoning=reasoning,
        ))

    # workload 摘要：折算工时（主讲=任务工时，主答=0.3，辅答=0.15）
    work: dict[str, float] = {m.name: 0.0 for m in members}
    task_hours = {t.id: t.estimated_hours for t in plan.tasks}
    for a in assignments:
        h = task_hours.get(a.task_id, 0.0)
        work[a.presenter] = work.get(a.presenter, 0.0) + h
        if a.qa_primary and a.qa_primary != a.presenter:
            work[a.qa_primary] = work.get(a.qa_primary, 0.0) + h * QA_PRIMARY_RATIO
        for s in a.qa_support:
            work[s] = work.get(s, 0.0) + h * QA_SUPPORT_RATIO

    # Overload detection
    overload_warnings = []
    member_map = {m.name: m for m in members}
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            overload_warnings.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h"
            )
    note = "基于技能匹配 + 负载均衡的确定性分配（B3）"
    if overload_warnings:
        note += "；警告：" + "；".join(overload_warnings)

    return QAOutput(assignments=assignments, workload=work, note=note)


def _fmt(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "无"


def enhance(qa: QAOutput, plan: PlanOutput,
            members: list[TeamMember]) -> QAOutput:
    """对 Matcher(LLM) 的输出做后处理：补 score、补 workload。

    保留 LLM 的人选，只补可解释的数值字段。
    """
    work: dict[str, float] = {m.name: 0.0 for m in members}
    task_hours = {t.id: t.estimated_hours for t in plan.tasks}
    member_map = {m.name: m for m in members}
    task_map = {t.id: t for t in plan.tasks}

    enhanced: list[QAAssignment] = []
    for a in qa.assignments:
        t = task_map.get(a.task_id)
        h = task_hours.get(a.task_id, 0.0)
        # 折算工时到 workload
        work[a.presenter] = work.get(a.presenter, 0.0) + h
        if a.qa_primary and a.qa_primary != a.presenter:
            work[a.qa_primary] = work.get(a.qa_primary, 0.0) + h * QA_PRIMARY_RATIO
        for s in a.qa_support:
            work[s] = work.get(s, 0.0) + h * QA_SUPPORT_RATIO
        # 补 score
        score = a.score
        if score == 0.0 and t is not None and a.presenter in member_map:
            score = skill_score(member_map[a.presenter], t.required_skills)
        enhanced.append(a.model_copy(update={"score": round(score, 3)}))

    # 负载失衡/超载检测，补充到 note
    imbalance = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            imbalance.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h")
    note = qa.note or ""
    if imbalance:
        note += "；负载警告：" + "；".join(imbalance)
    return qa.model_copy(update={
        "assignments": enhanced, "workload": work, "note": note})