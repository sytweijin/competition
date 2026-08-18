"""
===== 第 0 步：JSON 接口契约 =====
这是整个项目的核心——所有 Agent 的输入/输出格式在此定义。
A / C 在并行开发前必须先看此文件。

约定：
- 所有 Agent 输出必须是 Pydantic model，Coordinator 以此做 validate。
- 错误格式统一为 AgentError。
- 字段一旦发布即视为「接口契约」，向后兼容，新增字段需带默认值。
"""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ──────────── 枚举 / 常量 ────────────

class TaskStatus(str, Enum):
    """Task execution status for progress tracking"""
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    blocked = "blocked"


# ──────────── 输入 ────────────

class CourseInfo(BaseModel):
    """课程基本信息"""
    name: str = Field(description="课程名称")
    description: str = Field(description="课程描述/要求")


class TeamMember(BaseModel):
    """团队成员信息"""
    name: str
    role: str = Field(
        default="执行成员",
        description="角色：项目负责人 / 骨干 / 执行成员 / 志愿者 / 自定义角色。"
        "工作量统计按角色折算，小型项目同样适用。",
    )
    skill_tags: list[str] = Field(default_factory=list,
                                  description="技能标签 e.g. ['前端','Python','PPT']")
    available_hours: float = Field(
        default=20.0,
        description="总可用工时（人时）= 每日工时 × 可用天数。负载均衡与超载预警使用。",
    )
    daily_available_hours: float = Field(
        default=4.0,
        description="每人每天可用工时（小时），用于时间线折算。现实中不同成员可用时间不同。",
    )
    available_stages: list[str] = Field(default_factory=list)
    unavailable_dates: list[date] = Field(
        default_factory=list,
        description="成员不可用的具体日期（如考试、请假、其他安排）。排期时跳过这些日期。",
    )
    profile_mode: str = Field(
        default="tags",
        description="能力输入模式：'tags'（技能标签）或 'bio'（自然语言简介）。",
    )
    manager: str = Field(
        default="",
        description="上级成员姓名；空表示顶层节点。用于组织树与工作量汇总。",
    )
    bio: str = Field(
        default="",
        description="成员能力简介（profile_mode='bio' 时使用）。自然语言描述成员擅长什么、经验、软技能等。",
    )
    @field_validator('available_hours', 'daily_available_hours')
    @classmethod
    def _clamp_hours(cls, v: float) -> float:
        """钳制工时到合法下限，避免 0/负值导致除零或负工时。"""
        return max(0.5, float(v))


class AssignmentInput(BaseModel):
    """Coordinator 的输入（即整个系统的入口）"""
    course: CourseInfo
    members: list[TeamMember]
    deadline: date = Field(description="作业截止日期")
    additional_requirements: str = ""
    background: str = ""
    requirements: str = ""
    default_start_date: Optional[date] = None
    default_end_date: Optional[date] = None
    uploaded_files: list[dict] = Field(default_factory=list)
    requirement_analysis: dict = Field(default_factory=dict)
    project_mode: str = Field(
        default="small_group",
        description="项目模式：small_group（小组作业，人固定，先看人再拆任务）"
        "或 large_project（大型项目，先拆任务再认领招募）。",
    )

    @field_validator("members")
    @classmethod
    def _filter_members(cls, v):
        """剔除无姓名成员并检查重名；大型项目允许先不填骨干。"""
        named = [m for m in v if m.name.strip()]
        names = [m.name for m in named]
        dups = set(n for n in names if names.count(n) > 1)
        if dups:
            raise ValueError(f"duplicate member names: {chr(44).join(dups)}")
        return named

    @model_validator(mode="after")
    def _require_members_for_small_group(self):
        """小型项目人固定，必须至少 1 名成员；大型项目先拆任务后可再补骨干。"""
        if self.project_mode != "large_project" and not self.members:
            raise ValueError("至少需要 1 名有姓名的团队成员")
        if self.project_mode != "large_project":
            self.members = [
                member.model_copy(update={"manager": ""})
                for member in self.members
            ]
        return self


