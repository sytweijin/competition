"""
CLI 单 Agent 调试入口

用法示例:
    python -m app.cli planner --course "软件工程" --desc "做一个管理系统" --members "张三:前端,李四:后端" --deadline 2026-08-01
    python -m app.cli matcher --plan-file plan.json --members "张三:前端,李四:后端"
    python -m app.cli timeline --plan-file plan.json --deadline 2026-08-01 --members "张三:4,李四:6"
    python -m app.cli reporter --plan-file plan.json --timeline-file timeline.json --qa-file qa.json
    python -m app.cli interview --plan-file plan.json --qa-file qa.json --requirements "重点关注技术选型"

负责人: B
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from app.models.schemas import (
    AssignmentInput, CourseInfo, TeamMember, PlanOutput,
    TimelineOutput, QAOutput, SubTask,
)


def parse_members(raw: str) -> list[TeamMember]:
    """解析成员字符串: "张三:前端,Python,李四:后端" 或 "张三,李四" """
    members = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, skills_str = part.split(":", 1)
            skills = [s.strip() for s in skills_str.split(";") if s.strip()]
        else:
            name, skills = part, []
        members.append(TeamMember(name=name.strip(), skill_tags=skills))
    return members


def parse_hours_members(raw: str) -> list[TeamMember]:
    """解析带每日工时的成员: "张三:4,李四:6" """
    members = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, hours_str = part.split(":", 1)
            try:
                daily_hours = float(hours_str.strip())
            except ValueError:
                daily_hours = 4.0
        else:
            name, daily_hours = part, 4.0
        members.append(TeamMember(
            name=name.strip(), daily_available_hours=daily_hours,
            available_hours=max(daily_hours, daily_hours * 14),
        ))
    return members


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_planner(args):
    from app.agents.planner import PlannerAgent
    parsed_members = parse_members(args.members)
    # 尝试从 args 获取工时信息（如果用户传了 --hours）
    if hasattr(args, 'hours') and args.hours:
        from app.cli import parse_hours_members as _phm
        hours_map = {m.name: m for m in _phm(args.hours)}
        for m in parsed_members:
            if m.name in hours_map:
                m.daily_available_hours = hours_map[m.name].daily_available_hours
                m.available_hours = hours_map[m.name].available_hours
    members_str = [
        f"{m.name}(skills: {'; '.join(m.skill_tags) or 'N/A'}, daily: {m.daily_available_hours}h, total: {m.available_hours}h)"
        for m in parsed_members
    ]
    agent = PlannerAgent()
    result = agent.run(
        course_name=args.course,
        course_description=args.desc,
        members=members_str,
        deadline=args.deadline,
        extra=args.extra or "",
    )
    print_result(result, "Planner")


def cmd_matcher(args):
    from app.agents.matcher import MatcherAgent
    from app.agents.scoring import enhance
    plan = PlanOutput(**load_json(args.plan_file))
    members = parse_members(args.members)
    agent = MatcherAgent()
    result = agent.run(plan=plan, members=members)
    if not hasattr(result, "error_type"):
        result = enhance(result, plan, members)
    print_result(result, "Matcher")


def cmd_timeline(args):
    from app.agents.timeline import TimelineAgent
    plan = PlanOutput(**load_json(args.plan_file))
    members = parse_hours_members(args.members) if args.members else None
    agent = TimelineAgent()
    result = agent.run(
        plan=plan,
        deadline=args.deadline,
        members=members,
    )
    print_result(result, "Timeline")


def cmd_reporter(args):
    from app.agents.reporter import ReporterAgent
    plan = PlanOutput(**load_json(args.plan_file))
    timeline = TimelineOutput(**load_json(args.timeline_file))
    qa = QAOutput(**load_json(args.qa_file))
    agent = ReporterAgent()
    result = agent.run(plan=plan, timeline=timeline, qa_matrix=qa)
    print_result(result, "Reporter")


def cmd_interview(args):
    from app.agents.interview_sim import InterviewSimAgent
    plan = PlanOutput(**load_json(args.plan_file))
    qa = QAOutput(**load_json(args.qa_file))
    agent = InterviewSimAgent()
    result = agent.run(plan=plan, qa_matrix=qa, user_requirements=args.requirements or "")
    if isinstance(result, str):
        print("\n" + "=" * 60)
        print("Interview Simulation Results")
        print("=" * 60)
        print(result)
    else:
        print(f"Error: {result}")


def cmd_full(args):
    """Run the full pipeline via Coordinator."""
    from app.coordinator import Coordinator
    members = parse_hours_members(args.members)
    # 与 Web 前端一致：按 deadline 剩余天数校正总可用工时
    deadline_date = date.fromisoformat(args.deadline)
    remaining = max(1, (deadline_date - date.today()).days)
    members = [
        m.model_copy(update={
            "available_hours": max(m.daily_available_hours,
                                    m.daily_available_hours * remaining),
        })
        for m in members
    ]
    inp = AssignmentInput(
        course=CourseInfo(name=args.course, description=args.desc),
        members=members,
        deadline=date.fromisoformat(args.deadline),
        additional_requirements=args.extra or "",
    )
    coord = Coordinator()
    result = coord.run(inp)
    print_result(result, "FullPlan")


def print_result(result, label: str):
    print("\n" + "=" * 60)
    print(f"{label} Output")
    print("=" * 60)
    if hasattr(result, "model_dump_json"):
        print(json.dumps(json.loads(result.model_dump_json()), indent=2, ensure_ascii=False))
    elif isinstance(result, str):
        print(result)
    else:
        print(result)
    print("=" * 60)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Single-agent debugging CLI for WorkBuddy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Agent to run")

    # Planner
    p_planner = sub.add_parser("planner", help="Run Planner agent")
    p_planner.add_argument("--course", required=True, help="Course name")
    p_planner.add_argument("--desc", default="", help="Course description")
    p_planner.add_argument("--members", required=True, help="Members: name:skill1;skill2,name2:skill3")
    p_planner.add_argument("--deadline", required=True, help="Deadline ISO date")
    p_planner.add_argument("--hours", default="", help="Members with daily hours: name:hours,name2:hours2")
    p_planner.add_argument("--extra", default="", help="Extra requirements")
    p_planner.set_defaults(func=cmd_planner)

    # Matcher
    p_matcher = sub.add_parser("matcher", help="Run Matcher agent")
    p_matcher.add_argument("--plan-file", required=True, help="Path to plan JSON")
    p_matcher.add_argument("--members", required=True, help="Members string")
    p_matcher.set_defaults(func=cmd_matcher)

    # Timeline
    p_timeline = sub.add_parser("timeline", help="Run Timeline agent (pure algorithm)")
    p_timeline.add_argument("--plan-file", required=True, help="Path to plan JSON")
    p_timeline.add_argument("--deadline", required=True, help="Deadline ISO date")
    p_timeline.add_argument("--members", default="", help="Members with daily hours: name:hours")
    p_timeline.set_defaults(func=cmd_timeline)

    # Reporter
    p_reporter = sub.add_parser("reporter", help="Run Reporter agent")
    p_reporter.add_argument("--plan-file", required=True, help="Path to plan JSON")
    p_reporter.add_argument("--timeline-file", required=True, help="Path to timeline JSON")
    p_reporter.add_argument("--qa-file", required=True, help="Path to QA matrix JSON")
    p_reporter.set_defaults(func=cmd_reporter)

    # Interview
    p_interview = sub.add_parser("interview", help="Run Interview Simulation")
    p_interview.add_argument("--plan-file", required=True, help="Path to plan JSON")
    p_interview.add_argument("--qa-file", required=True, help="Path to QA matrix JSON")
    p_interview.add_argument("--requirements", default="", help="Custom requirements for the simulation")
    p_interview.set_defaults(func=cmd_interview)

    # Full pipeline
    p_full = sub.add_parser("full", help="Run the full Coordinator pipeline")
    p_full.add_argument("--course", required=True)
    p_full.add_argument("--desc", default="")
    p_full.add_argument("--members", required=True, help="Members: name:hours,name2:hours2")
    p_full.add_argument("--deadline", required=True)
    p_full.add_argument("--extra", default="")
    p_full.set_defaults(func=cmd_full)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
