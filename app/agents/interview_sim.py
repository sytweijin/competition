"""
B1: 团队协作模拟 Agent（轻量）
负责人：B（提交人）

v0.3.1: 支持用户自定义模拟要求（评委关注点、重点模块等）
"""

import logging

from app.agents.base import BaseAgent
from app.llm.prompts import INTERVIEW_SYSTEM
from app.models.schemas import PlanOutput, QAOutput

logger = logging.getLogger(__name__)

# MiniCPM-o（8B）对超长材料文本会退化（实测云端对 5 万字 PPT 提取文本
# 反复输出 "ösösös…/MAIL MAIL…" 乱码并拖慢响应）：材料一律限量，
# 保留开头主体 + 结尾结论，中间省略，既保质量又显著提速。
_SOURCE_LIMIT = 12000
_SOURCE_LIMIT_RETRY = 6000


def _bounded_source(source: str, limit: int = _SOURCE_LIMIT) -> str:
    """把长材料截成 首部 75% + 尾部 25%，中间用省略标记连接。"""
    source = (source or "").strip()
    if len(source) <= limit:
        return source
    head_len = int(limit * 0.75)
    tail_len = limit - head_len
    return (
        source[:head_len]
        + "\n……（材料过长，中间内容已省略）……\n"
        + source[-tail_len:]
    )


class InterviewSimAgent(BaseAgent):
    system_prompt = INTERVIEW_SYSTEM
    response_model = None  # 使用 chat_text，非结构化输出

    def run(self, plan: PlanOutput, qa_matrix: QAOutput,
            user_requirements: str = "", project_context: str = "",
            material_text: str = "",
            material_names: list[str] | None = None) -> str:
        """根据答辩要求和答辩材料生成 8-10 道现场问题。

        Args:
            plan/qa_matrix: 仅用于旧调用无材料时提供最低限度项目背景。
            project_context: 项目原始要求中与答辩相关的上下文。
            material_text: 用户粘贴或上传的答辩稿/PPT文字。
            material_names: 答辩材料文件名。
        """
        names = "、".join(material_names or []) or "粘贴的答辩稿"
        if material_text.strip():
            source = _bounded_source(material_text.strip())
        else:
            source = "\n".join(f"- {t.name}: {t.description}" for t in plan.tasks)

        def build_user(src: str) -> str:
            user = (
                "请模拟正式答辩现场。问题必须围绕答辩者实际提交的内容，"
                "不要围绕任务完成状态、项目看板或系统分工提问。\n\n"
                f"## 原始答辩/展示要求\n{project_context.strip() or '未提供额外要求'}\n\n"
                f"## 答辩材料（{names}）\n{src}\n\n"
            )
            if user_requirements.strip():
                user += f"## 评委关注点\n{user_requirements.strip()}\n\n"
            user += (
                "请生成 8-10 道可能的答辩提问并标注优先级。重点检查材料中的"
                "核心主张、证据、数据、方案逻辑、创新点、局限和表达清晰度；"
                "不得根据项目任务是否完成来提问。\n"
                "必须直接基于材料中的具体内容出题，不要询问答辩者"
                "'请概括/复述/再说明一遍材料'这类元问题——材料里已有的结论"
                "直接追问它的依据、逻辑和边界即可。每题一句话，只输出问题，"
                "不要开场白和解释。"
            )
            return user

        result = self.llm.chat_text(
            system_prompt=self.system_prompt,
            user_prompt=build_user(source),
            max_tokens=2048,
        )
        if not isinstance(result, str) or not result.strip():
            # 长材料仍可能让模型退化：缩短到一半再试一次（应用层第二道防线，
            # 与 _realtime_text 的防乱码重试叠加，最多多一次生成）。
            logger.warning(
                "Interview question generation failed, retrying with shorter "
                "material: %s", getattr(result, "message", result))
            result = self.llm.chat_text(
                system_prompt=self.system_prompt,
                user_prompt=build_user(
                    _bounded_source(source, _SOURCE_LIMIT_RETRY)),
                max_tokens=2048,
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
        # chat_text 失败时记录原因并返回空串，由路由层给出
        # 基于材料/评委关注点的确定性兜底问题（此前漏 return 会返回 None，
        # 路由只能给出与材料无关的通用问题）。
        logger.warning("Interview question generation failed: %s",
                       getattr(result, "message", result))
        return ""

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
        source = (
            _bounded_source(material_text.strip(), 8000)
            if material_text.strip()
            else "\n".join(
                f"- {t.name}: {t.description}" for t in plan.tasks)
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
        # 防"重新开始"：评委生成失败时，前端历史里可能残留没有评委回复的
        # user 消息；连续多条 user 会让模型误判为新会话，从第一问重新问。
        # 这里归一化历史（丢弃空内容、合并连续同角色），并让本轮回答也参与
        # 合并，保证发给模型的 Q/A 始终交替。
        normalized_history: list[dict] = []
        for msg in history:
            role = "assistant" if msg.get("role") == "assistant" else "user"
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if normalized_history and normalized_history[-1]["role"] == role:
                normalized_history[-1]["content"] += "\n" + content
            else:
                normalized_history.append({"role": role, "content": content})
        adjust_mode = mode == "adjust"
        if adjust_mode:
            feedback = user_answer.strip() or "这道题不够精确，请调整得更具体、更贴合我们的项目。"
            final_user = (
                "请根据我的反馈重新调整你刚才提出的评审问题。"
                "只输出调整后的一个问题，不要点评，不要解释。\n\n反馈："
                + feedback
            )
        elif user_answer.strip():
            final_user = user_answer
        else:
            final_user = "请提第一个评审问题。"
        turn_messages = normalized_history + [
            {"role": "user", "content": final_user}]
        merged: list[dict] = []
        for msg in turn_messages:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1]["content"] += "\n" + msg["content"]
            else:
                merged.append(dict(msg))
        messages.extend(merged)

        result = self.llm.chat_messages(
            system_prompt=INTERVIEW_ADJUST_SYSTEM if adjust_mode else INTERVIEW_CHAT_SYSTEM,
            messages=messages,
            temperature=0.6,
            max_tokens=2048,
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
