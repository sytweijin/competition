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
    QAOutput, TimelineOutput, ReportOutput, SubTask,
)
from app.agents.scoring import format_skills_for_prompt
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.scoring import assign_with_balance, enhance
from app.agents.timeline import TimelineAgent
from app.agents.reporter import ReporterAgent

logger = logging.getLogger(__name__)


class Coordinator:
    """总调度器，编排多 Agent 主链路。"""

    def __init__(self):
        self.planner = PlannerAgent()
        self.matcher = MatcherAgent()
        self.timeline = TimelineAgent()
        self.reporter = ReporterAgent()

    def run(self, inp: AssignmentInput) -> FullPlan:
        """执行完整主链路。"""
        logger.info("Coordinator started: %s", inp.course.name)

        # Step 1: Planner
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            logger.warning("Planner LLM failed, use deterministic fallback: %s",
                           plan.message)
            plan = self._fallback_plan(inp, plan.message)

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

    def draft(self, inp: AssignmentInput) -> PlanOutput:
        """仅生成任务拆解，严格不触发 Matcher/Timeline/Reporter。"""
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            plan = self._fallback_plan(inp, plan.message)
        start = inp.default_start_date
        end = inp.default_end_date or inp.deadline
        tasks = []
        for index, task in enumerate(plan.tasks, 1):
            stage = task.execution_stage or "实践中"
            tasks.append(task.model_copy(update={
                "order": task.order or index,
                "start_date": task.start_date or start,
                "end_date": task.end_date or end,
                "execution_stage": stage,
                "assignee_id": None,
                "collaborator_ids": [],
            }))
        return plan.model_copy(update={"tasks": tasks})

    def confirm(self, inp: AssignmentInput, plan: PlanOutput) -> FullPlan:
        """用户确认任务草案后，才执行自动分工、排期与报告。"""
        qa_matrix = self._step_matcher(plan, inp.members)
        timeline = self._step_timeline(plan, inp.deadline.isoformat(), qa_matrix, inp.members)
        if isinstance(timeline, AgentError):
            timeline = TimelineOutput(tasks=[], critical_path=[], total_days=0, note=timeline.message)
        report = self._step_reporter(plan, timeline, qa_matrix)
        if isinstance(report, AgentError):
            report = ReportOutput(summary="报告生成失败", risk_note=report.message)
        by_task = {a.task_id: a for a in qa_matrix.assignments}
        assigned_tasks = [
            t.model_copy(update={
                "assignee_id": by_task[t.id].presenter if t.id in by_task else None,
                "collaborator_ids": ([by_task[t.id].qa_primary] if t.id in by_task and by_task[t.id].qa_primary else [])
            }) for t in plan.tasks
        ]
        return FullPlan(input=inp, plan=plan.model_copy(update={"tasks": assigned_tasks}),
                        timeline=timeline, qa_matrix=qa_matrix, report=report)

    # ──────────── 各步骤 ────────────

    def _step_planner(self, inp: AssignmentInput) -> PlanOutput | AgentError:
        # 为 Planner 提供丰富的成员信息（含技能和可用工时）
        members = [
            f"{m.name}(技能: {format_skills_for_prompt(m.skill_tags)}; "
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
                for s in (a.qa_support or []):
                    if s not in people:
                        people.append(s)
                assignments[a.task_id] = people
        return self.timeline.run(
            plan=plan, deadline=deadline,
            assignments=assignments, members=members,
        )

    def _step_reporter(self, plan: PlanOutput,
                       timeline: TimelineOutput,
                       qa_matrix: QAOutput) -> ReportOutput | AgentError:
        return self.reporter.run(plan=plan, timeline=timeline,
                                 qa_matrix=qa_matrix)
    @staticmethod
    def _fallback_plan(inp: AssignmentInput,
                       error_msg: str = "") -> PlanOutput:
        """Planner LLM 不可用时的确定性兜底计划。

        按 5 个标准阶段生成通用任务，根据团队总产能等比缩放工时，
        确保下游链路不中断。
        """
        text = f"{inp.course.description} {inp.background} {inp.requirements} {inp.additional_requirements}"
        if "秀米" in text or ("推送" in text and ("实践" in text or "公众号" in text)):
            specs = [
                ("确定推送主题和内容框架", "策划", 3, ["内容策划"], "实践前"),
                ("制定摄影和素材收集要求", "摄影", 2, ["摄影策划"], "实践前"),
                ("实践过程摄影", "摄影", 6, ["摄影"], "实践中"),
                ("活动记录与资料整理", "资料", 4, ["资料整理"], "实践中"),
                ("收集成员感想", "采访", 3, ["采访沟通"], "实践中"),
                ("推送文案撰写", "文案", 6, ["文案撰写"], "实践后"),
                ("图片筛选与处理", "设计", 4, ["图片处理"], "实践后"),
                ("秀米排版", "排版", 5, ["秀米排版"], "实践后"),
                ("内容审核与修改", "审核", 3, ["内容审核"], "实践后"),
                ("推送发布与数据反馈", "发布", 2, ["平台发布", "数据分析"], "实践后"),
            ]
            tasks = []
            for i, (name, category, hours, skills, stage) in enumerate(specs):
                deps = []
                if i == 5:
                    deps = ["T4", "T5"]
                elif i == 6:
                    deps = ["T3"]
                elif i == 7:
                    deps = ["T6", "T7"]
                elif i > 7:
                    deps = [f"T{i}"]
                tasks.append(SubTask(
                    id=f"T{i+1}", name=name, description=f"完成{name}并形成可验收成果",
                    category=category, estimated_hours=hours, required_skills=skills,
                    execution_stage=stage, dependencies=deps, order=i+1))
            return PlanOutput(
                tasks=tasks,
                summary="按内容、摄影、资料、排版、审核和发布等专业流程拆解的推送任务草案。",
                reasoning="LLM 不可用时启用秀米推送专用兜底，仍不分配负责人。")

        # 团队总产能（默认 3 人 × 20h = 60h 作为基准）
        total_capacity = sum(m.available_hours for m in inp.members) or 60.0
        scale = max(0.5, min(2.0, total_capacity / 60.0))
        base_hours = {0: (4, "需求分析与调研", ["调研", "文档"]),
                      1: (6, "方案设计与技术选型", ["设计", "架构"]),
                      2: (8, "核心模块开发", ["开发", "编程"]),
                      3: (6, "测试与联调", ["测试", "调试"]),
                      4: (4, "文档撰写与答辩准备", ["文档", "PPT"])}
        tasks: list[SubTask] = []
        for i in range(5):
            hours, name, skills = base_hours[i]
            hours = round(hours * scale)
            deps = [tasks[i - 1].id] if i > 0 else []
            tasks.append(SubTask(
                id=f"T{i + 1}",
                name=name,
                description=f"{name}：根据课程要求完成对应工作",
                estimated_hours=float(hours),
                dependencies=deps,
                required_skills=skills,
            ))
        return PlanOutput(
            tasks=tasks,
            summary=("Planner 不可用，已生成确定性兜底计划（5 个标准阶段）。"
                     f"错误信息：{error_msg}" if error_msg
                     else "确定性兜底计划（5 个标准阶段）"),
            reasoning=("LLM 规划失败，按需求→设计→开发→测试→文档的标准"
                       "瀑布模型生成默认计划，确保下游可用。"),
        )
