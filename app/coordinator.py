"""
Coordinator 总调度
负责：编排 Planner → Matcher → Timeline → Reporter 主链路
同时负责：输出校验 + 重试 + 日志
负责人：B（提交人）
"""

import logging

from app.models.schemas import (
    AgentError, AssignmentInput, FullPlan, PlanOutput,
    QAOutput, TimelineOutput, ReportOutput,
)
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.timeline import TimelineAgent
from app.agents.reporter import ReporterAgent

logger = logging.getLogger(__name__)


class Coordinator:
    """总调度器，编排多 Agent 主链路"""

    def __init__(self):
        self.planner = PlannerAgent()
        self.matcher = MatcherAgent()
        self.timeline = TimelineAgent()
        self.reporter = ReporterAgent()

    def run(self, inp: AssignmentInput) -> FullPlan:
        """执行完整主链路"""
        logger.info("Coordinator started: %s", inp.course.name)

        # Step 1: Planner
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            raise RuntimeError(f"Planner failed: {plan.message}")

        # Step 2: Matcher
        qa_matrix = self._step_matcher(plan, inp.members)
        if isinstance(qa_matrix, AgentError):
            logger.warning("Matcher failed, will skip QA matrix: %s",
                           qa_matrix.message)
            qa_matrix = QAOutput(assignments=[],
                                 note="Matcher failed: " + qa_matrix.message)

        # Step 3: Timeline
        timeline = self._step_timeline(plan, inp.deadline.isoformat())
        if isinstance(timeline, AgentError):
            logger.warning("Timeline failed, will skip timeline: %s",
                           timeline.message)
            timeline = TimelineOutput(tasks=[], critical_path=[],
                                      total_days=0,
                                      note="Timeline failed: " + timeline.message)

        # Step 4: Reporter
        report = self._step_reporter(plan, timeline, qa_matrix)
        if isinstance(report, AgentError):
            report = ReportOutput(
                summary="Report generation failed.",
                timeline_section="",
                qa_matrix_section="",
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

    def _step_planner(self, inp: AssignmentInput) -> PlanOutput | AgentError:
        members = [m.name for m in inp.members]
        return self.planner.run(
            course_name=inp.course.name,
            course_description=inp.course.description,
            members=members,
            deadline=inp.deadline.isoformat(),
            extra=inp.additional_requirements,
        )

    def _step_matcher(self, plan: PlanOutput,
                       members) -> QAOutput | AgentError:
        return self.matcher.run(plan=plan, members=members)

    def _step_timeline(self, plan: PlanOutput,
                        deadline: str) -> TimelineOutput | AgentError:
        return self.timeline.run(plan=plan, deadline=deadline)

    def _step_reporter(self, plan: PlanOutput,
                        timeline: TimelineOutput,
                        qa_matrix: QAOutput) -> ReportOutput | AgentError:
        return self.reporter.run(plan=plan, timeline=timeline,
                                  qa_matrix=qa_matrix)
