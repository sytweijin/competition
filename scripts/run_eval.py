#!/usr/bin/env python
"""运行学生项目评测集，统计识别率、耗时、负载改善和重排耗时。

默认使用确定性快速链路（不调用真实模型）；``--use-ai`` 可切换为真实模型。
结果写入 ``eval/results/``，同时生成人工方案 vs Agent 方案对照。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import (  # noqa: E402
    AssignmentInput, CourseInfo, FullPlan, ManualAssignmentRequest, TeamMember,
)
from app.file_analysis import analyze_locally  # noqa: E402
from app.services.project_service import (  # noqa: E402
    apply_manual_assignment, confirm_draft, generate_draft, workload_snapshot,
)

CASES_FILE = ROOT / "eval" / "cases.json"
RESULTS_DIR = ROOT / "eval" / "results"


def load_cases() -> list[dict]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def _assignment_input(case: dict) -> AssignmentInput:
    data = case["input"]
    analysis = analyze_locally(
        f"{data['course']['description']}\n{data.get('requirements', '')}")
    return AssignmentInput(
        course=CourseInfo(**data["course"]),
        members=[TeamMember(**member) for member in data["members"]],
        deadline=data["deadline"],
        requirements=data.get("requirements", ""),
        default_start_date=data.get("default_start_date"),
        default_end_date=data.get("default_end_date"),
        requirement_analysis=analysis,
    )


def _workload_cv(plan: FullPlan) -> float:
    snapshot = workload_snapshot(plan)
    values = [
        item["total_hours"]
        for item in snapshot["members"].values()
    ]
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(values) / mean


def _recognition_rate(plan: FullPlan, hard_requirements: list[str]) -> float:
    text = " ".join([
        plan.plan.summary or "",
        *(task.name or "" for task in plan.plan.tasks),
        *(task.description or "" for task in plan.plan.tasks),
    ]).lower()
    matched = sum(
        1 for keyword in hard_requirements if keyword.lower() in text)
    return round(matched / len(hard_requirements), 4) if hard_requirements else 1.0


def _baseline_workload_cv(plan: FullPlan) -> float:
    if not plan.input.members:
        return 0.0
    first = plan.input.members[0].name
    baseline = apply_manual_assignment(ManualAssignmentRequest(
        plan=plan,
        assignees={task.id: first for task in plan.plan.tasks},
    ))
    return _workload_cv(baseline)


def evaluate_case(case: dict, use_ai: bool = False) -> dict:
    inp = _assignment_input(case)
    started = time.perf_counter()
    draft = generate_draft(inp, use_ai=use_ai)
    plan = confirm_draft(inp, draft, use_ai_reflection=False)
    generation_ms = (time.perf_counter() - started) * 1000

    recognition = _recognition_rate(plan, case["hard_requirements"])
    agent_cv = _workload_cv(plan)
    baseline_cv = _baseline_workload_cv(plan)
    load_improvement = round(
        (baseline_cv - agent_cv) / baseline_cv, 4) if baseline_cv > 0 else 0.0

    reschedule_started = time.perf_counter()
    if plan.plan.tasks:
        target = plan.plan.tasks[0].id
        owner = next(
            (member.name for member in plan.input.members
             if member.name != plan.plan.tasks[0].assignee_id),
            plan.input.members[0].name,
        )
        apply_manual_assignment(ManualAssignmentRequest(
            plan=plan, assignees={target: owner}))
    reschedule_ms = (time.perf_counter() - reschedule_started) * 1000

    return {
        "id": case["id"],
        "name": case["name"],
        "task_count": len(plan.plan.tasks),
        "total_hours": round(
            sum(task.estimated_hours for task in plan.plan.tasks), 2),
        "recognition_rate": recognition,
        "generation_ms": round(generation_ms, 2),
        "load_cv": round(agent_cv, 4),
        "baseline_load_cv": round(baseline_cv, 4),
        "load_improvement": load_improvement,
        "reschedule_ms": round(reschedule_ms, 2),
        "critical_path_count": len(plan.timeline.critical_path),
        "human_reference": case["human_reference"],
    }


def _markdown_report(results: list[dict]) -> str:
    lines = [
        "# 人工方案 vs Agent 方案对照",
        "",
        "| 案例 | 任务数 人工/Agent | 工时 人工/Agent | 识别率 | 负载CV | 负载改善 | 重排耗时 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        human = item["human_reference"]
        lines.append(
            f"| {item['name']} | {human['task_count']}/{item['task_count']} | "
            f"{human['total_hours']}h/{item['total_hours']}h | "
            f"{item['recognition_rate']:.0%} | {item['load_cv']:.2f} | "
            f"{item['load_improvement']:.0%} | {item['reschedule_ms']:.0f}ms |"
        )
    lines.extend([
        "",
        "说明：识别率表示硬性交付要求出现在 Agent 生成任务/摘要中的比例；",
        "负载改善为“全部交给第一名成员”基线到 Agent 分工的负载离散度下降比例。",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-ai", action="store_true")
    parser.add_argument("--ids", nargs="*", default=None)
    args = parser.parse_args()

    cases = load_cases()
    if args.ids:
        cases = [case for case in cases if case["id"] in args.ids]
    if not cases:
        print("没有匹配的评测用例。", file=sys.stderr)
        return 2

    results = [evaluate_case(case, use_ai=args.use_ai) for case in cases]
    summary = {
        "total_cases": len(results),
        "avg_recognition_rate": round(
            statistics.fmean(item["recognition_rate"] for item in results), 4),
        "avg_generation_ms": round(
            statistics.fmean(item["generation_ms"] for item in results), 2),
        "avg_load_improvement": round(
            statistics.fmean(item["load_improvement"] for item in results), 4),
        "avg_reschedule_ms": round(
            statistics.fmean(item["reschedule_ms"] for item in results), 2),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "eval_report.json").write_text(
        json.dumps({"summary": summary, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (RESULTS_DIR / "human_vs_agent.md").write_text(
        _markdown_report(results), encoding="utf-8")
    print(json.dumps({"summary": summary, "results": results},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
