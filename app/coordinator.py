"""
Coordinator 总调度
负责：编排 Planner -> Matcher -> Timeline 核心主链路
同时负责：输出校验 + 重试 + 日志
"""

from __future__ import annotations

import logging
import re
from app.models.schemas import (
    AgentError, AssignmentInput, FullPlan, PlanOutput,
    QAOutput, TimelineOutput, ReportOutput, ReflectionOutput, SubTask, TaskStatus,
    ProjectModule,
)
from app.agents.scoring import format_skills_for_prompt
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.scoring import assign_with_balance, enhance
from app.agents.timeline import TimelineAgent, sync_task_dates
from app.agents.reporter import ReporterAgent
from app.agents.reflection import ReflectionAgent
from app.agents.validation import ensure_large_project_structure
from app.file_analysis import _classify_requirement_unit, _strip_dangling_brackets
from app.services.duration_estimator import (
    build_duration_context, calibrate_plan_estimates,
)
from app.performance import PerformanceTrace, request_trace, stage

logger = logging.getLogger(__name__)


def should_reflect(
    plan: PlanOutput,
    timeline: TimelineOutput,
    qa_matrix: QAOutput,
    total_capacity: float = 0.0,
    deadline=None,
) -> tuple[bool, list[str]]:
    """确定性 Reflection 风险门；返回是否调用 LLM 及触发原因。"""
    reasons: list[str] = []
    task_ids = {task.id for task in plan.tasks}
    assignments = {item.task_id: item for item in qa_matrix.assignments}

    if any(task.id not in assignments or not assignments[task.id].presenter
           for task in plan.tasks if task.status != TaskStatus.completed):
        reasons.append("unassigned_task")

    scores = [item.score for item in qa_matrix.assignments
              if item.task_id in task_ids and item.score > 0]
    if scores and min(scores) < 0.35:
        reasons.append("low_skill_match")

    workload = [value for value in (qa_matrix.workload or {}).values()
                if value > 0]
    if len(workload) >= 2:
        avg = sum(workload) / len(workload)
        if max(workload) > max(avg * 1.6, avg + 4):
            reasons.append("workload_imbalance")

    total_hours = sum(task.estimated_hours for task in plan.tasks)
    if total_capacity > 0 and total_hours > total_capacity * 1.1:
        reasons.append("capacity_overload")

    if len(timeline.critical_path) > 5:
        reasons.append("long_critical_path")

    if "依赖环" in (timeline.note or "") or "检测到依赖环" in (timeline.reasoning or ""):
        reasons.append("dependency_cycle")

    if deadline is not None and timeline.total_days > 0:
        from app.config import today as _today
        remaining = max(0, (deadline - _today()).days)
        if timeline.total_days > remaining:
            reasons.append("deadline_risk")

    return bool(reasons), list(dict.fromkeys(reasons))


