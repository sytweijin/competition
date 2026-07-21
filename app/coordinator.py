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
import re

from app.models.schemas import (
    AgentError, AssignmentInput, FullPlan, PlanOutput,
    QAOutput, TimelineOutput, ReportOutput, ReflectionOutput, SubTask,
)
from app.agents.scoring import format_skills_for_prompt
from app.agents.planner import PlannerAgent
from app.agents.matcher import MatcherAgent
from app.agents.scoring import assign_with_balance, enhance
from app.agents.timeline import TimelineAgent
from app.agents.reporter import ReporterAgent
from app.agents.reflection import ReflectionAgent
from app.file_analysis import _classify_requirement_unit, _strip_dangling_brackets
from app.services.duration_estimator import (
    build_duration_context, calibrate_plan_estimates,
)

logger = logging.getLogger(__name__)


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

        # Step 1: Planner
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            logger.warning("Planner LLM failed, use deterministic fallback: %s",
                           plan.message)
            plan = self._fallback_plan(inp, plan.message)
        plan = calibrate_plan_estimates(plan)

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

        # Step 5: Reflection（C4）
        total_capacity = sum(m.available_hours for m in inp.members)
        reflection = self._step_reflection(plan, timeline, qa_matrix, total_capacity)

        logger.info("Coordinator completed")
        return FullPlan(
            input=inp,
            plan=plan,
            timeline=timeline,
            qa_matrix=qa_matrix,
            report=report,
            reflection=reflection,
        )

    def draft(self, inp: AssignmentInput) -> PlanOutput:
        """仅生成任务拆解，严格不触发 Matcher/Timeline/Reporter。"""
        plan = self._step_planner(inp)
        if isinstance(plan, AgentError):
            plan = self._fallback_plan(inp, plan.message)
        plan = calibrate_plan_estimates(plan)
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
        # 确认阶段使用可解释的确定性评分，避免 Matcher + Reporter 两次串行 LLM 等待。
        qa_matrix = assign_with_balance(plan, inp.members)
        timeline = self._step_timeline(plan, inp.deadline.isoformat(), qa_matrix, inp.members)
        if isinstance(timeline, AgentError):
            timeline = TimelineOutput(tasks=[], critical_path=[], total_days=0, note=timeline.message)
        report = ReportOutput(
            summary=plan.summary,
            timeline_section=f"共 {len(timeline.tasks)} 项排期，总工期 {timeline.total_days} 天。",
            qa_matrix_section="\n".join(
                f"{a.task_name}：{a.presenter}（{a.reasoning}）"
                for a in qa_matrix.assignments),
            risk_note=qa_matrix.note,
        )
        by_task = {a.task_id: a for a in qa_matrix.assignments}
        assigned_tasks = [
            t.model_copy(update={
                "assignee_id": by_task[t.id].presenter if t.id in by_task else None,
                "collaborator_ids": (
                    ([by_task[t.id].qa_primary] if by_task[t.id].qa_primary else [])
                    + list(by_task[t.id].qa_support or [])
                )[:max(0, t.suggested_people - 1)] if t.id in by_task else []
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
                deps = [f"T{i}"] if i > 0 and spec[4] != "实践中" else []
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
                      4: (4, "文档撰写与答辩准备", ["文档", "PPT"])}
        tasks: list[SubTask] = []
        for i in range(5):
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
            summary=("Planner 不可用，已生成确定性兜底计划（5 个标准阶段）。"
                     f"错误信息：{error_msg}" if error_msg
                     else "确定性兜底计划（5 个标准阶段）"),
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
    add("确认项目目标与交付标准", "策划", 2, ["需求分析", "沟通"], "实践前")

    # 先落地文件中明确写出的动作/交付物，避免被通用行业模板淹没。
    for item in _specific_requirement_items(analysis)[:6]:
        name = _requirement_task_name(item)
        if name:
            add(name, _infer_category(name), _estimate_hours(name),
                _infer_skills(name), _infer_stage(name),
                _infer_people(name),
                _requirement_source_with_constraints(item, analysis))

    if any(word in lowered for word in ("调研", "问卷", "访谈", "调查")):
        add("设计调研方案与问题清单", "调研", 3, ["调研设计"], "实践前")
        add("开展调研与资料采集", "调研", 6, ["访谈", "资料收集"], "实践中", 2)
        add("整理并分析调研数据", "分析", 5, ["数据分析"], "实践后")
    if any(word in lowered for word in ("活动", "实践", "现场", "志愿")):
        focus = _project_focus(project_name)
        has_file_execution = any(
            spec[4] == "实践中" and spec[6] for spec in specs)
        add(f"制定{focus}现场任务清单", "策划", 3, ["活动策划"], "实践前")
        if not has_file_execution:
            add(f"开展{focus}现场任务", "执行", 6,
                ["组织协调"], "实践中", 3)
        add(f"整理{focus}过程证据", "记录", 4,
            ["资料整理"], "实践中", 2)
    if any(word in lowered for word in ("摄影", "照片", "拍摄", "视频")):
        add("制定拍摄清单与素材规范", "摄影", 2, ["摄影策划"], "实践前")
        add("现场摄影与视频素材采集", "摄影", 6, ["摄影", "摄像"], "实践中", 2)
        add("素材筛选与后期处理", "设计", 5, ["图片处理", "视频剪辑"], "实践后")
    if any(word in lowered for word in ("报告", "总结", "论文", "文档")):
        add("搭建报告结构与内容提纲", "文案", 2.5, ["内容策划"], "实践前")
        add("撰写报告或总结正文", "文案", 6, ["文案撰写"], "实践后")
        add("数据、图表与附件整理", "资料", 4, ["数据可视化", "资料整理"], "实践后")
    if any(word in lowered for word in ("ppt", "答辩", "汇报", "展示")):
        add("设计汇报结构与演示逻辑", "策划", 2.5, ["汇报策划"], "实践后")
        add("制作演示文稿与视觉排版", "设计", 5, ["PPT", "视觉设计"], "实践后")
        add("答辩演练与问题准备", "答辩", 3, ["表达", "应答"], "实践后", 2)
    if any(word in lowered for word in ("开发", "系统", "网站", "程序", "小程序")):
        add("梳理功能需求与验收标准", "产品", 3, ["需求分析"], "实践前")
        add("完成核心功能设计与实现", "开发", 10, ["技术开发"], "实践中", 2)
        add("功能测试、修复与联调", "测试", 6, ["测试", "调试"], "实践后", 2)

    add("成果审核、修改与最终提交", "审核", 3, ["质量审核"], "实践后", 2)
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
                    item.get("execution_stage", "实践中")),
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
    if any(word in lowered for word in ("timeout", "connection", "connect")) \
            or any(word in error_msg for word in ("超时", "连接")):
        return "原因：AI 服务连接或响应超时"
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
        return "实践后"
    if any(word in name for word in ("方案", "标准", "设计", "准备")):
        return "实践前"
    if any(word in name for word in ("现场", "采集", "开展", "执行")):
        return "实践中"
    return "实践后"


def _infer_people(name: str) -> int:
    return 2 if any(word in name for word in ("现场", "拍摄", "采集", "联调", "演练")) else 1
