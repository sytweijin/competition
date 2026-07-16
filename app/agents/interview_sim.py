"""
B1: 答辩模拟 Agent（轻量）
负责人：B（提交人）

v0.3.1: 支持用户自定义模拟要求（评委关注点、重点模块等）
"""

from app.agents.base import BaseAgent
from app.llm.prompts import INTERVIEW_SYSTEM
from app.models.schemas import PlanOutput, QAOutput


class InterviewSimAgent(BaseAgent):
    system_prompt = INTERVIEW_SYSTEM
    response_model = None  # 使用 chat_text，非结构化输出

    def run(self, plan: PlanOutput, qa_matrix: QAOutput,
            user_requirements: str = "") -> str:
        """模拟答辩评委提问，生成 10-15 道问题。

        Args:
            plan: 任务计划。
            qa_matrix: QA 责任矩阵。
            user_requirements: 用户自定义要求，如评委关注点、重点模块等。
        """
        task_lines = "\n".join(
            f"- {t.id} {t.name}" for t in plan.tasks)
        qa_lines = "\n".join(
            f"- {a.task_name}: {a.presenter}/{a.qa_primary}"
            for a in qa_matrix.assignments) or "无"
        user = (
            f"以下是学生的作业计划和QA分配：\n\n"
            f"## 任务计划\n{task_lines}\n\n"
            f"## QA矩阵\n{qa_lines}\n\n"
        )
        if user_requirements.strip():
            user += f"## 用户特别要求\n{user_requirements.strip()}\n\n"
            user += "请优先围绕用户的特别要求生成问题，同时覆盖其他维度。\n\n"
        user += "请生成10-15道可能的答辩问题，并标注优先级。"

        result = self.llm.chat_text(
            system_prompt=self.system_prompt,
            user_prompt=user,
        )
        if isinstance(result, str):
            return result
        # chat_text 失败时返回错误提示文本，不抛异常
        return result.message
