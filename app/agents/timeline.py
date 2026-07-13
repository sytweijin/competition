"""
Timeline Agent
负责：倒排时间线 + 关键路径标红
负责人：B
"""

from app.agents.base import BaseAgent
from app.llm.prompts import TIMELINE_SYSTEM, TIMELINE_USER_TEMPLATE
from app.models.schemas import PlanOutput, TimelineOutput


class TimelineAgent(BaseAgent[TimelineOutput]):
    system_prompt = TIMELINE_SYSTEM
    response_model = TimelineOutput

    def run(self, plan: PlanOutput, deadline: str) -> TimelineOutput:
        """生成倒排时间线"""
        tasks_str = "; ".join(
            f"{t.id} {t.name} ({t.estimated_hours}h, "
            f"依赖: {', '.join(t.dependencies) or '无'})"
            for t in plan.tasks
        )
        user = TIMELINE_USER_TEMPLATE.format(
            tasks=tasks_str, deadline=deadline
        )
        result = self._call_llm(user)
        # TODO: B 在此添加关键路径计算逻辑
        return result