class Coordinator:
    """总调度器，编排多 Agent 主链路。"""

    def __init__(self):
        self.planner = PlannerAgent()
        self.matcher = MatcherAgent()
        self.timeline = TimelineAgent()
        self.reporter = ReporterAgent()
        self.reflector = ReflectionAgent()

    def run(self, inp: AssignmentInput) -> FullPlan:
        """执行完整主链路。"""
        logger.info("Coordinator started: %s", inp.course.name)

        trace = PerformanceTrace(task_count=0, member_count=len(inp.members))
        with request_trace(trace):
            result = self._run_traced(inp, trace)
        return result.model_copy(update={"performance": trace.finish()})

    def _run_traced(self, inp: AssignmentInput,
                    trace: PerformanceTrace) -> FullPlan:
        """带请求级埋点的完整主链路。"""

        # Step 1: Planner
        with stage("Planner"):
            plan = self._step_planner(inp)
            if isinstance(plan, AgentError):
                logger.warning("Planner LLM failed, use deterministic fallback: %s",
                               plan.message)
                plan = (self._fallback_large_project_plan(inp, plan.message)
                        if inp.project_mode == "large_project"
                        else self._fallback_plan(inp, plan.message))
            plan = calibrate_plan_estimates(plan)
            if inp.project_mode == "large_project":
                plan = ensure_large_project_structure(plan)
        trace.task_count = len(plan.tasks)

        # Step 2: Matcher（B3：LLM + 确定性评分兜底）
        with stage("Matcher"):
            qa_matrix = self._step_matcher(plan, inp.members)

        # 回填负责人到 plan tasks（与 confirm 路径一致），让风险分析、
        # 前端任务列表和导出文档都能正确显示负责人
        by_task = {a.task_id: a for a in qa_matrix.assignments}
        plan = plan.model_copy(update={"tasks": [
            t.model_copy(update={
                "assignee_id": by_task[t.id].presenter if t.id in by_task else None,
                "collaborator_ids": (
                    ([by_task[t.id].qa_primary] if by_task[t.id].qa_primary else [])
                    + list(by_task[t.id].qa_support or [])
                ) if t.id in by_task else []
            }) for t in plan.tasks
        ]})
        if inp.project_mode == "large_project":
            plan = self._sync_module_owners(plan)

        # Step 3: Timeline（回填 QA 矩阵的负责人，传入成员信息）
        with stage("Timeline"):
            timeline = self._step_timeline(
                plan, inp.deadline.isoformat(), qa_matrix, inp.members)
        if isinstance(timeline, AgentError):
            logger.warning("Timeline failed, skip timeline: %s",
                           timeline.message)
            timeline = TimelineOutput(tasks=[], critical_path=[],
                                      total_days=0,
                                      note="Timeline failed: " + timeline.message)
        else:
            # 时间线是排期事实来源：把每项任务的起止日期回填到 plan.tasks，
            # 否则草案阶段的默认项目窗口日期会覆盖真实排期，
            # 资源日历会把整个窗口（含成员不可用日）都算成有任务。
            plan = sync_task_dates(plan, timeline)

        trace.mark_first_useful_result()

        # 核心结果优先：Reporter 仅在用户打开报告页或导出报告时调用；
        # Reflection 也不再占用首次响应关键路径。保留稳定的 FullPlan schema，
        # 用空报告明确表示“尚未生成”，而不是伪装成已生成报告。
        trace.reflection_executed = False
        trace.reflection_reasons = []
        trace.reporter_blocks_response = False
        report = ReportOutput(summary="")

        logger.info("Coordinator completed")
        return FullPlan(
            input=inp,
            plan=plan,
            timeline=timeline,
            qa_matrix=qa_matrix,
            report=report,
            reflection=None,
        )

    def draft(self, inp: AssignmentInput) -> PlanOutput:
        """仅生成任务拆解，严格不触发 Matcher/Timeline/Reporter。

        顺序 B：Planner 已直接给出 assignee_id（基于成员能力拆任务），
        草案阶段保留这些分配，让用户在确认前就能看到"谁负责什么"。
        用户可在草案编辑界面调整 assignee_id，确认后由 Matcher 做负载均衡微调。
        """
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            plan = (self._fallback_large_project_plan(inp, plan.message)
                    if inp.project_mode == "large_project"
                    else self._fallback_plan(inp, plan.message))
        plan = calibrate_plan_estimates(plan)
        if inp.project_mode == "large_project":
            plan = ensure_large_project_structure(plan)
        start = inp.default_start_date
        end = inp.default_end_date or inp.deadline
        tasks = []
        for index, task in enumerate(plan.tasks, 1):
            stage = task.execution_stage or "执行"
            # 保留 Planner 给的 assignee_id（顺序 B）；LLM 未给则为 None
            tasks.append(task.model_copy(update={
                "order": task.order or index,
                "start_date": task.start_date or start,
                "end_date": task.end_date or end,
                "execution_stage": stage,
            }))
        return plan.model_copy(update={"tasks": tasks})

    def confirm(
        self,
        inp: AssignmentInput,
        plan: PlanOutput,
        *,
        use_ai_reflection: bool = True,
    ) -> FullPlan:
        """用户确认任务草案后，执行自动分工、排期与报告。

        顺序 B：Planner 已在拆任务时给出 assignee_id（基于成员能力），
        confirm 阶段用 assign_with_balance 做负载均衡微调——
        保留 Planner 的初始分配，只在负载严重不均时搬运负责人。
        """
        trace = PerformanceTrace(
            task_count=len(plan.tasks), member_count=len(inp.members))
        with request_trace(trace):
            result = self._confirm_traced(
                inp, plan, use_ai_reflection=use_ai_reflection, trace=trace)
        return result.model_copy(update={"performance": trace.finish()})

    def _confirm_traced(self, inp: AssignmentInput, plan: PlanOutput, *,
                        use_ai_reflection: bool,
                        trace: PerformanceTrace) -> FullPlan:
        if inp.project_mode == "large_project":
            plan = ensure_large_project_structure(plan)
        with stage("Matcher"):
            qa_matrix = (
                QAOutput(assignments=[], note="B3确定性兜底：暂无骨干成员")
                if not inp.members
                else assign_with_balance(plan, inp.members)
            )
        with stage("Timeline"):
            timeline = self._step_timeline(
                plan, inp.deadline.isoformat(), qa_matrix, inp.members)
        if isinstance(timeline, AgentError):
            timeline = TimelineOutput(tasks=[], critical_path=[], total_days=0, note=timeline.message)
        else:
            plan = sync_task_dates(plan, timeline)
        trace.mark_first_useful_result()
        with stage("Reporter"):
            report = ReportOutput(
                summary=plan.summary,
                timeline_section=f"共 {len(timeline.tasks)} 项排期，总工期 {timeline.total_days} 天。",
                qa_matrix_section="\n".join(
                    f"{a.task_name}：{a.presenter}（{a.reasoning}）"
                    for a in qa_matrix.assignments),
                risk_note=self._build_risk_note(
                    plan, timeline, qa_matrix, inp.members, inp.deadline),
            )
        by_task = {a.task_id: a for a in qa_matrix.assignments}
        assigned_tasks = [
            t.model_copy(update={
                "assignee_id": by_task[t.id].presenter if t.id in by_task else None,
                "collaborator_ids": (
                    ([by_task[t.id].qa_primary] if by_task[t.id].qa_primary else [])
                    + list(by_task[t.id].qa_support or [])
                ) if t.id in by_task else []
            }) for t in plan.tasks
        ]
        final_plan = plan.model_copy(update={"tasks": assigned_tasks})
        if inp.project_mode == "large_project":
            final_plan = self._sync_module_owners(final_plan)
        # P1-3: confirm 路径也执行 Reflection 审查（确定性兜底，不阻塞主流程）
        total_capacity = sum(m.available_hours for m in inp.members)
        reflect, reasons = should_reflect(
            final_plan, timeline, qa_matrix, total_capacity, inp.deadline)
        trace.reflection_executed = bool(use_ai_reflection and reflect)
        trace.reflection_reasons = reasons
        with stage("Reflection"):
            reflection = (
                self._step_reflection(
                    final_plan, timeline, qa_matrix, total_capacity)
                if use_ai_reflection and reflect
                else self.reflector._deterministic_reflect(
                    final_plan, timeline, qa_matrix, total_capacity)
            )
        return FullPlan(input=inp, plan=final_plan,
                        timeline=timeline, qa_matrix=qa_matrix, report=report,
                        reflection=reflection)

    # ──────────── 各步骤 ────────────


    @staticmethod
    def _build_risk_note(plan, timeline, qa_matrix, members, deadline=None) -> str:
        """生成详细的风险提示"""
        risks = []
        
        # 1. 工作量分析
        workload = qa_matrix.workload or {}
        values = [v for v in workload.values() if v > 0]
        if values:
            avg = sum(values) / len(values)
            for name, hours in workload.items():
                if hours == 0:
                    risks.append(f'- **{name}**：未分配任何任务，可能存在分工遗漏')
                elif hours > avg * 1.35 and hours - avg > 2:
                    risks.append(f'- **{name}**：承担 {hours:g}h，显著高于团队平均 {avg:.1f}h，建议检查是否过载')
                elif hours < avg * 0.5 and avg - hours > 3:
                    risks.append(f'- **{name}**：仅承担 {hours:g}h，远低于团队平均 {avg:.1f}h，可考虑增加任务')
        
        # 2. 总工时与产能对比
        total_hours = sum(t.estimated_hours for t in plan.tasks)
        capacity = sum(m.available_hours for m in members) or 1
        if total_hours > capacity * 1.1:
            risks.append(f'- **总工时超负荷**：任务总计 {total_hours:g}h，超过团队总可用 {capacity:g}h，建议削减低优先级任务或延长排期')
        elif total_hours < capacity * 0.5:
            risks.append(f'- **产能利用率低**：任务总计 {total_hours:g}h，仅占团队可用 {capacity:g}h 的 {total_hours/capacity*100:.0f}%，可考虑增加任务深度')
        
        # 3. 关键路径风险
        if timeline.critical_path and len(timeline.critical_path) >= max(1, len(plan.tasks) * 0.7):
            risks.append(f'- **关键路径过长**：{len(timeline.critical_path)}/{len(plan.tasks)} 个任务处于关键路径，任一延迟都会影响整体交付')
        
        # 4. 未分配任务
        unassigned = [t for t in plan.tasks if not t.assignee_id]
        if unassigned:
            risks.append(f'- **{len(unassigned)} 个任务未分配负责人**：{", ".join(t.name for t in unassigned[:3])}{"等" if len(unassigned) > 3 else ""}')
        
        # 5. 技能匹配风险
        skill_mismatches = []
        for task in plan.tasks:
            if task.required_skills and task.assignee_id:
                assignee = next((m for m in members if m.name == task.assignee_id), None)
                if assignee:
                    member_skills = set(s.name if hasattr(s, 'name') else s for s in assignee.skill_tags)
                    required = set(task.required_skills)
                    if not required.intersection(member_skills):
                        skill_mismatches.append(task.name)
        if skill_mismatches:
            risks.append(f'- **技能匹配度低**：{", ".join(skill_mismatches[:2])}{"等" if len(skill_mismatches) > 2 else ""} 的负责人可能缺乏相关技能')
        
        # 6. 时间线风险
        if timeline.total_days == 0 and timeline.tasks:
            risks.append('- **时间线计算异常**：总工期为 0 天，请检查任务依赖关系')
        
        if deadline and timeline.total_days > 0:
            from app.config import today as _today
            remaining_days = max(0, (deadline - _today()).days)
            if timeline.total_days > remaining_days:
                risks.append(
                    f'- **可能延期**：预估工期 {timeline.total_days} 工作日，'
                    f'超过截止日期剩余 {remaining_days} 天')
        
        if not risks:
            return '当前方案整体风险较低，建议关注任务执行过程中的突发情况。'
        
        return chr(10).join(risks)

    def _step_planner(self, inp: AssignmentInput) -> PlanOutput | AgentError:
        # 大型项目模式：走独立的 Planner 提示词
        if inp.project_mode == "large_project":
            return self._step_planner_large_project(inp)
        # 小组作业：顺序 B，让 Planner 看着成员能力拆任务并直接给出 assignee_id
        # 双输入模式：tags 模式用技能标签，bio 模式用自然语言简介
        members = []
        for m in inp.members:
            if m.profile_mode == "bio" and m.bio:
                members.append(
                    f"{m.name}(简介: {m.bio}; "
                    f"总可用: {m.available_hours}h; "
                    f"每日可用: {m.daily_available_hours}h)")
            else:
                members.append(
                    f"{m.name}(技能: {format_skills_for_prompt(m.skill_tags)}; "
                    f"总可用: {m.available_hours}h; "
                    f"每日可用: {m.daily_available_hours}h)")
        extracted = _format_requirement_analysis(
            inp.requirement_analysis, inp.uploaded_files)
        extra = "\n".join(
            item for item in (inp.additional_requirements, inp.requirements, extracted)
            if item and item.strip())
        duration_query = " ".join((
            inp.course.name, inp.course.description, inp.background,
            inp.requirements, inp.additional_requirements, extracted,
        ))
        duration_context = build_duration_context(duration_query)
        extra = "\n\n".join(item for item in (extra, duration_context) if item)
        return self.planner.run(
            course_name=inp.course.name,
            course_description=inp.course.description,
            members=members,
            deadline=inp.deadline.isoformat(),
            extra=extra,
        )

    def _step_planner_large_project(self, inp: AssignmentInput) -> PlanOutput | AgentError:
        """大型项目模式：先拆任务再认领招募。

        用 LARGE_PROJECT_PLANNER 提示词，让 Planner 按交付物和流程拆任务，
        骨干认领负责，需要志愿者的任务标注 extra_helpers_needed。
        """
        members = [
            f"{m.name}(技能: {format_skills_for_prompt(m.skill_tags)}; "
            f"总可用: {m.available_hours}h)"
            for m in inp.members]
        member_text = (
            "\n".join(members)
            if members
            else "（可先不填，由方案先拆大任务模块与子任务，后续再补骨干认领）"
        )
        extracted = _format_requirement_analysis(
            inp.requirement_analysis, inp.uploaded_files)
        extra = "\n".join(
            item for item in (inp.additional_requirements, inp.requirements, extracted)
            if item and item.strip())
        from app.llm.prompts import (
            LARGE_PROJECT_PLANNER_SYSTEM, LARGE_PROJECT_PLANNER_USER_TEMPLATE)
        user = LARGE_PROJECT_PLANNER_USER_TEMPLATE.format(
            course_name=inp.course.name,
            course_description=inp.course.description,
            members=member_text,
            deadline=inp.deadline.isoformat(),
            extra=extra or "无",
        )
        from app.llm.client import LLMClient
        result = LLMClient.get_shared().chat_structured(
            system_prompt=LARGE_PROJECT_PLANNER_SYSTEM,
            user_prompt=user,
            response_model=PlanOutput,
            temperature=0.3,
        )
        if isinstance(result, AgentError):
            return result
        result = result.model_copy(update={
            "tasks": [t.model_copy(update={"status": TaskStatus.pending}) for t in result.tasks]
        })
        try:
            from app.agents.validation import validate_plan
            return ensure_large_project_structure(validate_plan(result))
        except Exception:
            return ensure_large_project_structure(result)

    @staticmethod
    def _sync_module_owners(plan: PlanOutput) -> PlanOutput:
        """模块负责人自动回填给骨干，使“骨干认领模块”与任务分工保持一致。"""
        task_by_module: dict[str, list[str]] = {}
        for task in plan.tasks:
            if task.module_id:
                task_by_module.setdefault(task.module_id, []).append(task.assignee_id or "")
        updated_modules = []
        for module in plan.modules:
            owner = module.assignee_id
            if not owner:
                owners = [name for name in task_by_module.get(module.id, []) if name]
                owner = max(set(owners), key=owners.count) if owners else None
            updated_modules.append(module.model_copy(update={"assignee_id": owner}))
        return plan.model_copy(update={"modules": updated_modules})

    def _step_matcher(self, plan: PlanOutput,
                      members) -> QAOutput:
        """LLM 匹配成功 -> enhance 补分；失败 -> 确定性兜底。"""
        if not members:
            return QAOutput(assignments=[], note="暂无骨干成员，先保留模块与子任务结构")
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

    def _step_reflection(self, plan: PlanOutput, timeline: TimelineOutput,
                         qa_matrix: QAOutput,
                         total_capacity: float = 0.0) -> ReflectionOutput:
        """执行 Reflection 审查，永远不抛异常，失败时用确定性兜底。"""
        try:
            return self.reflector.run(
                plan=plan,
                timeline=timeline,
                qa_matrix=qa_matrix,
                total_capacity=total_capacity,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReflectionAgent unexpected error, use fallback: %s", exc)
            return self.reflector._deterministic_reflect(plan, timeline, qa_matrix, total_capacity)

    @staticmethod
    def _fallback_large_project_plan(inp: AssignmentInput,
                                     error_msg: str = "") -> PlanOutput:
        """大型项目 LLM 不可用时的确定性兜底：先拆模块，再拆子任务，骨干可后补。"""
        modules = [
            ProjectModule(id="M1", name="需求梳理与方案规划", order=1,
                          description="围绕项目目标、调研与交付边界，先形成可执行方案。"),
            ProjectModule(id="M2", name="核心内容制作与实施", order=2,
                          description="按方案组织主要交付物制作，需要外部参与者时单独招募。"),
            ProjectModule(id="M3", name="质量审核与成果整合", order=3,
                          description="审核质量、汇总各模块成果并形成完整报告。"),
            ProjectModule(id="M4", name="汇报演示与材料提交", order=4,
                          description="准备演示与提交材料，完成最终汇报和复盘。"),
        ]
        task_specs = [
            ("需求调研与目标确认", "调研", 3, ["调研", "沟通协调"], "准备", "M1", 0),
            ("方案框架与分工计划", "策划", 4, ["策划"], "准备", "M1", 0),
            ("核心内容制作", "执行", 8, ["组织执行"], "执行", "M2", 2),
            ("素材采集与整理", "素材", 5, ["素材整理"], "执行", "M2", 1),
            ("分模块实施推进", "执行", 6, ["执行"], "执行", "M2", 1),
            ("质量审核与修改", "审核", 4, ["质量审核"], "收尾", "M3", 0),
            ("成果整合与报告撰写", "文案", 6, ["文案撰写"], "收尾", "M3", 0),
            ("汇报演示与材料提交", "汇报", 4, ["表达", "演示"], "收尾", "M4", 1),
        ]
        tasks: list[SubTask] = []
        member_names = [m.name for m in inp.members]
        module_owners: dict[str, str] = {}
        for index, module in enumerate(modules):
            if member_names:
                module_owners[module.id] = member_names[index % len(member_names)]
        for i, (name, cat, hours, skills, stage, module_id, volunteers) in enumerate(task_specs):
            owner = module_owners.get(module_id)
            tasks.append(SubTask(
                id=f"T{i + 1}",
                name=name,
                module_id=module_id,
                description=f"{name}：根据项目要求完成对应工作，产出可检查的{cat}成果",
                estimated_hours=float(hours),
                dependencies=[f"T{i}"] if i > 0 else [],
                required_skills=skills,
                execution_stage=stage,
                assignee_id=owner,
                extra_helpers_needed=volunteers,
                suggested_people=1 + volunteers,
                order=i + 1,
            ))
        return PlanOutput(
            tasks=tasks,
            modules=modules,
            summary=("大型项目确定性兜底计划（4个模块、8项子任务，"
                     "先拆模块再拆子任务，骨干可按模块认领）"),
            reasoning=("LLM 规划失败，按需求梳理→内容制作→质量整合→汇报提交拆成模块，"
                       "子任务标注志愿者需求量；已填骨干时会先建议模块负责人。" if error_msg
                       else "确定性兜底计划"),
        )

    @staticmethod
    def _fallback_plan(inp: AssignmentInput,
                       error_msg: str = "") -> PlanOutput:
        """Planner LLM 不可用时的确定性兜底计划。

        按 5 个标准阶段生成通用任务，根据团队总产能等比缩放工时，
        确保下游链路不中断。
        """
        analysis_text = " ".join(
            str(value)
            for key, raw in (inp.requirement_analysis or {}).items()
            for value in ([raw] if isinstance(raw, str) else (raw or []))
            if key != "questions" and str(value).strip())
        text = (
            f"{inp.course.description} {inp.background} {inp.requirements} "
            f"{inp.additional_requirements} {analysis_text}")
        blueprint_plan = _fallback_blueprint_plan(inp, error_msg)
        if blueprint_plan is not None:
            return blueprint_plan
        if (not _specific_requirement_items(inp.requirement_analysis)
                and ("秀米" in text
                     or ("推送" in text
                         and ("实践" in text or "公众号" in text)))):
            specs = [
                ("确定推送主题和内容框架", "策划", 3, ["内容策划"], "准备"),
                ("制定摄影和素材收集要求", "摄影", 2, ["摄影策划"], "准备"),
                ("实践过程摄影", "摄影", 6, ["摄影"], "执行"),
                ("活动记录与资料整理", "资料", 4, ["资料整理"], "执行"),
                ("收集成员感想", "采访", 3, ["采访沟通"], "执行"),
                ("推送文案撰写", "文案", 6, ["文案撰写"], "收尾"),
                ("图片筛选与处理", "设计", 4, ["图片处理"], "收尾"),
                ("秀米排版", "排版", 5, ["秀米排版"], "收尾"),
                ("内容审核与修改", "审核", 3, ["内容审核"], "收尾"),
                ("推送发布与数据反馈", "发布", 2, ["平台发布", "数据分析"], "收尾"),
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
                    execution_stage=stage, dependencies=deps, order=i+1,
                    suggested_people=2 if "摄影" in name else 1))
            return PlanOutput(
                tasks=tasks,
                summary="按内容、摄影、资料、排版、审核和发布等专业流程拆解的推送任务草案。",
                reasoning="LLM 不可用时启用秀米推送专用兜底，仍不分配负责人。")

        # 根据已提取的要求和常见交付流程生成领域化兜底，不再只返回通用 5 阶段。
        specs = _domain_fallback_specs(
            text, inp.requirement_analysis, inp.course.name)
        if len(specs) > 2:
            tasks = []
            for i, spec in enumerate(specs):
                deps = [f"T{i}"] if i > 0 and spec[4] != "执行" else []
                tasks.append(SubTask(
                    id=f"T{i+1}", name=spec[0],
                    description=_fallback_description(
                        spec[0], spec[1], spec[6], inp),
                    category=spec[1], estimated_hours=spec[2],
                    required_skills=spec[3], execution_stage=spec[4],
                    dependencies=deps, suggested_people=spec[5], order=i+1))
            return PlanOutput(
                tasks=tasks,
                summary=("已根据项目背景、交付物和专业流程生成可编辑的快速草案。"
                         if error_msg == "快速模式" else
                         "AI 拆解本次未成功，系统已根据文件和项目要求生成可编辑草案。"),
                reasoning=(
                    "文件解析正常；本地规则按动作、专业能力、执行阶段和交付物拆解，"
                    "未增加第二次模型等待。"
                    if error_msg == "快速模式" else
                    f"{_friendly_fallback_cause(error_msg)}；文件解析正常，"
                    "系统已自动使用本地规则继续生成，未丢失上传文件。"))

        # 工时由任务本身的范围决定。团队产能只供后续 Reflection 判断
        # 是否超载，不得反向放大或压缩同一项工作的预计人时。
        base_hours = {0: (4, "需求分析与调研", ["调研", "文档"]),
                      1: (6, "方案设计与技术选型", ["设计", "架构"]),
                      2: (8, "核心模块开发", ["开发", "编程"]),
                      3: (6, "测试与联调", ["测试", "调试"]),
                      4: (4, "文档撰写与汇报材料", ["文档", "PPT"])}
        # 按团队总产能自适应阶段数：小团队砍掉测试/文档，保留核心链路
        total_capacity = sum(m.available_hours for m in inp.members)
        num_stages = 3 if total_capacity <= 30 else (4 if total_capacity <= 60 else 5)
        tasks: list[SubTask] = []
        for i in range(num_stages):
            hours, name, skills = base_hours[i]
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
            summary=("Planner 不可用，已生成确定性兜底计划"
                     f"（{num_stages} 个标准阶段）。"
                     f"错误信息：{error_msg}" if error_msg
                     else f"确定性兜底计划（{num_stages} 个标准阶段）"),
            reasoning=("LLM 规划失败，按需求→设计→开发→测试→文档的标准"
                       "瀑布模型生成默认计划，确保下游可用。"),
        )


