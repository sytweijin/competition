"""基于结构化案例的轻量任务工时检索与校准。"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.schemas import PlanOutput, SubTask
from app.config import MEMORY_DIR


_KNOWLEDGE_PATH = (
    Path(__file__).resolve().parent.parent / "knowledge" / "duration_examples.json"
)
_FEEDBACK_PATH = MEMORY_DIR / ".duration_feedback.jsonl"


@dataclass(frozen=True)
class DurationEstimate:
    hours: float
    min_hours: float
    max_hours: float
    reason: str
    confidence: str
    example_id: str | None = None
    required_duration_hours: float | None = None


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
    """根据多案例排序、任务范围、显式时长和历史修正估算任务。"""
    text = " ".join((task.name, task.description, task.category)).strip()
    matches = _rank_task_examples(task, limit=3)
    matched = matches[0] if matches else None

    explicit, required_duration = _explicit_time_info(text)
    if explicit is not None:
        lower = max(0.5, explicit * 0.9)
        upper = max(lower, explicit * 1.1)
        return DurationEstimate(
            hours=_round_half(explicit), min_hours=_round_half(lower),
            max_hours=_round_half(upper),
            reason=f"任务说明明确给出约 {explicit:g} 小时的工作量。",
            confidence="高", example_id=matched["id"] if matched else None,
            required_duration_hours=required_duration,
        )

    if matched is None:
        current = max(0.5, task.estimated_hours)
        reason = "知识库暂无足够相似案例，暂保留原估值并给出浮动范围。"
        if required_duration is not None:
            reason += (
                f" 另有 {required_duration:g} 小时规定活动时长，"
                "不直接当作负责人制作工时。")
        return DurationEstimate(
            hours=_round_half(current),
            min_hours=_round_half(max(0.5, current * 0.75)),
            max_hours=_round_half(current * 1.25),
            reason=reason,
            confidence="低", required_duration_hours=required_duration,
        )

    multiplier, scope_reason = _scope_multiplier(text, matched["id"])
    feedback_multiplier, feedback_count = _feedback_multiplier(task, matched["id"])
    multiplier *= feedback_multiplier
    base = float(matched["base_hours"]) * multiplier
    lower = float(matched["min_hours"]) * multiplier
    upper = float(matched["max_hours"]) * multiplier
    reason = matched["reason"]
    if scope_reason:
        reason += f" {scope_reason}"
    if len(matches) > 1:
        reason += " 同时参考了" + "、".join(item["label"] for item in matches[1:]) + "。"
    if feedback_count:
        reason += f" 已结合 {feedback_count} 条相似任务的人工修正。"
    if required_duration is not None:
        reason += (
            f" 任务另有 {required_duration:g} 小时的规定活动持续时间；"
            "该时长单独展示，不直接当作负责人制作工时。"
        )
    return DurationEstimate(
        hours=_round_half(base), min_hours=_round_half(lower),
        max_hours=max(_round_half(lower), _round_half(upper)),
        reason=reason, confidence="高" if feedback_count else "中",
        example_id=matched["id"], required_duration_hours=required_duration,
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
            "required_duration_hours": estimate.required_duration_hours,
        }))
    return plan.model_copy(update={"tasks": tasks})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _example_matches(example: dict, text: str) -> bool:
    normalized = _normalize(text)
    return any(_normalize(word) in normalized for word in example["keywords"])


def _explicit_time_info(text: str) -> tuple[float | None, float | None]:
    """区分明确制作人时和活动持续时间，避免把二者混为一个数字。"""
    effort_patterns = (
        r"(?:工作量|预计|需要|耗时|共计)\s*(\d+(?:\.\d+)?)\s*(?:小时|h)\b",
    )
    effort_values = [
        float(match.group(1))
        for pattern in effort_patterns
        for match in re.finditer(pattern, text, flags=re.IGNORECASE)
    ]
    duration_values = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*学时", text):
        prefix = text[max(0, match.start() - 8):match.start()]
        if re.search(r"(?:不超过|至多|最多|上限)\s*$", prefix):
            continue
        duration_values.append(float(match.group(1)))
    return (
        max(effort_values) if effort_values else None,
        max(duration_values) if duration_values else None,
    )


def _scope_multiplier(text: str, example_id: str) -> tuple[float, str]:
    multiplier = 1.0
    reasons: list[str] = []
    compact = _normalize(text)

    word_scopes = list(re.finditer(r"(\d{3,5})\s*字", text))
    if word_scopes and example_id in {"short-writing", "research-report"}:
        selected = max(word_scopes, key=lambda item: int(item.group(1)))
        stated_words = int(selected.group(1))
        prefix = text[max(0, selected.start() - 8):selected.start()]
        is_upper_bound = bool(re.search(
            r"(?:不超过|至多|最多|上限为?|控制在)\s*$", prefix))
        words = round(stated_words * 0.6) if is_upper_bound else stated_words
        if example_id == "research-report":
            if words <= 3000:
                multiplier *= 0.75
            elif words <= 6000:
                multiplier *= 1.0
            elif words <= 10000:
                multiplier *= 1.25
            else:
                multiplier *= 1.5
        else:
            if words <= 1500:
                multiplier *= 0.8
            elif words <= 3500:
                multiplier *= 1.2
            else:
                multiplier *= 1.5
        if is_upper_bound:
            reasons.append(
                f"{stated_words} 字是上限而非默认写满，按常见约 {words} 字估算")
        else:
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


def _rank_task_examples(task: SubTask, limit: int = 3) -> list[dict]:
    """综合任务名称、描述和动作位置排序，避免只凭一个宽泛关键词。"""
    name = _normalize(task.name)
    description = _normalize(task.description)
    ranked: list[tuple[float, dict]] = []
    completion_ids = {"confirm-submit", "review-revise"}
    for example in load_duration_examples():
        score = 0.0
        for keyword in example["keywords"]:
            word = _normalize(keyword)
            if word in name:
                score += max(3, len(word)) * 2
                if name.startswith(word):
                    score += 4
            elif word in description:
                score += max(2, len(word))
        if (example["id"] in completion_ids
                and any(word in name for word in ("确认", "检查", "提交", "审核", "校对"))):
            score += 5
            if any(word in name + description for word in ("已有", "完成后", "最终")):
                score += 5
        # 上下文感知：避免“搭建报告结构”被误判为开发类
        if example["id"] == "software-feature":
            writing_context = ("报告", "文案", "提纲", "结构", "策划", "方案", "总结", "撰写")
            if any(w in name + description for w in writing_context):
                score *= 0.3
        # 上下文感知：避免“设计演示文稿”被误判为平面设计类
        if example["id"] == "layout":
            if any(w in name + description for w in ("ppt", "演示", "汇报", "幻灯片")):
                score *= 0.2
        if score > 0:
            ranked.append((score, example))
    ranked.sort(key=lambda item: (-item[0], item[1]["base_hours"]))
    return [item[1] for item in ranked[:limit]]


def record_duration_feedback(original: SubTask, corrected: SubTask) -> bool:
    """记录用户对自动估时的明确修正；不保存完整任务说明。"""
    if not original.estimate_reason:
        return False
    if abs(original.estimated_hours - corrected.estimated_hours) < 0.5:
        return False
    matches = _rank_task_examples(original, limit=1)
    example_id = matches[0]["id"] if matches else "unknown"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signature": _normalize(original.name),
        "example_id": example_id,
        "suggested_hours": original.estimated_hours,
        "corrected_hours": corrected.estimated_hours,
    }
    try:
        _FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _FEEDBACK_PATH.open("a", encoding="utf-8") as target:
            target.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _feedback_multiplier(task: SubTask, example_id: str) -> tuple[float, int]:
    """至少三条相似人工修正后才影响新估时，避免单次误改污染基准。"""
    if not _FEEDBACK_PATH.exists():
        return 1.0, 0
    signature = _normalize(task.name)
    ratios: list[float] = []
    try:
        for line in _FEEDBACK_PATH.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("example_id") != example_id:
                continue
            if _signature_similarity(signature, entry.get("signature", "")) < 0.3:
                continue
            suggested = float(entry.get("suggested_hours", 0))
            corrected = float(entry.get("corrected_hours", 0))
            if suggested > 0 and corrected > 0:
                ratios.append(corrected / suggested)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 1.0, 0
    if len(ratios) < 3:
        return 1.0, 0
    return min(1.5, max(0.5, statistics.median(ratios))), len(ratios)


def _signature_similarity(left: str, right: str) -> float:
    def bigrams(value: str) -> set[str]:
        return {value[index:index + 2] for index in range(max(0, len(value) - 1))}
    a, b = bigrams(left), bigrams(right)
    if not a or not b:
        return 1.0 if left == right and left else 0.0
    return len(a & b) / len(a | b)
