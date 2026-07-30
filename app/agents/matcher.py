"""
Matcher Agent
负责：QA 责任矩阵生成 + 答辩细分/角色匹配
"""

from __future__ import annotations

from app.agents.base import BaseAgent
from app.llm.prompts import MATCHER_SYSTEM, MATCHER_USER_TEMPLATE
from app.models.schemas import AgentError, PlanOutput, TeamMember, QAOutput, QAAssignment
from app.agents.scoring import format_skills_for_prompt


class MatcherAgent(BaseAgent[QAOutput]):
    system_prompt = MATCHER_SYSTEM
    response_model = QAOutput

    def run(self, plan: PlanOutput,
            members: list[TeamMember]) -> QAOutput | AgentError:
        """根据任务计划和成员信息生成 QA 责任矩阵。"""
        members_str = "; ".join(
            f"{m.name}(技能: {format_skills_for_prompt(m.skill_tags)}; "
            f"可用工时: {m.available_hours}h)"
            for m in members
        )
        tasks_str = "; ".join(
            f"{t.id} {t.name}({t.estimated_hours}h, 需: {', '.join(t.required_skills) or '无'})"
            for t in plan.tasks
        )
        user = MATCHER_USER_TEMPLATE.format(tasks=tasks_str, members=members_str)
        result = self._call_llm(user)
        if isinstance(result, AgentError):
            return result
        # 兜底：把 LLM 编造的不存在成员名修正为真实成员
        return self._sanitize(result, plan, members)

    @staticmethod
    def _sanitize(qa: QAOutput, plan: PlanOutput,
                  members: list[TeamMember]) -> QAOutput:
        """剔除/修正引用了不存在成员或不存在任务的分配。"""
        valid_names = {m.name for m in members}
        # 至少要有一个成员可用作兜底
        fallback = members[0].name if members else ""
        task_map = {t.id: t.name for t in plan.tasks}

        cleaned: list[QAAssignment] = []
        for a in qa.assignments:
            # 跳过指向不存在任务的分配
            if a.task_id not in task_map:
                continue
            presenter = a.presenter if a.presenter in valid_names else fallback
            primary = a.qa_primary if a.qa_primary in valid_names else fallback
            support = [s for s in a.qa_support if s in valid_names]
            cleaned.append(a.model_copy(update={
                "presenter": presenter,
                "qa_primary": primary,
                "qa_support": support,
            }))
        if not cleaned:
            return AgentError(
                agent="Matcher",
                error_type="validation_error",
                message="LLM assignments all reference invalid members/tasks",
                recoverable=True,
            )
        return qa.model_copy(update={"assignments": cleaned})