def _domain_fallback_specs(
        text: str, analysis: dict, project_name: str = "项目") -> list[tuple]:
    """生成 5-12 项专业化任务。

    每项为（名称、类别、工时、技能、阶段、人数、对应文件原文）。
    本地快速模式直接复用文件提炼结果，不增加任何模型调用。
    """
    lowered = text.lower()
    specs: list[tuple] = []

    def add(name, category, hours, skills, stage, people=1, source=""):
        if name not in {item[0] for item in specs}:
            specs.append((
                name, category, hours, skills, stage, people, source))

    # 通用起始工作
    add("确认项目目标与交付标准", "策划", 2, ["需求分析", "沟通"], "准备")

    # 先落地文件中明确写出的动作/交付物，避免被通用行业模板淹没。
    for item in _specific_requirement_items(analysis)[:6]:
        name = _requirement_task_name(item)
        if name:
            add(name, _infer_category(name), _estimate_hours(name),
                _infer_skills(name), _infer_stage(name),
                _infer_people(name),
                _requirement_source_with_constraints(item, analysis))

    if any(word in lowered for word in ("调研", "问卷", "访谈", "调查")):
        add("设计调研方案与问题清单", "调研", 3, ["调研设计"], "准备")
        add("开展调研与资料采集", "调研", 6, ["访谈", "资料收集"], "执行", 2)
        add("整理并分析调研数据", "分析", 5, ["数据分析"], "收尾")
    if any(word in lowered for word in ("活动", "实践", "现场", "志愿")):
        focus = _project_focus(project_name)
        has_file_execution = any(
            spec[4] == "执行" and spec[6] for spec in specs)
        add(f"制定{focus}现场任务清单", "策划", 3, ["活动策划"], "准备")
        if not has_file_execution:
            add(f"开展{focus}现场任务", "执行", 6,
                ["组织协调"], "执行", 3)
        add(f"整理{focus}过程证据", "记录", 4,
            ["资料整理"], "执行", 2)
    if any(word in lowered for word in ("摄影", "照片", "拍摄", "视频")):
        add("制定拍摄清单与素材规范", "摄影", 2, ["摄影策划"], "准备")
        add("现场摄影与视频素材采集", "摄影", 6, ["摄影", "摄像"], "执行", 2)
        add("素材筛选与后期处理", "设计", 5, ["图片处理", "视频剪辑"], "收尾")
    if any(word in lowered for word in ("报告", "总结", "论文", "文档")):
        add("搭建报告结构与内容提纲", "文案", 2.5, ["内容策划"], "准备")
        add("撰写报告或总结正文", "文案", 6, ["文案撰写"], "收尾")
        add("数据、图表与附件整理", "资料", 4, ["数据可视化", "资料整理"], "收尾")
    if any(word in lowered for word in ("ppt", "答辩", "汇报", "展示")):
        add("设计汇报结构与演示逻辑", "策划", 2.5, ["汇报策划"], "收尾")
        add("制作演示文稿与视觉排版", "设计", 5, ["PPT", "视觉设计"], "收尾")
        add("汇报演练与问题准备", "汇报", 3, ["表达", "应答"], "收尾", 2)
    if any(word in lowered for word in ("开发", "系统", "网站", "程序", "小程序")):
        add("梳理功能需求与验收标准", "产品", 3, ["需求分析"], "准备")
        add("完成核心功能设计与实现", "开发", 10, ["技术开发"], "执行", 2)
        add("功能测试、修复与联调", "测试", 6, ["测试", "调试"], "收尾", 2)

    add("成果审核、修改与最终提交", "审核", 3, ["质量审核"], "收尾", 2)
    return specs[:12]


