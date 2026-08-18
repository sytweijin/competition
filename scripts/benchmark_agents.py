#!/usr/bin/env python
"""本地可重复的 Planner / Matcher 性能基准。

安全约束：本脚本复用 app.config 的 python-dotenv 配置加载机制，但不直接读取、
输出或持久化 LLM_API_KEY。输出仅含规模、耗时、状态和计数。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.matcher import MatcherAgent  # noqa: E402
from app.agents.planner import PlannerAgent  # noqa: E402
from app.models.schemas import AgentError, PlanOutput, SubTask, TeamMember  # noqa: E402
from app.performance import (  # noqa: E402
    PerformanceTrace, llm_event_sink, request_trace, stage,
)

CASES = {
    "small": (3, 3),
    "medium": (10, 8),
    "large": (24, 12),
}
SKILLS = ["需求", "Python", "前端", "后端", "测试", "设计", "数据", "文档"]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def members_for(count: int) -> list[TeamMember]:
    return [TeamMember(
        name=f"M{index + 1}",
        skill_tags=[SKILLS[index % len(SKILLS)], SKILLS[(index + 2) % len(SKILLS)]],
        available_hours=40 + (index % 4) * 10,
    ) for index in range(count)]


def plan_for(count: int) -> PlanOutput:
    tasks = []
    for index in range(count):
        dependencies = [] if index == 0 else [f"T{index}"]
        tasks.append(SubTask(
            id=f"T{index + 1}",
            name=f"任务 {index + 1}",
            description="完成一个可验收的项目工作项",
            estimated_hours=4 + index % 4,
            dependencies=dependencies,
            required_skills=[SKILLS[index % len(SKILLS)]],
        ))
    return PlanOutput(tasks=tasks, summary=f"包含 {count} 个工作项的基准计划")


class JsonlWriter:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.handle = path.open("w", encoding="utf-8")

    def write(self, payload: dict) -> None:
        self.handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def run_one(agent_name: str, case_name: str, task_count: int, member_count: int,
            writer: JsonlWriter) -> dict:
    request_id = uuid.uuid4().hex
    trace = PerformanceTrace(task_count=task_count, member_count=member_count)
    events: list[dict] = []

    def emit(event: dict) -> None:
        payload = {"type": "attempt", "case": case_name, **event}
        events.append(payload)
        writer.write(payload)

    started = time.perf_counter()
    with request_trace(trace), llm_event_sink(request_id, emit), stage(agent_name):
        if agent_name == "Planner":
            result = PlannerAgent().run(
                course_name=f"{case_name} 本地性能基准",
                course_description=(
                    f"请拆解为约 {task_count} 个有依赖关系的通用软件项目任务。"),
                members=[member.name for member in members_for(member_count)],
                deadline=(date.today() + timedelta(days=45)).isoformat(),
                extra="输出可执行任务和验收标准",
            )
        else:
            result = MatcherAgent().run(
                plan=plan_for(task_count), members=members_for(member_count))
    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics = trace.finish()
    retries = metrics["llm_retries"].get(agent_name, 0)
    fallback_count = metrics["llm_plain_fallbacks"].get(agent_name, 0)
    summary = {
        "type": "request_summary",
        "request_id": request_id,
        "case": case_name,
        "agent": agent_name.lower(),
        "task_count": task_count,
        "member_count": member_count,
        "latency_ms": round(elapsed_ms, 3),
        "llm_calls": metrics["llm_calls"].get(agent_name, 0),
        "first_success": (
            not isinstance(result, AgentError) and retries == 0 and fallback_count == 0),
        "success": not isinstance(result, AgentError),
        "timeouts": metrics["llm_timeouts"].get(agent_name, 0),
        "retries": retries,
        "fallbacks": fallback_count,
        "retry_extra_ms": metrics["llm_retry_extra_ms"].get(agent_name, 0.0),
        "final_status": "success" if not isinstance(result, AgentError) else result.error_type,
        "attempt_events": len(events),
    }
    writer.write(summary)
    return summary


def aggregate(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {"planner": [], "matcher": []}
    for row in rows:
        grouped[row["agent"]].append(row)
    output = {}
    for agent, items in grouped.items():
        latencies = [item["latency_ms"] for item in items]
        calls = len(items)
        output[agent] = {
            "requests": calls,
            "llm_calls": sum(item["llm_calls"] for item in items),
            "latency_ms": latencies,
            "p50_ms": round(percentile(latencies, 0.50), 3),
            "p95_ms": round(percentile(latencies, 0.95), 3),
            "p99_ms": round(percentile(latencies, 0.99), 3),
            "first_success_rate": round(
                sum(item["first_success"] for item in items) / calls, 4),
            "timeouts": sum(item["timeouts"] for item in items),
            "retries": sum(item["retries"] for item in items),
            "fallbacks": sum(item["fallbacks"] for item in items),
            "retry_extra_total_ms": round(
                sum(item["retry_extra_ms"] for item in items), 3),
            "retry_extra_avg_ms": round(
                statistics.fmean(item["retry_extra_ms"] for item in items), 3),
            "success_rate": round(sum(item["success"] for item in items) / calls, 4),
        }
    return output


def summaries_from_jsonl(path: Path) -> list[dict]:
    """从既有 JSONL 重建请求摘要，并以物理事件校准 timeout 计数。"""
    attempts_by_request: dict[str, list[dict]] = {}
    summaries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if payload.get("type") == "attempt":
            attempts_by_request.setdefault(payload["request_id"], []).append(payload)
        elif payload.get("type") == "request_summary":
            summaries.append(payload)
    for summary in summaries:
        attempts = attempts_by_request.get(summary["request_id"], [])
        summary["timeouts"] = sum(
            event.get("status") == "timeout" for event in attempts)
    return summaries


def print_summary(summary: dict) -> None:
    for agent in ("planner", "matcher"):
        item = summary[agent]
        print(f"\n{agent.title()}")
        print(f"requests: {item['requests']}")
        print(f"llm_calls: {item['llm_calls']}")
        print(f"p50: {item['p50_ms'] / 1000:.3f}s")
        print(f"p95: {item['p95_ms'] / 1000:.3f}s")
        print(f"p99: {item['p99_ms'] / 1000:.3f}s")
        print(f"first_success_rate: {item['first_success_rate']:.1%}")
        print(f"timeouts: {item['timeouts']}")
        print(f"retries: {item['retries']}")
        print(f"fallbacks: {item['fallbacks']}")
        print(f"retry_extra_avg: {item['retry_extra_avg_ms'] / 1000:.3f}s")
        print(f"final_success_rate: {item['success_rate']:.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10,
                        help="每个 case、每个 Agent 的重复次数，默认 10")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES),
                        help="要运行的输入规模")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "benchmark_results" / "planner_matcher.jsonl")
    parser.add_argument("--summary", type=Path,
                        default=ROOT / "benchmark_results" / "planner_matcher_summary.json")
    parser.add_argument("--summarize-only", action="store_true",
                        help="不调用模型，仅从 --output 的既有 JSONL 重新汇总")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats 必须至少为 1")

    if args.summarize_only:
        summary = aggregate(summaries_from_jsonl(args.output))
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print_summary(summary)
        return 0

    # app.config 已通过项目既有 load_dotenv() 加载配置；这里只判断是否可用，
    # 永远不读取、打印或写出变量值。
    if not bool(os.environ.get("LLM_API_KEY")):
        print("项目配置未检测到 LLM_API_KEY；真实模型 benchmark 已安全跳过。", file=sys.stderr)
        return 2

    writer = JsonlWriter(args.output)
    rows = []
    try:
        for case_name in args.cases:
            task_count, member_count = CASES[case_name]
            for _ in range(args.repeats):
                for agent in ("Planner", "Matcher"):
                    rows.append(run_one(
                        agent, case_name, task_count, member_count, writer))
    finally:
        writer.close()

    summary = aggregate(rows)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(summary)
    print(f"\nJSONL: {args.output}")
    print(f"Summary: {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
