"""
B1: 团队协作模拟 Agent（轻量）
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
        """模拟协作评审提问，生成 10-15 道问题。

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
            f"以下是团队的项目计划和责任分工：\n\n"
            f"## 任务计划\n{task_lines}\n\n"
            f"## 责任分工\n{qa_lines}\n\n"
        )
        if user_requirements.strip():
            user += f"## 用户特别要求\n{user_requirements.strip()}\n\n"
            user += "请优先围绕用户的特别要求生成问题，同时覆盖其他维度。\n\n"
        user += "请生成10-15道可能的评审提问，并标注优先级。"

        result = self.llm.chat_text(
            system_prompt=self.system_prompt,
            user_prompt=user,
        )
        if isinstance(result, str):
            # Post-process: strip any leaked internal terminology
            # 禁用清单与 prompts.INTERVIEW_SYSTEM 逐条对齐（含裸 主讲/主答/辅答等答辩遗留术语）；码点统一 UTF-8。
            # ASCII 项大小写不敏感替换，避免 Score/Load 漏网。
            import re
            bans_zh = [
                'QA角色', '答辩角色', '责任矩阵',
                '主讲分配', '主讲', '主答', '辅答',
                '匹配度', '系统推荐', '算法分配', 'AI分配',
                'QA矩阵', 'QA分配', 'QA角色',
            ]
            bans_ascii = [
                'B3', 'CPM', 'workload', 'load', 'score',
                'task_id', 'assign_with_balance',
                'Matcher', 'Planner', 'Timeline', 'Reporter', 'Scoring',
            ]
            for term in bans_zh:
                result = result.replace(term, '')
            # 裸 "QA" 等遗留术语，整词替换为"协作"
            result = re.sub(r'\bQA\b', '协作', result)
            for term in bans_ascii:
                result = re.sub(r'\b' + re.escape(term) + r'\b', '', result, flags=re.IGNORECASE)
            # Collapse multiple newlines
            result = re.sub(r'\n{3,}', '\n\n', result)
            return result
        # chat_text 失败时返回错误提示文本，不抛异常
        return result.message