def _fallback_blueprint_plan(
        inp: AssignmentInput, error_msg: str) -> PlanOutput | None:
    """把文件分析器给出的任务蓝图直接转为草案，不依赖 LLM。"""
    blueprint = (inp.requirement_analysis or {}).get("task_blueprint", [])
    if not blueprint:
        return None

    tasks: list[SubTask] = []
    key_to_ids: dict[str, list[str]] = {}
    for item in blueprint[:36]:
        key = str(item.get("key", "")).strip()
        people_value = item.get("suggested_people", 1)
        members = inp.members if people_value == "all" else [None]
        created_ids: list[str] = []
        for member in members:
            task_id = f"T{len(tasks) + 1}"
            created_ids.append(task_id)
            dependencies = [
                dependency_id
                for dependency_key in item.get("depends_on", [])
                for dependency_id in key_to_ids.get(str(dependency_key), [])
            ]
            base_name = str(item.get("name", "文件要求任务")).strip()
            name = (
                f"{member.name}撰写个人总结报告"
                if member is not None and key == "personal_reports"
                else base_name)
            level = str(item.get("requirement_level", "必须")).strip()
            level_text = {
                "必须": "课程硬性要求",
                "建议": "手册建议项",
                "鼓励": "手册鼓励项",
            }.get(level, level)
            description = str(item.get("description", "")).strip()
            tasks.append(SubTask(
                id=task_id,
                name=name,
                description=f"{level_text}：{description}",
                category=str(item.get("category", "执行")),
                estimated_hours=max(
                    0.5, float(item.get("estimated_hours", 3))),
                required_skills=[
                    str(skill) for skill in item.get("required_skills", [])
                ],
                execution_stage=str(
                    item.get("execution_stage", "执行")),
                dependencies=dependencies,
                suggested_people=(
                    1 if member is not None
                    else max(1, min(10, int(people_value)))),
                order=len(tasks) + 1,
            ))
        if key:
            key_to_ids[key] = created_ids

    if error_msg == "快速模式":
        summary = (
            "已直接根据上传文件中的必须项、建议项和数量要求生成分层任务草案。")
        reasoning = (
            "文件解析成功；复杂成果已继续拆成策划、素材、文案、排版、"
            "剪辑、审核和提交等可执行步骤，全程未调用模型。")
    else:
        summary = (
            "AI 拆解本次未成功，系统已直接按上传文件中的明确要求生成任务草案。")
        reasoning = (
            f"{_friendly_fallback_cause(error_msg)}；这不代表文件解析失败。"
            "系统已使用文件任务蓝图继续生成，并区分课程硬性要求、建议项和鼓励项。")
    return PlanOutput(tasks=tasks, summary=summary, reasoning=reasoning)


