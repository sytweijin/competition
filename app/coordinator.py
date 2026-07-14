"""
Coordinator 总调度
负责：编排 Planner -> Matcher -> Timeline -> Reporter 主链路
同时负责：输出校验 + 重试 + 日志
负责人：B（提交人）

v0.3 改进：
- 将成员信息传递给 TimelineAgent，支持按成员实际可用工时折算
- 将 extra_requirements 传入 Planner
- 增强错误处理和日志
"""

from __future__ import annotations

import logging

from app.models.schemas import (
    AgentError, AssignmentInput, FullPlan, PlanOutput,
    QAOutput, TimelineOutput, ReportOutput,
)
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.scoring import assign_with_balance, enhance
from app.agents.timeline import TimelineAgent
from app.agents.reporter import ReporterAgent

logger = logging.getLogger(__name__)


class Coordinator:
    """总调度器，编排多 Agent 主链路。"""

    def __init__(self, hours_per_day: float | None = None):
        self.planner = PlannerAgent()
        self.matcher = MatcherAgent()
        self.timeline = TimelineAgent()
        self.reporter = ReporterAgent()
        self.hours_per_day = hours_per_day

    def run(self, inp: AssignmentInput) -> FullPlan:
        """执行完整主链路。"""
        logger.info("Coordinator started: %s", inp.course.name)

        # Step 1: Planner
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            raise RuntimeError(f"Planner failed: {plan.message}")

        # Step 2: Matcher（B3：LLM + 确定性评分兜底）
        qa_matrix = self._step_matcher(plan, inp.members)

        # Step 3: Timeline（回填 QA 矩阵的负责人，传入成员信息）
        timeline = self._step_timeline(plan, inp.deadline.isoformat(), qa_matrix, inp.members)
        if isinstance(timeline, AgentError):
            logger.warning("Timeline failed, skip timeline: %s",
                           timeline.message)
            timeline = TimelineOutput(tasks=[], critical_path=[],
                                      total_days=0,
                                      note="Timeline failed: " + timeline.message)

        # Step 4: Reporter
        report = self._step_reporter(plan, timeline, qa_matrix)
        if isinstance(report, AgentError):
            report = ReportOutput(
                summary="Report generation failed.",
                risk_note=report.message,
            )

        logger.info("Coordinator completed")
        return FullPlan(
            input=inp,
            plan=plan,
            timeline=timeline,
            qa_matrix=qa_matrix,
            report=report,
        )

    # ──────────── 各步骤 ────────────

    def _step_planner(self, inp: AssignmentInput) -> PlanOutput | AgentError:
        # 为 Planner 提供丰富的成员信息（含技能和可用工时）
        members = [
            f"{m.name}(技能: {', '.join(m.skill_tags) or '未标注'}; "
            f"总可用: {m.available_hours}h; "
            f"每日可用: {m.daily_available_hours}h)"
            for m in inp.members
        ]
        return self.planner.run(
            course_name=inp.course.name,
            course_description=inp.course.description,
            members=members,
            deadline=inp.deadline.isoformat(),
            extra=inp.additional_requirements,
        )

    def _step_matcher(self, plan: PlanOutput,
                      members) -> QAOutput:
        """LLM 匹配成功 -> enhance 补分；失败 -> 确定性兜底。"""
        result = self.matcher.run(plan=plan, members=members)
        if isinstance(result, AgentError):
            logger.warning("Matcher LLM failed, use deterministic B3: %s",
                           result.message)
            fallback = assign_with_balance(plan, members)
            return fallback.model_copy(
                update={"note": (fallback.note +
                                 "（LLM 不可用，启用确定性兜底）")})
        return enhance(result, plan, members)

    def _step_timeline(self, plan: PlanOutput, deadline: str,
                       qa: QAOutput | None = None,
                       members: list | None = None) -> TimelineOutput | AgentError:
        assignments: dict[str, list[str]] = {}
        if qa is not None:
            for a in qa.assignments:
                people = [a.presenter] if a.presenter else []
                if a.qa_primary and a.qa_primary not in people:
                    people.append(a.qa_primary)
                assignments[a.task_id] = people
        kwargs: dict = {"plan": plan, "deadline": deadline,
                        "assignments": assignments, "members": members}
        if self.hours_per_day is not None:
            kwargs["hours_per_day"] = self.hours_per_day
        return self.timeline.run(**kwargs)

    def _step_reporter(self, plan: PlanOutput,
                       timeline: TimelineOutput,
                       qa_matrix: QAOutput) -> ReportOutput | AgentError:
        return self.reporter.run(plan=plan, timeline=timeline,
                                 qa_matrix=qa_matrix)
