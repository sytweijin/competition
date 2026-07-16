"""
Planner Agent
负责：输入课程/要求/组员 → 输出弹性子任务（1-8 个，含工时、依赖）
负责人：队友 A（端到端）；B 负责兜底校验
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.validation import PlanValidationError, validate_plan
from app.llm.prompts import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE
from app.models.schemas import AgentError, PlanOutput


class PlannerAgent(BaseAgent[PlanOutput]):
    system_prompt = PLANNER_SYSTEM
    response_model = PlanOutput

    def run(self, course_name: str, course_description: str,
            members: list[str], deadline: str,
            extra: str = "") -> PlanOutput | AgentError:
        """执行规划。返回 PlanOutput，或 AgentError（LLM/校验失败）。"""
        user = PLANNER_USER_TEMPLATE.format(
            course_name=course_name,
            course_description=course_description,
            members=", ".join(members),
            deadline=deadline,
            extra=extra or "无",
        )
        result = self._call_llm(user)
        if isinstance(result, AgentError):
            return result
        # 新生成的任务一律从「待开始」起步：LLM 偶发把 status 写成 completed/in_progress，
        # 会导致任务一出生就被标成已完成，这里强制归零。
        result = result.model_copy(update={
            "tasks": [t.model_copy(update={"status": "pending"}) for t in result.tasks]
        })
        # 兜底校验：去重 id、剔除悬空依赖、检测环
        try:
            return validate_plan(result)
        except PlanValidationError as e:
            return AgentError(
                agent="Planner",
                error_type="validation_error",
                message=f"Planner 输出校验失败：{e}",
                recoverable=False,
            )