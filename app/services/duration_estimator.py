"""基于结构化案例的轻量任务工时检索与校准。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.schemas import PlanOutput, SubTask


_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent / "knowledge" / "duration_examples.json"
)


@dataclass(frozen=True)
class DurationEstimate:
    hours: float
    min_hours: float
    max_hours: float
    reason: str
    confidence: str
    example_id: str | None = None


@lru_cache(maxsize=1)
def load_duration_examples() -> tuple[dict, ...]:
    with _KNOWLEDGE_PATH.open("r", encoding="utf-8") as source:
        return tuple(json.load(source))


def retrieve_duration_examples(text: str, limit: int = 6) -> list[dict]:
    """按关键词相关度检索案例；无命中时返回常用轻量案例作为基线。"""
    normalized = _normalize(text)
    scored: list[tuple[int, dict]] = []
    for example in load_duration_examples():
        hits = [word for word in example["keywords"] if _normalize(word) in normalized]
        if hits:
            score = sum(max(2, len(_normalize(word))) for word in hits)
            scored.append((score, example))
    scored.sort(key=lambda item: (-item[0], item[1]["base_hours"]))
    if scored:
        return [item[1] for item in scored[:limit]]
    defaults = {"confirm-submit", "organize-materials", "short-writing", "ppt"}
    return [item for item in load_duration_examples() if item["id"] in defaults][:limit]


def build_duration_context(text: str, limit: int = 6) -> str:
    """生成供 Planner 使用的紧凑检索上下文。"""
    lines = [
        "以下是从本地工时知识库检索到的参考案例。只按实际工作范围估算人时，",
        "成员可用时间仅用于超载检查，不得为了填满产能而放大任务工时：",
    ]
    for example in retrieve_duration_examples(text, limit):
        lines.append(
            f"- {example['label']}：常见 {example['min_hours']:g}–"
            f"{example['max_hours']:g}h，基准 {example['base_hours']:g}h；"
            f"{example['reason']}"
        )
    return "\n".join(lines)


def estimate_task(task: SubTask) -> DurationEstimate:
    """根据最相近案例、范围词和显式工作时长估算一个任务。"""
    text = " ".join((task.name, task.description, task.category)).strip()
    matches = retrieve_duration_examples(text, limit=1)
    matched = matches[0] if matches and _example_matches(matches[0], text) else None

    explicit = _explicit_effort_hours(text)
    if explicit is not None:
        lower = max(0.5, explicit * 0.9)
        upper = max(lower, explicit * 1.1)
        return DurationEstimate(
            hours=_round_half(explicit), min_hours=_round_half(lower),
            max_hours=_round_half(upper),
            reason=f"任务说明明确给出约 {explicit:g} 小时的工作量。",
            confidence="高", example_id=matched["id"] if matched else None,
        )

    if matched is None:
        current = max(0.5, task.estimated_hours)
        return DurationEstimate(
            hours=_round_half(current),
            min_hours=_round_half(max(0.5, current * 0.75)),
            max_hours=_round_half(current * 1.25),
            reason="知识库暂无足够相似案例，暂保留原估值并给出浮动范围。",
            confidence="低",
        )

    multiplier, scope_reason = _scope_multiplier(text, matched["id"])
    base = float(matched["base_hours"]) * multiplier
    lower = float(matched["min_hours"]) * multiplier
    upper = float(matched["max_hours"]) * multiplier
    reason = matched["reason"]
    if scope_reason:
        reason += f" {scope_reason}"
    return DurationEstimate(
        hours=_round_half(base), min_hours=_round_half(lower),
        max_hours=max(_round_half(lower), _round_half(upper)),
        reason=reason, confidence="中", example_id=matched["id"],
    )


def calibrate_plan_estimates(plan: PlanOutput) -> PlanOutput:
    """统一校准新生成的计划；用户后续手工编辑不经过这里。"""
    tasks = []
    for task in plan.tasks:
        estimate = estimate_task(task)
        tasks.append(task.model_copy(update={
            "estimated_hours": estimate.hours,
            "estimate_min_hours": estimate.min_hours,
            "estimate_max_hours": estimate.max_hours,
            "estimate_reason": estimate.reason,
            "estimate_confidence": estimate.confidence,
        }))
    return plan.model_copy(update={"tasks": tasks})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _example_matches(example: dict, text: str) -> bool:
    normalized = _normalize(text)
    return any(_normalize(word) in normalized for word in example["keywords"])


def _explicit_effort_hours(text: str) -> float | None:
    """只识别明确的工作量，不把“6 分钟汇报”等成品时长当制作工时。"""
    patterns = (
        r"(?:工作量|预计|需要|耗时|共计|共)\s*(\d+(?:\.\d+)?)\s*(?:小时|学时|h)\b",
        r"(\d+(?:\.\d+)?)\s*学时",
    )
    values = [
        float(match.group(1))
        for pattern in patterns
        for match in re.finditer(pattern, text, flags=re.IGNORECASE)
    ]
    return max(values) if values else None


def _scope_multiplier(text: str, example_id: str) -> tuple[float, str]:
    multiplier = 1.0
    reasons: list[str] = []
    compact = _normalize(text)

    word_counts = [int(value) for value in re.findall(r"(\d{3,5})\s*字", text)]
    if word_counts and example_id in {"short-writing", "research-report"}:
        words = max(word_counts)
        if words <= 1500:
            multiplier *= 0.8
        elif words <= 3500:
            multiplier *= 1.2
        elif words <= 10000:
            multiplier *= 1.5
        else:
            multiplier *= 1.8
        reasons.append(f"已按约 {words} 字的成果规模调整")

    day_counts = [int(value) for value in re.findall(r"(\d+)\s*天", text)]
    if day_counts and example_id == "field-research":
        days = max(day_counts)
        multiplier *= max(1.0, days * 0.75)
        reasons.append(f"已按 {days} 天现场工作调整")

    if any(word in compact for word in ("简单", "基础", "初步", "简要")):
        multiplier *= 0.8
        reasons.append("任务范围偏轻量")
    if any(word in compact for word in ("完整", "复杂", "从零", "高质量", "多轮")):
        multiplier *= 1.35
        reasons.append("任务包含完整或复杂要求")
    return min(3.0, max(0.5, multiplier)), "；".join(reasons) + ("。" if reasons else "")


def _round_half(value: float) -> float:
    return max(0.5, round(float(value) * 2) / 2)