def _friendly_fallback_cause(error_msg: str) -> str:
    """把内部 Agent 错误改写为用户能理解且不泄露配置细节的说明。"""
    lowered = (error_msg or "").lower()
    if "未配置" in error_msg or "auth" in lowered or "鉴权" in error_msg:
        return "原因：AI 服务鉴权未通过或访问凭据不可用"
    if any(word in lowered for word in ("timeout", "timed out", "connection", "connect")) \
            or any(word in error_msg for word in ("超时", "连接")):
        return "原因：AI 服务连接或响应超时（模型生成较慢，可重试或调大 LLM_TIMEOUT）"
    if any(word in lowered for word in ("parse", "json", "validation")) \
            or any(word in error_msg for word in ("格式", "解析", "校验")):
        return (
            "原因：AI 已返回内容，但其中存在缺少必填字段、字段类型错误或"
            "JSON 不完整，系统无法安全采用")
    return "原因：AI 服务本次没有返回可用的任务草案"


def _format_requirement_analysis(analysis: dict, files: list[dict]) -> str:
    """把本地文件提炼结果压缩成 Planner 易映射的结构化上下文。"""
    if not analysis:
        return ""
    sections = (
        ("项目目标", "project_goal", 2),
        ("核心任务", "core_tasks", 8),
        ("必须交付物", "required_deliverables", 8),
        ("建议/鼓励成果", "recommended_deliverables", 6),
        ("交付物", "deliverables", 8),
        ("时间要求", "time_requirements", 6),
        ("格式要求", "format_requirements", 6),
        ("限制条件", "constraints", 6),
        ("评价标准", "evaluation_criteria", 6),
    )
    names = "、".join(
        str(item.get("name", "")).strip()
        for item in files if item.get("name")) or "已上传文件"
    lines = [f"## 文件要求提炼（来源：{names}）"]
    blueprint = analysis.get("task_blueprint", []) or []
    task_requirements = analysis.get("task_requirements", []) or []
    if blueprint:
        lines.append("- 拆解规则：必须项不得遗漏；建议项和鼓励项需明确标注，不能冒充硬性要求。")
        lines.append("- 可执行任务蓝图：")
        for item in blueprint[:30]:
            level = str(item.get("requirement_level", "必须"))
            stage = str(item.get("execution_stage", ""))
            name = re.sub(r"\s+", " ", str(item.get("name", ""))).strip()
            description = re.sub(
                r"\s+", " ", str(item.get("description", ""))).strip()
            lines.append(
                f"  - [{level}][{stage}] {name}：{description[:180]}")
            if sum(len(line) for line in lines) >= 4800:
                break
    elif task_requirements:
        lines.append("- 任务与附属限制（限制只能写入任务说明，禁止单独生成任务）：")
        for mapping in task_requirements[:16]:
            task = re.sub(
                r"\s+", " ", str(mapping.get("task", ""))).strip()
            constraints = "；".join(
                re.sub(r"\s+", " ", str(value)).strip()
                for value in mapping.get("constraints", []) or []
                if str(value).strip())
            line = f"  - 任务：{task}"
            if constraints:
                line += f"；附属限制：{constraints}"
            lines.append(line[:500])
    for label, key, limit in sections:
        if blueprint and key in (
                "project_goal", "core_tasks", "deliverables"):
            continue
        if task_requirements and key == "core_tasks":
            continue
        raw = analysis.get(key, [])
        values = [raw] if isinstance(raw, str) else list(raw or [])
        cleaned = [re.sub(r"\s+", " ", str(value)).strip()[:220]
                   for value in values if str(value).strip()]
        if cleaned:
            lines.append(f"- {label}：" + "；".join(cleaned[:limit]))
        if sum(len(line) for line in lines) >= 5600:
            break
    if len(lines) == 1 and str(analysis.get("summary", "")).strip():
        lines.append(
            "- 其他原文要求：" +
            re.sub(r"\s+", " ", str(analysis["summary"])).strip()[:4000])
    return "\n".join(lines)[:6000]


