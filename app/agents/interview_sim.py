"""
B1: 答辩模拟 Agent（轻量）
负责人：B（提交人）
"""

from app.agents.base import BaseAgent
from app.llm.prompts import INTERVIEW_SYSTEM
from app.models.schemas import PlanOutput, QAOutput


class InterviewSimAgent(BaseAgent):
    system_prompt = INTERVIEW_SYSTEM
    response_model = None  # 使用 chat_text，非结构化输出

    def run(self, plan: PlanOutput, qa_matrix: QAOutput) -> str:
        """模拟答辩评委提问，生成 10-15 道问题"""
        user = (
            f"以下是学生的作业计划和QA分配：\n\n"
            f"## 任务计划\n{plan.model_dump_json(indent=2)}\n\n"
            f"## QA矩阵\n{qa_matrix.model_dump_json(indent=2)}\n\n"
            f"请生成10-15道可能的答辩问题，并标注优先级。"
        )
        result = self.llm.chat_text(
            system_prompt=self.system_prompt,
            user_prompt=user,
        )
        if isinstance(result, str):
            return result
        return f"[Error] {result.message}"
