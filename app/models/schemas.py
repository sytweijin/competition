"""
===== 第 0 步：JSON 接口契约 =====
这是整个项目的核心——所有 Agent 的输入/输出格式在此定义。
A / C 在并行开发前必须先看此文件。

约定：
- 所有 Agent 输出必须是 Pydantic model，Coordinator 以此做 validate。
- 错误格式统一为 AgentError。
"""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ──────────── 枚举 / 常量 ────────────

class SkillLevel(str, Enum):
    """技能等级标签，B3 完整版使用"""
    beginner = "初级"
    intermediate = "中级"
    advanced = "高级"
    expert = "专家"


class MemberRole(str, Enum):
    """成员在作业中的角色"""
    presenter = "主讲"
    qa_primary = "主答"
    qa_support = "辅答"
    contributor = "参与"


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


class AssignmentInput(BaseModel):
    """Coordinator 的输入（即整个系统的入口）"""
    course: CourseInfo
    members: list[TeamMember]
    deadline: date = Field(description="作业截止日期")
    additional_requirements: str = ""


# ──────────── Planner 输出 ────────────

class SubTask(BaseModel):
    """Planner 输出的一个子任务"""
    id: str = Field(description="唯一标识，如 T1, T2")
    name: str
    description: str
    estimated_hours: float = Field(description="预估工时（人时）")
    dependencies: list[str] = Field(default_factory=list,
                                     description="依赖的其他任务 ID 列表")
    required_skills: list[str] = Field(default_factory=list)


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
    chapter: str = Field(description="所属答辩章节/段落")
    presenter: str = Field(description="主讲人姓名")
    qa_primary: str = Field(description="主答人姓名")
    qa_support: list[str] = Field(default_factory=list,
                                   description="辅答人列表")
    reasoning: str = Field(default="",
                            description="可解释性：为什么这样分配")


class QAOutput(BaseModel):
    """Matcher Agent 的完整输出"""
    assignments: list[QAAssignment]
    note: str = ""


# ──────────── Timeline 输出 ────────────

class TimelineTask(BaseModel):
    """带时间线信息的任务"""
    task_id: str
    name: str
    start_date: date
    end_date: date
    is_critical: bool = Field(description="是否在关键路径上")
    assigned_to: list[str] = Field(default_factory=list)


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
    timeline_section: str
    qa_matrix_section: str
    risk_note: str = ""


# ──────────── Coordinator 最终输出 ────────────

class FullPlan(BaseModel):
    """整个系统的最终输出"""
    input: AssignmentInput
    plan: PlanOutput
    timeline: TimelineOutput
    qa_matrix: QAOutput
    report: ReportOutput
    version: str = "0.1.0"


# ──────────── 通用 ────────────

class AgentError(BaseModel):
    """Agent 错误输出格式"""
    agent: str
    error_type: str  # "parse_error" | "llm_timeout" | "validation_error" | "unknown"
    message: str
    recoverable: bool = False
