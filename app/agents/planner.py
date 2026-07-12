"""
Planner Agent
负责：输入课程/要求/组员 → 输出 5-8 子任务（含工时、依赖）
负责人：队友 A
"""

from app.agents.base import BaseAgent
from app.llm.prompts import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE
from app.models.schemas import PlanOutput


class PlannerAgent(BaseAgent[PlanOutput]):
    system_prompt = PLANNER_SYSTEM
    response_model = PlanOutput

    def run(self, course_name: str, course_description: str,
            members: list[str], deadline: str,
            extra: str = "") -> PlanOutput:
        """执行规划"""
        user = PLANNER_USER_TEMPLATE.format(
            course_name=course_name,
            course_description=course_description,
            members=", ".join(members),
            deadline=deadline,
            extra=extra,
        )
        result = self._call_llm(user)
        # TODO: 队友 A 在此添加输出校验逻辑
        #       如校验 task id 唯一性、依赖不指向不存在的任务等
        return result
