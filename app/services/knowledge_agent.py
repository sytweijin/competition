"""Knowledge Agent：根据问题自主选择系统工具并合成回答。"""

from __future__ import annotations

from app.models.schemas import FullPlan
from app.services.tools import call_tool


def _summarize_workload(result) -> str:
    members = result.get("members", {})
    if not members:
        return "当前没有成员工作量数据。"
    lines = []
    for name, item in members.items():
        lines.append(
            f"{name}（{item.get('role', '执行成员')}）"
            f" {item.get('total_hours', 0)}h / {item.get('task_count', 0)} 项"
        )
    return "成员工作量：\n- " + "\n- ".join(lines)


def _summarize_calendar(result) -> str:
    warnings = result.get("warnings") or []
    if not result.get("days"):
        return "资源日历：暂无带排期的任务。"
    base = f"资源日历共 {len(result['days'])} 天。"
    if warnings:
        return base + "\n冲突提示：\n- " + "\n- ".join(warnings)
    return base + "未发现资源冲突。"


def _summarize_reminders(result) -> str:
    items = result.get("reminders") or []
    if not items:
        return "提醒中心：当前没有待处理提醒。"
    return "提醒：\n- " + "\n- ".join(f"{x['title']}：{x['detail']}" for x in items)


def _summarize_review(result) -> str:
    members = result.get("members", {})
    suggestions = result.get("suggestions") or []
    lines = []
    for name, item in list(members.items())[:5]:
        dev = round(item.get("actual_hours", 0) - item.get("planned_hours", 0), 2)
        lines.append(f"{name} 计划 {item.get('planned_hours', 0)}h / 实际 {item.get('actual_hours', 0)}h / 偏差 {dev}h")
    text = "组织复盘：\n- " + "\n- ".join(lines)
    if suggestions:
        text += "\n经验建议：\n- " + "\n- ".join(suggestions[:3])
    return text


def ask(
    question: str,
    plan: FullPlan | None = None,
    username: str | None = None,
) -> dict:
    """根据问题关键词选择工具，返回回答与调用轨迹。"""
    q = question.lower()
    trace: list[str] = []
    parts: list[str] = []
    seen: set[str] = set()

    def run(name: str, args: dict) -> dict | None:
        if name in seen:
            return None
        seen.add(name)
        trace.append(name)
        try:
            return call_tool(name, args, plan, username=username)
        except Exception:
            return None

    if "风险" in q or "排期风险" in q:
        for name, label in [
            ("resource_calendar", _summarize_calendar),
            ("reminders", _summarize_reminders),
            ("org_review", _summarize_review),
        ]:
            result = run(name, {})
            if result:
                parts.append(label(result))

    if any(k in q for k in ["工作量", "负载", "谁最忙", "均衡"]):
        result = run("workload", {})
        if result:
            parts.append(_summarize_workload(result))

    if any(k in q for k in ["日历", "排期", "冲突", "哪天", "可用"]):
        result = run("resource_calendar", {})
        if result:
            parts.append(_summarize_calendar(result))

    if any(k in q for k in ["提醒", "到期", "截止", "未分配", "志愿者"]):
        result = run("reminders", {})
        if result:
            parts.append(_summarize_reminders(result))

    if any(k in q for k in ["复盘", "组织", "偏差", "经验", "建议"]):
        result = run("org_review", {})
        if result:
            parts.append(_summarize_review(result))

    if any(k in q for k in ["知识", "历史", "以前", "类似", "参考"]):
        result = run("knowledge", {"question": question})
        if result:
            parts.append(result.get("answer", ""))

    if not parts:
        if plan is not None:
            course = plan.input.course.name
            total = sum(t.estimated_hours or 0 for t in plan.plan.tasks)
            parts.append(
                f"当前项目「{course}」共 {len(plan.plan.tasks)} 项任务，"
                f"计划总工时 {round(total, 1)}h。"
            )
        else:
            parts.append("请提供项目方案，或问得更具体一些，例如“工作量怎么样”。")

    return {"answer": "\n\n".join(parts), "trace": trace}
