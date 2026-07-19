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

    @field_validator("members")
    @classmethod
    def _at_least_one_member(cls, v):
        """至少 1 名有姓名的成员，CLI 与 Web 共用此校验。"""
        named = [m for m in v if m.name.strip()]
        if not named:
            raise ValueError("至少需要 1 名有姓名的团队成员")
        # check for duplicate member names
        names = [m.name for m in named]
        dups = set(n for n in names if names.count(n) > 1)
        if dups:
            raise ValueError(f"duplicate member names: {chr(44).join(dups)}")
        return named


# ──────────── Planner 输出 ────────────

class SubTask(BaseModel):
    """Planner 输出的一个子任务"""
    id: str = Field(description="唯一标识，如 T1, T2")
    name: str
    description: str = ""
    estimated_hours: float = Field(default=0.0, description="预估工时（人时）")
    dependencies: list[str] = Field(default_factory=list,
                                    description="依赖的其他任务 ID 列表")
    required_skills: list[str] = Field(default_factory=list)
    category: str = "其他"
    execution_stage: str = "实践中"
    custom_stage: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    assignee_id: Optional[str] = None
    collaborator_ids: list[str] = Field(default_factory=list)
    suggested_people: int = Field(default=1, ge=1, le=10,
                                  description="建议参与人数，至少 1 人")
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
    summary: str = Field(description="总体任务拆解说明")
    reasoning: str = Field(default="",
                           description="可解释性：为什么这样拆")


# ──────────── Matcher 输出 ────────────

class QAAssignment(BaseModel):
    """单个任务的 QA 责任分配"""
    task_id: str
    task_name: str
    chapter: str = Field(default="", description="所属答辩章节/段落")
    presenter: str = Field(description="主讲人姓名")
    qa_primary: str = Field(description="主答人姓名")
    qa_support: list[str] = Field(default_factory=list,
                                  description="辅答人列表")
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

class FullPlan(BaseModel):
    """整个系统的最终输出"""
    input: AssignmentInput
    plan: PlanOutput
    timeline: TimelineOutput
    qa_matrix: QAOutput
    report: ReportOutput
    reflection: Optional[ReflectionOutput] = Field(default=None, description="Reflection Agent 的自我审查结果")
    version: str = "3.0"


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
    op: str = Field(description="add/update/remove/split/merge/reorder")
    task_id: str = ""
    task_ids: list[str] = Field(default_factory=list)
    task: Optional[SubTask] = None
    tasks: list[SubTask] = Field(default_factory=list)
    ordered_ids: list[str] = Field(default_factory=list)


class DraftMutationRequest(BaseModel):
    plan: PlanOutput
    operations: list[DraftOperation]


class ManualAssignmentRequest(BaseModel):
    plan: FullPlan
    assignees: dict[str, str] = Field(default_factory=dict)
    collaborators: dict[str, list[str]] = Field(default_factory=dict)


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
