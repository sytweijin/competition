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
            user_requirements: str = "", project_context: str = "",
            material_text: str = "",
            material_names: list[str] | None = None) -> str:
        """根据答辩要求和答辩材料生成 10-15 道现场问题。

        Args:
            plan/qa_matrix: 仅用于旧调用无材料时提供最低限度项目背景。
            project_context: 项目原始要求中与答辩相关的上下文。
            material_text: 用户粘贴或上传的答辩稿/PPT文字。
            material_names: 答辩材料文件名。
        """
        names = "、".join(material_names or []) or "粘贴的答辩稿"
        if material_text.strip():
            source = material_text.strip()[:50000]
        else:
            source = "\n".join(f"- {t.name}: {t.description}" for t in plan.tasks)
        user = (
            "请模拟正式答辩现场。问题必须围绕答辩者实际提交的内容，"
            "不要围绕任务完成状态、项目看板或系统分工提问。\n\n"
            f"## 原始答辩/展示要求\n{project_context.strip() or '未提供额外要求'}\n\n"
            f"## 答辩材料（{names}）\n{source}\n\n"
        )
        if user_requirements.strip():
            user += f"## 评委关注点\n{user_requirements.strip()}\n\n"
        user += (
            "请生成10-15道可能的答辩提问并标注优先级。重点检查材料中的"
            "核心主张、证据、数据、方案逻辑、创新点、局限和表达清晰度；"
            "不得根据项目任务是否完成来提问。"
        )

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

    def chat_turn(self, plan: PlanOutput, qa_matrix: QAOutput,
                  user_answer: str, history: list[dict],
                  mode: str = "answer", user_requirements: str = "",
                  project_context: str = "", material_text: str = "",
                  material_names: list[str] | None = None) -> str:
        """多轮互动模式：根据用户回答给出点评并提出下一个问题。

        Args:
            plan/qa_matrix: 仅用于旧调用无材料时提供最低限度项目背景。
            user_answer: 用户对上一轮问题的回答（首轮为空，触发第一个问题）。
            history: 之前的对话历史 [{role, content}]。
            user_requirements: 评委关注点。
            project_context: 项目原始要求中与答辩相关的上下文。
            material_text: 用户提交的答辩稿或 PPT 提取文字。
            material_names: 答辩材料文件名。
        """
        from app.llm.prompts import INTERVIEW_ADJUST_SYSTEM, INTERVIEW_CHAT_SYSTEM
        names = "、".join(material_names or []) or "粘贴的答辩稿"
        source = material_text.strip()[:50000] if material_text.strip() else "\n".join(
            f"- {t.name}: {t.description}" for t in plan.tasks
        )
        context = (
            "这是一次基于答辩稿或PPT内容的模拟答辩，不是项目任务完成情况检查。\n\n"
            f"## 原始答辩/展示要求\n{project_context.strip() or '未提供额外要求'}\n\n"
            f"## 答辩材料（{names}）\n{source}\n\n"
        )
        if user_requirements.strip():
            context += f"## 评委关注点\n{user_requirements.strip()}\n\n"

        messages = [{"role": "user", "content": context + "请开始模拟答辩，针对材料提第一个问题。"}]
        messages.append({"role": "assistant", "content":
            "好的，我已经了解了你们的项目。让我们开始吧。"})
        for msg in history:
            messages.append({"role": msg.get("role", "user"),
                             "content": msg.get("content", "")})
        adjust_mode = mode == "adjust"
        if adjust_mode:
            feedback = user_answer.strip() or "这道题不够精确，请调整得更具体、更贴合我们的项目。"
            messages.append({"role": "user", "content":
                "请根据我的反馈重新调整你刚才提出的评审问题。只输出调整后的一个问题，不要点评，不要解释。\n\n反馈：" + feedback})
        elif user_answer.strip():
            messages.append({"role": "user", "content": user_answer})
        else:
            messages.append({"role": "user",
                             "content": "请提第一个评审问题。"})

        result = self.llm.chat_messages(
            system_prompt=INTERVIEW_ADJUST_SYSTEM if adjust_mode else INTERVIEW_CHAT_SYSTEM,
            messages=messages,
            temperature=0.6,
        )
        if isinstance(result, str):
            import re
            bans_zh = ['QA角色', '答辩角色', '责任矩阵', '主讲分配', '主讲',
                        '主答', '辅答', '匹配度', '系统推荐', '算法分配', 'AI分配',
                        'QA矩阵', 'QA分配']
            bans_ascii = ['B3', 'CPM', 'workload', 'load', 'score',
                           'task_id', 'assign_with_balance']
            for term in bans_zh:
                result = result.replace(term, '')
            result = re.sub(r'\bQA\b', '评审', result)
            for term in bans_ascii:
                result = re.sub(r'\b' + re.escape(term) + r'\b', '',
                                result, flags=re.IGNORECASE)
            result = re.sub(r'\n{3,}', '\n\n', result)
            return result
        return result.message
