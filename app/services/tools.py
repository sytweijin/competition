"""系统工具列表与调用。"""

from __future__ import annotations

from app.models.schemas import FullPlan
from app.services.collab import knowledge_search, org_review, reminders
from app.services.project_service import resource_calendar, workload_snapshot


def list_tools() -> list[dict]:
    return [
        {"name": "workload", "description": "查询当前方案成员工作量"},
        {"name": "resource_calendar", "description": "查询成员每日负载与冲突"},
        {"name": "reminders", "description": "查询到期/未分配/志愿者/模块提醒"},
        {"name": "org_review", "description": "查询组织级复盘"},
        {"name": "knowledge", "description": "检索知识库", "args": {"question": "提问内容"}},
    ]


def call_tool(
    name: str,
    args: dict,
    plan: FullPlan | None = None,
    username: str | None = None,
) -> dict:
    args = args or {}
    if name == "workload":
        if plan is None:
            raise ValueError("workload 需要 plan")
        return workload_snapshot(plan)
    if name == "resource_calendar":
        if plan is None:
            raise ValueError("resource_calendar 需要 plan")
        return resource_calendar(plan)
    if name == "reminders":
        if plan is None:
            raise ValueError("reminders 需要 plan")
        return {"reminders": reminders(plan)}
    if name == "org_review":
        if plan is None:
            raise ValueError("org_review 需要 plan")
        return org_review(plan)
    if name == "knowledge":
        question = str(args.get("question") or "")
        if not question:
            raise ValueError("knowledge 需要 question")
        return knowledge_search(question, plan, username=username)
    raise ValueError(f"未知工具：{name}")
