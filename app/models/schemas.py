"""
===== 第 0 步：JSON 接口契约 =====
这是整个项目的核心——所有 Agent 的输入/输出格式在此定义。
A / C 在并行开发前必须先看此文件。

约定：
- 所有 Agent 输出必须是 Pydantic model，Coordinator 以此做 validate。
- 错误格式统一为 AgentError。
- 字段一旦发布即视为「接口契约」，向后兼容，新增字段需带默认值。
"""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("members")
    @classmethod
    def _at_least_one_member(cls, v):
        """至少 1 名有姓名的成员，CLI 与 Web 共用此校验。"""
        named = [m for m in v if m.name.strip()]
        if not named:
            raise ValueError("至少需要 1 名有姓名的团队成员")
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
    status: TaskStatus = Field(default=TaskStatus.pending,
                               description="Task execution status")


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
    start_date: date
    end_date: date
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


# ──────────── Coordinator 最终输出 ────────────

class FullPlan(BaseModel):
    """整个系统的最终输出"""
    input: AssignmentInput
    plan: PlanOutput
    timeline: TimelineOutput
    qa_matrix: QAOutput
    report: ReportOutput
    version: str = "2.0"


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