def _specific_requirement_items(analysis: dict) -> list[str]:
    values: list[str] = []
    for key in ("core_tasks", "deliverables"):
        for item in (analysis or {}).get(key, []) or []:
            cleaned = re.sub(r"\s+", " ", str(item)).strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return values


def _requirement_source_with_constraints(item: str, analysis: dict) -> str:
    for mapping in (analysis or {}).get("task_requirements", []) or []:
        if str(mapping.get("task", "")).strip() != item:
            continue
        constraints = [
            str(value).strip()
            for value in mapping.get("constraints", []) or []
            if str(value).strip()
        ]
        if constraints:
            return f"{item}；相关限制：" + "；".join(constraints)
    return item


def _requirement_task_name(item: str) -> str:
    """从要求句中保留“动作 + 对象”，不凭空补造数量或成果。"""
    classified, _ = _classify_requirement_unit(item)
    if not classified:
        return ""
    cleaned = re.sub(
        r"^(?:核心)?(?:任务|要求|交付物|成果|目标)\s*[：:]\s*", "", item)
    cleaned = classified or cleaned
    cleaned = re.sub(r"^(?:需(?:要)?|必须|应当|应|请|负责)\s*", "", cleaned)
    actions = (
        "实现", "完成", "开发", "制作", "撰写", "编写", "拍摄", "收集",
        "发布", "设计", "开展", "组织", "召开", "形成", "提交", "整理",
        "分析", "排版", "审核", "录制", "搭建", "调研", "访谈", "宣讲",
        "测试", "部署", "演示",
    )
    positions = [cleaned.find(action) for action in actions
                 if cleaned.find(action) >= 0]
    if positions:
        cleaned = cleaned[min(positions):]
    cleaned = re.split(r"[；;。]", cleaned, maxsplit=1)[0]
    cleaned = _strip_dangling_brackets(cleaned.strip(" ，,：:"))
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", cleaned)
    cleaned = _strip_dangling_brackets(cleaned)
    if len(cleaned) < 4 or not any(action in cleaned for action in actions):
        return ""
    return cleaned[:32].rstrip("，,、")


