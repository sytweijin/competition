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
# 说明：同一任务的工时按角色投入占比折算到各成员（主讲1.0 + 主答0.3 + 辅答0.15/人），累计可能超过任务原工时。
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



def assign_with_balance(plan: PlanOutput,
                        members: list[TeamMember]) -> QAOutput:
    """确定性任务分配 + 负载均衡 v2.1

    负载差距 <= 1h 时停止调整
    """
    if not members or not plan.tasks:
        return QAOutput(assignments=[], note="B3确定性兜底+超载校正")

    active_tasks = [t for t in plan.tasks if t.status != "completed"]
    member_map = {m.name: m for m in members}
    all_task_map = {t.id: t for t in plan.tasks}
    task_hours = {t.id: t.estimated_hours for t in active_tasks}

    work = {m.name: 0.0 for m in members}
    assignments = []

    for t in plan.tasks:
        if t.status == "completed":
            assignments.append(QAAssignment(
                task_id=t.id, task_name=t.name, chapter="",
                presenter="(已完成)", qa_primary="", qa_support=[],
                score=0.0, reasoning="任务已完成",
            ))
            continue
        # 按匹配度降序，同分时负载轻者优先
        scored = [(m.name, skill_score(m, t.required_skills)) for m in members]
        scored.sort(key=lambda x: (-x[1], work[x[0]]))  # 取匹配度最高且负载最轻者
        presenter = scored[0][0]
        work[presenter] += t.estimated_hours

        # 主答取剩余中最优
        rest = [(n, s) for n, s in scored if n != presenter]
        rest.sort(key=lambda x: (-x[1], work[x[0]]))
        primary = rest[0][0] if rest else presenter
        if primary != presenter:
            work[primary] += t.estimated_hours * QA_PRIMARY_RATIO

        # 辅答取剩余中负载最轻的 2 人
        rest2 = [n for n, _ in rest if n != primary]
        rest2.sort(key=lambda n: work[n])
        support = rest2[:2]
        for s in support:
            work[s] += t.estimated_hours * QA_SUPPORT_RATIO

        best_skill = scored[0][1]
        reasoning = (
            f"{presenter} 的 {_fmt(t.required_skills)} 技能"
            f"匹配度 {best_skill:.2f}，综合最优"
        )
        assignments.append(QAAssignment(
            task_id=t.id, task_name=t.name, chapter="",
            presenter=presenter, qa_primary=primary, qa_support=support,
            score=round(best_skill, 3), reasoning=reasoning,
        ))

    # 负载差 > 1h 时继续均衡调整
    sorted_w = sorted(work.items(), key=lambda x: x[1])
    for _pass in range(30):
        if sorted_w[-1][1] - sorted_w[0][1] <= 0.5:
            break
        overloaded = sorted_w[-1][0]
        underloaded = sorted_w[0][0]
        # 找 overloaded 的最轻任务转移给 underloaded
        candidates = [(a, task_hours.get(a.task_id, 0)) for a in assignments
                      if a.presenter == overloaded]
        # 按与 gap/2 的接近程度排序
        gap = sorted_w[-1][1] - sorted_w[0][1]
        candidates.sort(key=lambda x: abs(x[1] - gap/2))  # 接近 gap/2 者优先
        swapped = False
        for a, h in candidates:
            if h <= 0:
                continue
            new_over = work[overloaded] - h
            new_under = work[underloaded] + h
            # 检查转移后负载是否更均衡
            if abs(new_over - new_under) < sorted_w[-1][1] - sorted_w[0][1]:
                a.presenter = underloaded
                work[overloaded] -= h
                work[underloaded] += h
                task = all_task_map.get(a.task_id)
                if task and underloaded in member_map:
                    new_score = skill_score(member_map[underloaded], task.required_skills)
                    a.score = round(new_score, 3)
                a.reasoning += f"已转给{underloaded}平衡负载"
                swapped = True
                break
        if not swapped:
            break
        sorted_w = sorted(work.items(), key=lambda x: x[1])

    # overload detection
    overload_warnings = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            overload_warnings.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h"
            )
    note = "B3确定性兜底 + 超载校正 v2.1"
    if overload_warnings:
        note += " 超载警告: " + "; ".join(overload_warnings)

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
            if s != a.presenter and s != a.qa_primary:
                work[s] = work.get(s, 0.0) + h * QA_SUPPORT_RATIO
        # 补 score
        score = a.score
        if score == 0.0 and t is not None and a.presenter in member_map:
            score = skill_score(member_map[a.presenter], t.required_skills)
        enhanced.append(a.model_copy(update={"score": round(score, 3)}))

    # 负载均衡转移：若最高最低差距 > 30%，转移主讲任务
    max_w = max(work.values()) if work else 1
    min_w = min(work.values()) if work else 0
    if max_w > min_w * 1.2:
        sorted_w = sorted(work.items(), key=lambda x: x[1])
        for _pass in range(50):
            if sorted_w[-1][1] - sorted_w[0][1] <= max_w * 0.15:
                break
            overloaded = sorted_w[-1][0]
            underloaded = sorted_w[0][0]
            candidates = [(a, task_hours.get(a.task_id, 0)) for a in enhanced
                          if a.presenter == overloaded]
            gap = sorted_w[-1][1] - sorted_w[0][1]
            candidates.sort(key=lambda x: abs(x[1] - gap/2))
            swapped = False
            for a, h in candidates:
                if h <= 0:
                    continue
                new_over = work[overloaded] - h
                new_under = work[underloaded] + h
                if abs(new_over - new_under) < gap:
                    a.presenter = underloaded
                    work[overloaded] -= h
                    work[underloaded] += h
                    task = task_map.get(a.task_id)
                    if task and underloaded in member_map:
                        a.score = round(skill_score(member_map[underloaded], task.required_skills), 3)
                    a.reasoning += f"已转给{underloaded}平衡负载"
                    swapped = True
                    break
            if not swapped:
                break
            sorted_w = sorted(work.items(), key=lambda x: x[1])
    # 负载失衡/超载检测（在均衡后计算，避免过期警告）
    note = qa.note or ""
    imbalance = []
    for name, hours in work.items():
        m = member_map.get(name)
        if m and hours > m.available_hours:
            imbalance.append(
                f"{name} 负载 {hours:.1f}h 超过可用 {m.available_hours:.1f}h")
    if imbalance:
        note += "；负载警告：" + "；".join(imbalance)
    return qa.model_copy(update={
        "assignments": enhanced, "workload": work, "note": note})