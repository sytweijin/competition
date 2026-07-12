"""
Matcher Agent
负责：QA 责任矩阵生成 + 答辩细分/角色匹配
负责人：队友 C
"""

from app.agents.base import BaseAgent
from app.llm.prompts import MATCHER_SYSTEM, MATCHER_USER_TEMPLATE
from app.models.schemas import PlanOutput, TeamMember, QAOutput


class MatcherAgent(BaseAgent[QAOutput]):
    system_prompt = MATCHER_SYSTEM
    response_model = QAOutput

    def run(self, plan: PlanOutput,
            members: list[TeamMember]) -> QAOutput:
        """根据任务计划和成员信息生成 QA 责任矩阵"""
        members_str = "; ".join(
            f"{m.name}: {', '.join(m.skill_tags)}" for m in members
        )
        tasks_str = "; ".join(
            f"{t.id} {t.name}" for t in plan.tasks
        )
        user = MATCHER_USER_TEMPLATE.format(
            tasks=tasks_str, members=members_str
        )
        result = self._call_llm(user)
        # TODO: 队友 C 在此扩展输出校验 + B3 完整角色匹配逻辑
        return result