def _project_focus(project_name: str) -> str:
    cleaned = re.sub(r"\s+", "", project_name or "项目")
    cleaned = re.sub(r"(课程|作业|项目)$", "", cleaned)
    return (cleaned or "项目")[:12]


def _fallback_description(
        name: str, category: str, source: str, inp: AssignmentInput) -> str:
    if source:
        requirement = re.sub(r"\s+", " ", source).strip()[:140]
        return (
            f"依据文件要求“{requirement}”完成{name}；产出可复核的{category}成果，"
            "并逐项核对其中的对象、数量、格式、时间和质量条件。")
    # 已建立“任务 -> 附属限制”映射时，不把某一任务的限制污染到所有通用任务。
    constraints = (
        [] if (inp.requirement_analysis or {}).get("task_requirements")
        else (inp.requirement_analysis or {}).get("constraints", [])[:2]
    )
    standard = "；".join(str(item) for item in constraints if str(item).strip())
    if standard:
        return (
            f"围绕“{inp.course.name}”完成{name}，产出可检查的{category}成果；"
            f"验收时对照文件限制：{standard[:160]}。")
    return (
        f"围绕“{inp.course.name}”完成{name}，明确处理对象并产出可检查的"
        f"{category}成果；提交前核对项目要求。")


def _infer_category(name: str) -> str:
    mapping = (
        (("拍摄", "摄影", "录制"), "摄影"),
        (("撰写", "文案"), "文案"),
        (("排版", "设计", "制作"), "设计"),
        (("调研", "访谈", "收集"), "调研"),
        (("分析",), "分析"),
        (("开发", "搭建"), "开发"),
        (("测试", "审核"), "审核"),
        (("发布", "提交"), "发布"),
    )
    return next((category for words, category in mapping
                 if any(word in name for word in words)), "执行")


