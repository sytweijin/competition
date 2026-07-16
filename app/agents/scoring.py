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



def _work_from(assignments, task_hours, members):
    """按最终分配精确计算每人负载。"""
    w = {m.name: 0.0 for m in members}
    for a in assignments:
        h = task_hours.get(a.task_id, 0.0)
        if h <= 0 or a.presenter in ("", "(已完成)"):
            continue
        w[a.presenter] = w.get(a.presenter, 0.0) + h
        if a.qa_primary and a.qa_primary != a.presenter:
            w[a.qa_primary] = w.get(a.qa_primary, 0.0) + h * QA_PRIMARY_RATIO
        for mem in (a.qa_support or []):
            if mem not in (a.presenter, a.qa_primary):
                w[mem] = w.get(mem, 0.0) + h * QA_SUPPORT_RATIO
    return w


def _balance_workload(assignments, task_hours, members, threshold=1.0, max_passes=500):
    """统一负载均衡：主讲/主答/辅答均可搬运，目标 max-min<=threshold。

    每步枚举所有可行搬运，用「真实重算负载」评估搬运后的全局 gap，选最小者执行；
    gap 不再下降即停。每次搬运前快照、评估后还原，杜绝近似误差。
    """
    names = [m.name for m in members]

    def gap_of(w):
        vals = list(w.values())
        return (max(vals) - min(vals)) if vals else 0.0

    def snapshot(a):
        return (a.presenter, a.qa_primary, list(a.qa_support or []))

    def restore(a, snap):
        a.presenter, a.qa_primary, sup = snap[0], snap[1], list(snap[2])
        a.qa_support = sup

    for _ in range(max_passes):
        gap = gap_of(_work_from(assignments, task_hours, members))
        if gap <= threshold + 1e-9:
            break
        best_gap = gap
        best = None  # (new_gap, assignment, kind, target)
        for a in assignments:
            if a.presenter in ("", "(已完成)"):
                continue
            snap = snapshot(a)
            cur_p, cur_q, cur_s = snap
            # 主讲换人
            for t in names:
                if t == cur_p:
                    continue
                a.presenter = t
                ng = gap_of(_work_from(assignments, task_hours, members))
                if ng < best_gap - 1e-12:
                    best_gap, best = ng, (a, "presenter", t)
                restore(a, snap)
            # 主答换人
            if cur_q:
                for t in names:
                    if t in (cur_p, cur_q):
                        continue
                    a.qa_primary = t
                    if t in (a.qa_support or []):
                        a.qa_support = [x for x in a.qa_support if x != t]
                    ng = gap_of(_work_from(assignments, task_hours, members))
                    if ng < best_gap - 1e-12:
                        best_gap, best = ng, (a, "primary", t)
                    restore(a, snap)
            # 辅答换人
            for owner in cur_s:
                for t in names:
                    if t in (cur_p, cur_q) or t in cur_s:
                        continue
                    a.qa_support = [x for x in cur_s if x != owner] + [t]
                    ng = gap_of(_work_from(assignments, task_hours, members))
                    if ng < best_gap - 1e-12:
                        best_gap, best = ng, (a, "support", (owner, t))
                    restore(a, snap)
        if best is None:
            break
        a, kind, payload = best
        if kind == "presenter":
            a.presenter = payload
        elif kind == "primary":
            if payload in (a.qa_support or []):
                a.qa_support = [x for x in a.qa_support if x != payload]
            a.qa_primary = payload
        else:
            owner, t = payload
            a.qa_support = [x for x in (a.qa_support or []) if x != owner] + [t]
    return _work_from(assignments, task_hours, members)

def _split_suggestion(work, assignments, task_hours, members, threshold=1.0):
    """均衡后 gap 仍超阈值时，给"建议拆分超载成员最大任务"的提示。

    当任务结构本身无法在成员间均摊（如 5 个 5h 任务给 3 人，必有人扛 2 个），
    自动拆分会改动用户计划，故不改数据，只在 note 里给出拆分建议。
    """
    if not work:
        return ""
    gap = max(work.values()) - min(work.values())
    if gap <= threshold + 1e-9:
        return ""
    over_name = max(work, key=lambda n: work[n])
    cands = []
    for a in assignments:
        if a.presenter == over_name:
            h = task_hours.get(a.task_id, 0.0)
            cands.append((h, a))
    if not cands:
        return ""
    cands.sort(key=lambda x: x[0], reverse=True)
    h, a = cands[0]
    if h <= 0:
        return ""
    return (f" 建议拆分 {over_name} 的 {a.task_name}（{h:.1f}h），"
            f"当前成员最大工时差 {gap:.1f}h 超过 1h，任务结构无法在 3 人间均摊")

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

        # 主答：剩余成员中「负载最轻」者优先（匹配度作同负载时的次序）
        rest = [(n, sc) for n, sc in scored if n != presenter]
        rest.sort(key=lambda x: (work[x[0]], -x[1]))
        primary = rest[0][0] if rest else ""
        if primary and primary != presenter:
            work[primary] += t.estimated_hours * QA_PRIMARY_RATIO

        # 辅答：再从剩余（排除主讲、主答）中取负载最轻的 2 人，避免同人占两席
        rest2 = [n for n, _ in rest if n != primary]
        rest2.sort(key=lambda n: work[n])
        support = rest2[:2]
        for s in support:
            work[s] += t.estimated_hours * QA_SUPPORT_RATIO

        best_skill = scored[0][1]
        reasoning = (
            f"{presenter}：{_fmt(t.required_skills)} 技能"
            f"匹配度 {best_skill:.2f}，综合最优"
        )
        assignments.append(QAAssignment(
            task_id=t.id, task_name=t.name, chapter="",
            presenter=presenter, qa_primary=primary, qa_support=support,
            score=round(best_skill, 3), reasoning=reasoning,
        ))

    # P1-2: 全员参与兜底——0 负载成员先补一个辅答角色，再进入均衡（避免兜底破坏均衡结果）
    zero_load = [n for n, h in work.items() if h <= 0]
    for n in zero_load:
        active = [a for a in assignments if a.presenter != "(已完成)" and n not in (a.qa_support or [])]
        if not active:
            continue
        target = max(active, key=lambda a: task_hours.get(a.task_id, 0.0))
        if target.qa_support is None:
            target.qa_support = []
        if n not in target.qa_support:
            target.qa_support.append(n)
            work[n] += task_hours.get(target.task_id, 0.0) * QA_SUPPORT_RATIO

    # 负载均衡：主讲/主答/辅答统一搬运，目标 max-min<=1h
    work = _balance_workload(assignments, task_hours, members, threshold=1.0)


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
    # 均衡后仍失衡（任务结构限制）：给出拆分建议而非自动改动计划
    note += _split_suggestion(work, assignments, task_hours, members, threshold=1.0)

    return QAOutput(assignments=assignments, workload=work, note=note)
def _fmt(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "未标注"


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

    # 负载均衡：主讲/主答/辅答统一搬运，目标 max-min<=1h
    work = _balance_workload(enhanced, task_hours, members, threshold=1.0)


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
    # 均衡后仍失衡（任务结构限制）：给出拆分建议而非自动改动计划
    note += _split_suggestion(work, enhanced, task_hours, members, threshold=1.0)
    return qa.model_copy(update={
        "assignments": enhanced, "workload": work, "note": note})