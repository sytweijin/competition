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
                    execution_stage=stage, dependencies=deps, order=i+1,
                    suggested_people=2 if "摄影" in name else 1))
            return PlanOutput(
                tasks=tasks,
                summary="按内容、摄影、资料、排版、审核和发布等专业流程拆解的推送任务草案。",
                reasoning="LLM 不可用时启用秀米推送专用兜底，仍不分配负责人。")

        # 根据已提取的要求和常见交付流程生成领域化兜底，不再只返回通用 5 阶段。
        specs = _domain_fallback_specs(text, inp.requirement_analysis)
        if specs:
            tasks = []
            for i, spec in enumerate(specs):
                deps = [f"T{i}"] if i > 0 and spec[4] != "实践中" else []
                tasks.append(SubTask(
                    id=f"T{i+1}", name=spec[0],
                    description=f"完成{spec[0]}，形成可检查、可交付的成果",
                    category=spec[1], estimated_hours=spec[2],
                    required_skills=spec[3], execution_stage=spec[4],
                    dependencies=deps, suggested_people=spec[5], order=i+1))
            return PlanOutput(
                tasks=tasks,
                summary="模型暂时不可用，已根据项目背景、交付物和专业流程生成可编辑的领域化草案。",
                reasoning="本地兜底按动作、专业能力、执行阶段和交付物拆解；请在确认前调整工时、日期和人数。")

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


def _domain_fallback_specs(text: str, analysis: dict) -> list[tuple]:
    """从项目文本生成 5-12 项专业化任务：(名称, 类别, 工时, 技能, 阶段, 人数)。"""
    lowered = text.lower()
    specs: list[tuple] = []

    def add(name, category, hours, skills, stage, people=1):
        if name not in {item[0] for item in specs}:
            specs.append((name, category, hours, skills, stage, people))

    # 通用起始工作
    add("确认项目目标与交付标准", "策划", 2, ["需求分析", "沟通"], "实践前")
    if any(word in lowered for word in ("调研", "问卷", "访谈", "调查")):
        add("设计调研方案与问题清单", "调研", 3, ["调研设计"], "实践前")
        add("开展调研与资料采集", "调研", 6, ["访谈", "资料收集"], "实践中", 2)
        add("整理并分析调研数据", "分析", 5, ["数据分析"], "实践后")
    if any(word in lowered for word in ("活动", "实践", "现场", "志愿")):
        add("制定现场执行与记录方案", "策划", 3, ["活动策划"], "实践前")
        add("现场执行与过程协调", "执行", 6, ["组织协调"], "实践中", 3)
        add("活动过程记录与资料归档", "记录", 4, ["资料整理"], "实践中", 2)
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

    # 文件提取出的核心任务用于补充领域词汇，最多补 4 项。
    for item in (analysis or {}).get("core_tasks", [])[:4]:
        name = item[:36].strip()
        if 4 <= len(name) <= 36:
            add(name, "执行", _estimate_hours(name), _infer_skills(name), _infer_stage(name),
                _infer_people(name))
    add("成果审核、修改与最终提交", "审核", 3, ["质量审核"], "实践后", 2)
    return specs[:12]


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
    if any(word in name for word in ("方案", "标准", "设计", "准备")):
        return "实践前"
    if any(word in name for word in ("现场", "采集", "开展", "执行")):
        return "实践中"
    return "实践后"


def _infer_people(name: str) -> int:
    return 2 if any(word in name for word in ("现场", "拍摄", "采集", "联调", "演练")) else 1