def _estimate_hours(name: str) -> float:
    rules = [
        (("开发", "实现", "现场执行"), 8), (("拍摄", "采集", "调研"), 6),
        (("撰写", "制作", "排版", "分析"), 5), (("整理", "处理", "测试"), 4),
        (("审核", "演练", "方案"), 3), (("发布", "提交", "确认"), 2),
    ]
    return next((hours for words, hours in rules if any(word in name for word in words)), 3)


def _infer_skills(name: str) -> list[str]:
    mapping = {
        "拍摄": "摄影", "摄影": "摄影", "撰写": "文案撰写", "排版": "视觉设计",
        "开发": "技术开发", "测试": "测试", "分析": "数据分析", "调研": "调研",
        "答辩": "表达", "审核": "质量审核",
    }
    return list(dict.fromkeys(skill for word, skill in mapping.items() if word in name)) or ["组织执行"]


def _infer_stage(name: str) -> str:
    if any(word in name for word in (
            "提交", "发布", "审核", "总结", "分析", "撰写", "排版", "后期")):
        return "收尾"
    if any(word in name for word in ("方案", "标准", "设计", "准备")):
        return "准备"
    if any(word in name for word in ("现场", "采集", "开展", "执行")):
        return "执行"
    return "收尾"


def _infer_people(name: str) -> int:
    return 2 if any(word in name for word in ("现场", "拍摄", "采集", "联调", "演练")) else 1