# ──────────── Planner 输出 ────────────

class ProjectModule(BaseModel):
    """大型项目模式：可认领的大任务/模块（先拆大任务，再拆子任务）。"""
    id: str = Field(description="唯一标识，如 M1, M2")
    name: str
    description: str = Field(default="", description="模块目标与交付边界")
    order: int = Field(default=0, description="模块显示顺序")
    status: TaskStatus = Field(default=TaskStatus.pending,
                               description="模块执行状态")
    assignee_id: Optional[str] = Field(
        default=None, description="认领该模块的骨干成员姓名")


class TaskParticipant(BaseModel):
    """任务级参与清单：谁、以什么角色、投入多少工时参与该任务。"""
    name: str = Field(description="参与者姓名（成员或外部协作者）")
    role: str = Field(default="执行成员", description="参与角色，如负责人/执行/骨干/志愿者")
    contribution_hours: float = Field(default=0.0, ge=0, description="该参与者在本任务投入的人时")
    is_volunteer: bool = Field(default=False, description="是否为外部志愿者/协作者")
    status: str = Field(default="已确认", description="志愿者状态；内部成员固定为已确认")


class SubTask(BaseModel):
    """Planner 输出的一个子任务"""
    id: str = Field(description="唯一标识，如 T1, T2")
    module_id: Optional[str] = Field(
        default=None, description="大型项目模式：所属模块（大任务）ID")
    name: str
    description: str = ""
    estimated_hours: float = Field(default=2.0, description="预估工时（人时）")
    estimate_min_hours: Optional[float] = Field(
        default=None, description="知识库建议工时下限")
    estimate_max_hours: Optional[float] = Field(
        default=None, description="知识库建议工时上限")
    estimate_reason: str = Field(default="", description="工时估算依据")
    estimate_confidence: str = Field(default="", description="工时估算可信度")
    required_duration_hours: Optional[float] = Field(
        default=None, description="任务要求明确规定的活动持续时长，不等同于制作人时")
    dependencies: list[str] = Field(default_factory=list,
                                    description="依赖的其他任务 ID 列表")
    required_skills: list[str] = Field(default_factory=list)
    category: str = "其他"
    execution_stage: str = "执行"
    custom_stage: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assignee_id: Optional[str] = None
    collaborator_ids: list[str] = Field(default_factory=list)
    suggested_people: int = Field(default=1, ge=1, le=10,
                                  description="建议参与人数，至少 1 人")
    extra_helpers_needed: int = Field(
        default=0, ge=0, le=20,
        description="大型项目模式：该任务需额外招募的志愿者/参与者人数（0=骨干可覆盖）。",
    )
    participants: list[TaskParticipant] = Field(
        default_factory=list,
        description="任务级参与清单；非空时工作量统计优先使用它。",
    )
    actual_hours: Optional[float] = Field(
        default=None, ge=0,
        description="实际完成工时（复盘用，任务完成后填写）。",
    )
    actual_end_date: Optional[date] = Field(
        default=None, description="实际完成日期（复盘用）。",
    )
    actual_feedback_recorded: bool = Field(
        default=False,
        description="实际工时是否已沉淀回工时知识库，避免重复记录。",
    )
    order: int = 0
    status: TaskStatus = Field(default=TaskStatus.pending,
                               description="Task execution status")

    @field_validator("estimated_hours")
    @classmethod
    def _positive_hours(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("预计工时必须大于 0")
        return round(float(value), 2)

    @model_validator(mode="after")
    def _valid_dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("截止日期不能早于开始日期")
        if self.execution_stage == "自定义" and not (self.custom_stage or "").strip():
            raise ValueError("自定义执行阶段必须填写名称")
        return self


class PlanOutput(BaseModel):
    """Planner Agent 的完整输出"""
    tasks: list[SubTask]
    modules: list[ProjectModule] = Field(
        default_factory=list,
        description="大型项目模式：模块（大任务）列表；子任务通过 module_id 归属。",
    )
    summary: str = Field(description="总体任务拆解说明")
    reasoning: str = Field(default="",
                           description="可解释性：为什么这样拆")
    member_assessment: dict[str, str] = Field(
        default_factory=dict,
        description="先看人：每个成员的能力评估 {姓名: 评估}，驱动任务按个人能力生成。",
    )


# ──────────── Matcher 输出 ────────────

class QAAssignment(BaseModel):
    """单个任务的 QA 责任分配"""
    task_id: str
    task_name: str
    chapter: str = Field(default="", description="所属章节/段落")
    presenter: str = Field(description="负责人姓名")
    qa_primary: str = Field(description="主要协助人姓名")
    qa_support: list[str] = Field(default_factory=list,
                                  description="辅助协助人列表")
    score: float = Field(default=0.0,
                         description="B3：该分配的技能匹配得分（0-1）")
    reasoning: str = Field(default="",
                           description="可解释性：为什么这样分配")


class QAOutput(BaseModel):
    """Matcher Agent 的完整输出"""
    assignments: list[QAAssignment]
    workload: dict[str, float] = Field(
        default_factory=dict,
        description="B3：成员负载摘要 {姓名: 折算工时}",
    )
    note: str = ""


# ──────────── Timeline 输出 ────────────

class TimelineTask(BaseModel):
    """带时间线信息的任务"""
    task_id: str
    name: str
    start_date: datetime
    end_date: datetime
    is_critical: bool = Field(description="是否在关键路径上")
    float_days: int = Field(default=0,
                            description="浮动天数（0 即关键路径任务）")
    assigned_to: list[str] = Field(default_factory=list)
    status: TaskStatus = Field(default=TaskStatus.pending,
                               description="Current execution status")


class TimelineOutput(BaseModel):
    """Timeline Agent 的完整输出"""
    tasks: list[TimelineTask]
    critical_path: list[str] = Field(description="关键路径上的任务 ID 列表")
    total_days: int
    note: str = ""
    reasoning: str = Field(default="",
                           description="可解释性：关键路径如何得出")


# ──────────── Report 输出 ────────────

class ReportOutput(BaseModel):
    """最终报告文本"""
    summary: str
    timeline_section: str = ""
    qa_matrix_section: str = ""
    risk_note: str = ""


# ──────────── Reflection 输出 ────────────

class ReflectionIssue(BaseModel):
    """Reflection Agent 发现的单条问题"""
    level: str = Field(description="严重程度：error / warning / suggestion")
    dimension: str = Field(description="所属维度：任务拆解 / 工时估算 / 负载均衡 / 时间线 / 分工合理性 / 风险")
    description: str = Field(description="问题描述")
    suggestion: str = Field(default="", description="改进建议")
    affected_tasks: list[str] = Field(default_factory=list, description="受影响的任务 ID")


class ReflectionOutput(BaseModel):
    """Reflection Agent 的完整输出"""
    issues: list[ReflectionIssue] = Field(default_factory=list, description="发现的问题列表")
    overall_score: float = Field(default=0.0, ge=0.0, le=10.0,
                                  description="计划整体质量评分（0-10）")
    overall_comment: str = Field(default="", description="整体评价（100-200字）")
    improvement_priority: list[str] = Field(
        default_factory=list,
        description="按优先级排列的改进方向（最多5条）",
    )
    passed: bool = Field(default=True, description="计划是否通过质量检查（无 error 级别问题）")


# ──────────── Coordinator 最终输出 ────────────

class Volunteer(BaseModel):
    """大型项目模式下的外部志愿者/参与者认领记录。"""
    name: str = Field(description="志愿者姓名/昵称")
    task_id: str = Field(description="认领的任务 ID")
    status: str = Field(
        default="待确认",
        description="认领状态：待确认 / 已确认 / 已婉拒",
    )
    contact: str = Field(default="", description="联系方式（可选）")
    note: str = Field(default="", description="备注（可选）")


class FullPlan(BaseModel):
    """整个系统的最终输出"""
    input: AssignmentInput
    plan: PlanOutput
    timeline: TimelineOutput
    qa_matrix: QAOutput
    report: ReportOutput
    reflection: Optional[ReflectionOutput] = Field(default=None, description="Reflection Agent 的自我审查结果")
    volunteer_pool: list[Volunteer] = Field(
        default_factory=list,
        description="大型项目模式：志愿者/参与者招募池，按任务认领。",
    )
    performance: dict = Field(
        default_factory=dict,
        description="请求级性能摘要；仅含耗时、数量和执行路径，不含 Prompt 或密钥。",
    )
    version: str = "5.76"


class DraftRequest(BaseModel):
    input: AssignmentInput
    use_ai: bool = Field(default=True, description="是否调用 LLM；false 时使用快速领域化草案")


class DraftResponse(BaseModel):
    input: AssignmentInput
    plan: PlanOutput
    warnings: list[str] = Field(default_factory=list)


class ConfirmDraftRequest(BaseModel):
    input: AssignmentInput
    plan: PlanOutput


class DraftOperation(BaseModel):
    """界面与未来自然语言 Agent 共用的任务修改指令。"""
    op: str = Field(description="任务与模块的增删改排序指令")
    task_id: str = ""
    task_ids: list[str] = Field(default_factory=list)
    task: Optional[SubTask] = None
    tasks: list[SubTask] = Field(default_factory=list)
    ordered_ids: list[str] = Field(default_factory=list)
    module: Optional[ProjectModule] = None
    module_id: str = Field(default="", description="模块操作目标 ID")
    module_ids: list[str] = Field(default_factory=list, description="合并模块时传入的多个模块 ID")
    modules: list[ProjectModule] = Field(default_factory=list)
    ordered_module_ids: list[str] = Field(default_factory=list)


class DraftMutationRequest(BaseModel):
    plan: PlanOutput
    operations: list[DraftOperation]


class ManualAssignmentRequest(BaseModel):
    plan: FullPlan
    assignees: dict[str, str] = Field(default_factory=dict)
    collaborators: dict[str, list[str]] = Field(default_factory=dict)
    module_assignees: dict[str, str] = Field(
        default_factory=dict,
        description="大型项目：模块认领 {模块ID: 骨干姓名}",
    )


class RequirementAnalysis(BaseModel):
    project_goal: str = ""
    core_tasks: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    time_requirements: list[str] = Field(default_factory=list)
    format_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    evaluation_criteria: list[str] = Field(default_factory=list)
    important_people: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    summary: str = ""


# ──────────── B4：协作图动态编辑 ────────────

class TaskEdit(BaseModel):
    """单个任务的编辑操作（B4）。

    op 取值：
    - "add"    : 新增任务（需提供 task，id 不能与现有冲突）
    - "remove" : 删除任务（task_id 必须存在）
    - "update" : 修改任务（task_id 必须存在，task 为新内容）
    """
    op: str = Field(description="操作类型：add / remove / update")
    task_id: str = Field(default="", description="目标任务 ID（remove/update 用）")
    task: Optional[SubTask] = Field(default=None, description="add/update 的新任务内容")


class EditPlanRequest(BaseModel):
    """B4 编辑计划的请求：基于已有计划应用一系列编辑后重算。"""
    plan: FullPlan
    edits: list[TaskEdit]
    recompute_timeline: bool = Field(default=True, description="是否重算时间线")
    recompute_matcher: bool = Field(default=True, description="是否重算 QA 矩阵")


# ──────────── 通用 ────────────

class AgentError(BaseModel):
    """Agent 错误输出格式"""
    agent: str
    error_type: str  # "parse_error" | "llm_timeout" | "validation_error" | "unknown"
    message: str
    recoverable: bool = False
