"""协作提醒、知识库检索与组织级复盘。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import MEMORY_DIR
from app.models.schemas import FullPlan
from app.services.auth_store import accessible_filenames, auth_enabled

EXPERIENCE_FILE = MEMORY_DIR / "experience.jsonl"


def _task_end(task, timeline_map):
    def as_date(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    if task.end_date:
        return as_date(task.end_date)
    item = timeline_map.get(task.id)
    if item is not None and getattr(item, "end_date", None):
        return as_date(item.end_date)
    return None


def reminders(plan: FullPlan) -> list[dict]:
    today = date.today()
    due_days = 3
    timeline_map = {}
    if plan.timeline and plan.timeline.tasks:
        for item in plan.timeline.tasks:
            timeline_map[item.task_id] = item
    result = []
    for task in plan.plan.tasks:
        if task.status == "completed":
            continue
        if not task.assignee_id:
            result.append({
                "type": "unassigned",
                "title": f"任务 {task.id} 未分配负责人",
                "detail": task.name,
            })
        end = _task_end(task, timeline_map)
        if end is not None:
            delta = (end - today).days
            if 0 <= delta <= due_days:
                result.append({
                    "type": "due",
                    "title": f"任务 {task.id} 即将到期",
                    "detail": f"{task.name} · {end.isoformat()} · 剩余 {delta} 天",
                })
    for volunteer in (plan.volunteer_pool or []):
        if volunteer.status == "待确认":
            result.append({
                "type": "volunteer",
                "title": "志愿者待确认",
                "detail": f"{volunteer.name} · 任务 {volunteer.task_id}",
            })
    if plan.input.project_mode == "large_project":
        for module in (plan.plan.modules or []):
            if not module.assignee_id:
                result.append({
                    "type": "module",
                    "title": f"模块 {module.id} 未认领骨干",
                    "detail": module.name,
                })
    return result


def _tokenize(text: str) -> set[str]:
    tokens = set()
    lower = str(text or "").lower()
    for part in re.split(r"[\s,，。;；:：]+", lower):
        if not part:
            continue
        if len(part) >= 2:
            tokens.add(part)
        if re.search(r"[\u4e00-\u9fff]", part):
            for n in (2, 3):
                for i in range(max(0, len(part) - n + 1)):
                    tokens.add(part[i:i + n])
    return tokens


def knowledge_search(
    question: str,
    plan: FullPlan | None = None,
    limit: int = 5,
    username: str | None = None,
) -> dict:
    tokens = _tokenize(question)
    sources = []
    if plan is not None:
        text = " ".join([
            plan.input.course.name,
            plan.plan.summary or "",
            " ".join(t.name for t in plan.plan.tasks),
        ])
        score = len(tokens & _tokenize(text))
        if score:
            sources.append({
                "name": plan.input.course.name,
                "snippet": (plan.plan.summary or plan.input.course.description or "")[:120],
                "score": score,
            })
    if MEMORY_DIR.exists():
        allowed = None
        if auth_enabled() and username != "admin":
            allowed = accessible_filenames(username)
        for path in sorted(MEMORY_DIR.glob("*.json")):
            if allowed is not None and path.name not in allowed:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            course = data.get("input", {}).get("course", {})
            plan_data = data.get("plan", {})
            text = " ".join([
                course.get("name", ""),
                course.get("description", ""),
                plan_data.get("summary", ""),
                " ".join(t.get("name", "") for t in plan_data.get("tasks", [])),
            ])
            score = len(tokens & _tokenize(text))
            if score:
                snippet = (plan_data.get("summary") or course.get("description") or "")[:120]
                sources.append({
                    "name": course.get("name") or path.stem,
                    "snippet": snippet,
                    "score": score,
                })
    sources.sort(key=lambda item: -item["score"])
    if EXPERIENCE_FILE.exists():
        for line in EXPERIENCE_FILE.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = f"{entry.get('course', '')} {entry.get('text', '')}"
            score = len(tokens & _tokenize(text))
            if score:
                sources.append({
                    "name": f"经验：{entry.get('course', '历史项目')}",
                    "snippet": entry.get("text", "")[:120],
                    "score": score,
                })
    sources = sources[:limit]
    if sources:
        answer = "根据知识库检索，找到以下相关内容：\n" + "\n".join(
            f"- {item['name']}：{item['snippet']}" for item in sources
        )
    else:
        answer = "知识库中没有找到与这个问题直接相关的内容。可以试试问项目分工、工时、排期或历史方案。"
    return {"answer": answer, "sources": sources}


def org_review(plan: FullPlan) -> dict:
    member_map = {m.name: m for m in plan.input.members}
    member_planned = {name: 0.0 for name in member_map}
    member_actual = {name: 0.0 for name in member_map}
    role_planned = {}
    role_actual = {}
    module_planned = {}
    module_actual = {}
    for task in plan.plan.tasks:
        owner = task.assignee_id
        planned = task.estimated_hours or 0.0
        actual = task.actual_hours
        if owner in member_map:
            member_planned[owner] += planned
            if actual is not None:
                member_actual[owner] += actual
        role = member_map[owner].role if owner in member_map else "未分配"
        role_planned[role] = role_planned.get(role, 0.0) + planned
        if actual is not None:
            role_actual[role] = role_actual.get(role, 0.0) + actual
        module_id = task.module_id or "_"
        module_planned[module_id] = module_planned.get(module_id, 0.0) + planned
        if actual is not None:
            module_actual[module_id] = module_actual.get(module_id, 0.0) + actual

    suggestions = []
    for task in plan.plan.tasks:
        if task.actual_hours is None:
            continue
        ratio = task.actual_hours / max(0.1, task.estimated_hours)
        if ratio > 1.2:
            suggestions.append(f"{task.name} 实际工时明显高于计划，建议后续同类任务提高预估。")
        elif ratio < 0.8:
            suggestions.append(f"{task.name} 实际工时明显低于计划，后续可降低同类任务预估。")
    return {
        "members": {
            name: {
                "role": member_map[name].role or "执行成员",
                "planned_hours": round(member_planned[name], 2),
                "actual_hours": round(member_actual[name], 2),
            }
            for name in member_map
        },
        "roles": {
            role: {
                "planned_hours": round(role_planned[role], 2),
                "actual_hours": round(role_actual.get(role, 0.0), 2),
            }
            for role in role_planned
        },
        "modules": {
            module_id: {
                "planned_hours": round(module_planned[module_id], 2),
                "actual_hours": round(module_actual.get(module_id, 0.0), 2),
            }
            for module_id in module_planned
        },
        "suggestions": suggestions,
    }


def save_experience(plan: FullPlan) -> int:
    """把组织复盘建议写入跨项目经验知识库。"""
    review = org_review(plan)
    suggestions = review.get("suggestions") or []
    if not suggestions:
        return 0
    existing = set()
    if EXPERIENCE_FILE.exists():
        for line in EXPERIENCE_FILE.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing.add((item.get("course", ""), item.get("text", "")))
    EXPERIENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "course": plan.input.course.name,
        "text": "\n".join(suggestions),
    }
    if (entry["course"], entry["text"]) in existing:
        return 0
    with EXPERIENCE_FILE.open("a", encoding="utf-8") as target:
        target.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(suggestions)
