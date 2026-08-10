"""清小搭广场 OpenAI 兼容协议适配层。

只负责协议、鉴权和自然语言入口；任务拆解、分工与排期继续复用
Project Service，避免网页与清小搭出现两套业务规则。
"""

from __future__ import annotations

import hmac
import json
import os
import re
import time
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool

from app.models.schemas import AssignmentInput, CourseInfo, TeamMember
from app.models.schemas import AgentError
from app.llm.client import LLMClient
from app.services.project_service import confirm_draft, generate_draft


router = APIRouter(prefix="/v1", tags=["清小搭兼容协议"])
MODEL_ID = "collaboration-planner"
_AUTH_ENV = "QINGXIAODA_API_KEY"
_GENERAL_SYSTEM_PROMPT = (
    "你是协作分工智能体，同时也是友好、准确的通用中文助手。"
    "直接回答用户当前问题，不要把普通问题强行拆成项目任务。"
    "只有用户明确要求项目拆解、人员分工或排期时，才建议使用规划能力。"
    "回答简洁清楚；不确定时坦诚说明。"
)
_CHAT_TIMEOUT = 18


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    stream: StrictBool = False
    max_tokens: int | None = Field(default=None, ge=1)
    model: str | None = None


def _check_auth(authorization: str | None) -> None:
    expected = os.getenv(_AUTH_ENV, "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"服务端尚未配置 {_AUTH_ENV}",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing credential")
    supplied = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid credential")


def _latest_user_message(messages: list[ChatMessage]) -> str:
    return next(
        (message.content.strip() for message in reversed(messages)
         if message.role == "user" and message.content.strip()),
        "",
    )


def _conversation_user_text(messages: list[ChatMessage]) -> str:
    """合并多轮用户输入，让“继续生成甘特图”等追问继承项目上下文。"""
    return "\n".join(
        message.content.strip() for message in messages
        if message.role == "user" and message.content.strip()
    )[-8000:]


def _parse_deadline(text: str) -> date:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if not match:
        relative_days = re.search(r"(\d{1,3})\s*(?:天|日)(?:内|后)?", text)
        if relative_days:
            # “5 天完成”按包含今天在内的 5 个自然日计算。
            return date.today() + timedelta(
                days=max(0, int(relative_days.group(1)) - 1))
        relative_weeks = re.search(r"(\d{1,2})\s*(?:周|星期)(?:内|后)?", text)
        if relative_weeks:
            return date.today() + timedelta(
                days=max(0, int(relative_weeks.group(1)) * 7 - 1))
        return date.today() + timedelta(days=13)
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return date.today() + timedelta(days=14)


def _parse_members(text: str) -> list[TeamMember]:
    match = re.search(
        r"(?:团队成员|成员|团队)\s*[:：]\s*(.+?)"
        r"(?:\n|截止|项目要求|补充要求|"
        r"(?:；|;)\s*(?=\d{1,3}\s*(?:天|日|周|星期))|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        member_count = re.search(
            r"(?:我们|团队)?\s*(\d{1,2})\s*(?:个?人|名成员)", text)
        count = min(10, max(1, int(member_count.group(1)))) \
            if member_count else 1
        skill_sets = (
            ["项目统筹", "沟通协调"],
            ["内容策划", "文案撰写", "PPT制作"],
            ["视觉设计", "数据整理"],
            ["质量检查", "演示汇报"],
        )
        return [
            TeamMember(
                name=f"成员{index}",
                skill_tags=skill_sets[(index - 1) % len(skill_sets)],
                available_hours=56,
                daily_available_hours=4,
            )
            for index in range(1, count + 1)
        ]

    member_text = match.group(1).strip().rstrip("。")
    # 只在括号外切分成员，保留“小林(文案,统筹)”内部的技能逗号。
    raw_members: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in member_text:
        if char in "(（":
            depth += 1
        elif char in ")）" and depth:
            depth -= 1
        if depth == 0 and char in "、,，;；":
            item = "".join(buffer).strip()
            if item:
                raw_members.append(item)
            buffer = []
        else:
            buffer.append(char)
    final_item = "".join(buffer).strip()
    if final_item:
        raw_members.append(final_item)

    members: list[TeamMember] = []
    for raw in raw_members:
        item = raw.strip()
        if not item:
            continue
        detail = re.match(r"([^()（）:：]+)[(（:：]?([^()（）]*)[)）]?", item)
        if not detail:
            continue
        name = detail.group(1).strip()
        skills = [
            skill.strip() for skill in re.split(r"[/+、，,\s]+", detail.group(2))
            if skill.strip()
        ]
        if name:
            members.append(TeamMember(
                name=name,
                skill_tags=skills,
                available_hours=56,
                daily_available_hours=4,
            ))
    return members or [TeamMember(
        name="项目负责人",
        skill_tags=["项目策划", "沟通协调"],
        available_hours=56,
        daily_available_hours=4,
    )]


def _parse_project_name(text: str) -> str:
    match = re.search(r"(?:项目名称|项目)\s*[:：]\s*([^\n；;]+)", text)
    if match:
        return match.group(1).strip()[:80]
    if re.search(r"PPT|幻灯片|演示文稿", text, flags=re.IGNORECASE):
        return "PPT 制作项目"
    if "甘特图" in text:
        return "协作排期项目"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return (first_line[:50] or "协作项目").strip("。；;：:")


def _build_input(text: str) -> AssignmentInput:
    deadline = _parse_deadline(text)
    start = date.today()
    project_days = max(1, (deadline - start).days + 1)
    members = [
        member.model_copy(update={
            "available_hours": member.daily_available_hours * project_days,
        })
        for member in _parse_members(text)
    ]
    return AssignmentInput(
        course=CourseInfo(name=_parse_project_name(text), description=text[:2000]),
        members=members,
        deadline=deadline,
        background=text[:4000],
        requirements=text[:4000],
        additional_requirements=text[:2000],
        default_start_date=start,
        default_end_date=deadline,
    )


def _is_planning_request(latest_text: str, conversation_text: str) -> bool:
    """只把明确的执行型规划请求送进分工链路，避免普通问答被误拆解。"""
    latest = latest_text.lower()
    conceptual_prefixes = ("什么是", "是什么意思", "为什么", "如何理解", "怎么理解",
                           "介绍一下", "解释一下", "有什么区别", "优缺点")
    execution_markers = ("请生成", "请制定", "请安排", "请拆解", "请分工", "请排期",
                         "帮我", "帮我们", "给我生成", "为我安排", "为我们安排")
    if any(marker in latest for marker in conceptual_prefixes) \
            and not any(marker in latest for marker in execution_markers):
        return False
    if latest.startswith(("如何", "怎么")) \
            and not any(marker in latest for marker in execution_markers):
        return False

    planning_actions = (
        "任务拆解", "拆解任务", "智能分工", "项目分工", "安排任务", "分配任务",
        "怎么分工", "如何分工", "生成甘特图", "制作甘特图", "重新排期",
        "项目排期", "制定项目计划", "生成项目计划", "安排这个项目",
    )
    if any(action in latest for action in planning_actions):
        return True

    has_people = bool(re.search(r"\d{1,2}\s*(?:个?人|名成员)", conversation_text)) \
        or bool(re.search(r"(?:成员|团队)\s*[:：]", conversation_text))
    has_time = bool(re.search(
        r"(?:20\d{2}[-/.年]\d{1,2}|\d{1,3}\s*(?:天|日|周|星期)|截止)",
        conversation_text,
    ))
    has_deliverable = _has_actionable_scope(conversation_text)
    if has_people and has_time and has_deliverable:
        return True

    continuation = ("继续", "调整", "修改", "延长", "缩短", "重新", "增加", "删除")
    prior_planning = any(action in conversation_text.lower()
                         for action in planning_actions)
    return prior_planning and any(word in latest for word in continuation)


def _has_actionable_scope(text: str) -> bool:
    """区分“能画甘特图吗”与包含交付物/团队/工期的可执行需求。"""
    scope_signals = (
        "PPT", "幻灯片", "演示文稿", "报告", "活动", "开发", "调研",
        "宣传", "答辩", "视频", "论文", "作业", "比赛", "项目：",
    )
    return any(signal.lower() in text.lower() for signal in scope_signals)


def _understand_with_llm(
    messages: list[ChatMessage], fallback_text: str,
) -> str:
    """让千问把多轮口语需求归一化；失败或超时则立即使用本地解析。"""
    enabled = os.getenv("QINGXIAODA_USE_AI", "true").lower() \
        in ("1", "true", "yes")
    if not enabled:
        return fallback_text
    dialogue = [
        {"role": message.role, "content": message.content}
        for message in messages[-8:]
        if message.role in ("user", "assistant")
    ]
    result = LLMClient.get_shared().chat_messages(
        system_prompt=(
            "你是项目需求理解器。请把多轮对话整理为一段简洁的中文项目需求，"
            "只输出需求摘要，不回答用户。必须保留项目目标、人数或成员姓名与技能、"
            "相对或绝对期限、交付物以及用户最新修改；不确定的信息不要编造。"
        ),
        messages=dialogue,
        temperature=0.1,
        timeout=_CHAT_TIMEOUT,
    )
    if isinstance(result, AgentError) or not result.strip():
        return fallback_text
    # 原始文本附在后面，保证规则解析仍能读取模型可能遗漏的数字和相对期限。
    return f"{result.strip()}\n原始用户需求：\n{fallback_text}"[-8000:]


def _needs_ai_normalization(messages: list[ChatMessage], text: str) -> bool:
    """简单结构化需求走本地快路径；复杂描述和多轮修改交给千问理解。"""
    user_turns = sum(message.role == "user" for message in messages)
    change_words = ("调整", "修改", "改成", "改为", "增加", "删除", "延长", "缩短")
    return len(text) > 260 or (
        user_turns > 1 and any(word in text for word in change_words)
    )


def _quick_general_answer(text: str) -> str | None:
    normalized = text.strip().lower().rstrip("！!。？?")
    if normalized in ("你好", "您好", "hello", "hi", "在吗"):
        return "你好！你可以问我一般问题，也可以让我帮你做项目拆解、分工和排期。"
    if normalized in ("谢谢", "感谢", "好的", "明白了"):
        return "不客气！还有什么想了解或需要安排的吗？"
    return None


def _answer_general(
    messages: list[ChatMessage], latest_text: str, max_tokens: int | None,
) -> str:
    quick = _quick_general_answer(latest_text)
    if quick:
        return quick
    dialogue = [
        {"role": message.role, "content": message.content}
        for message in messages[-8:]
        if message.role in ("user", "assistant")
    ]
    result = LLMClient.get_shared().chat_messages(
        system_prompt=_GENERAL_SYSTEM_PROMPT,
        messages=dialogue,
        temperature=0.4,
        timeout=_CHAT_TIMEOUT,
    )
    if isinstance(result, AgentError) or not result.strip():
        return "这个问题暂时没有回答成功，请稍后再试；项目拆解和分工功能仍可正常使用。"
    answer = result.strip()
    return answer[:max_tokens * 4] if max_tokens else answer


def _render_text_gantt(full_plan) -> list[str]:
    tasks = list(full_plan.timeline.tasks)
    if not tasks:
        return ["### 甘特图", "暂时没有可用的排期数据。"]
    start = min(item.start_date.date() for item in tasks)
    end = max(item.end_date.date() for item in tasks)
    span = max(1, (end - start).days + 1)
    width = min(14, max(5, span))
    lines = [
        "### 甘特图（文本版）",
        "",
        "| 任务 | 负责人 | 日期 | 时间轴 |",
        "|---|---|---|---|",
    ]
    assignments = {
        item.task_id: item for item in full_plan.qa_matrix.assignments
    }
    for item in tasks:
        left = round((item.start_date.date() - start).days / span * width)
        duration = max(1, (item.end_date.date() - item.start_date.date()).days + 1)
        blocks = max(1, round(duration / span * width))
        bar = "·" * left + "█" * blocks
        bar = (bar + "·" * width)[:width]
        owner = assignments.get(item.task_id)
        lines.append(
            f"| {item.name} | {owner.presenter if owner else '待确认'} | "
            f"{item.start_date.date().isoformat()} → {item.end_date.date().isoformat()} | "
            f"`{bar}` |"
        )
    return lines


def _render_plan(
    latest_text: str,
    conversation_text: str,
    max_tokens: int | None,
    messages: list[ChatMessage],
) -> str:
    # 清小搭连通探测会发送 max_tokens:1；必须走毫秒级路径，不能触发模型。
    if max_tokens == 1:
        return "好"
    if not _is_planning_request(latest_text, conversation_text):
        return _answer_general(messages, latest_text, max_tokens)

    if not _has_actionable_scope(conversation_text):
        return (
            "可以生成甘特图并帮你规划。请再告诉我项目交付物或目标、人数和期限，例如："
            "‘3 个人 5 天完成一个答辩 PPT，并生成甘特图’。"
        )

    understood_text = (
        _understand_with_llm(messages, conversation_text)
        if _needs_ai_normalization(messages, conversation_text)
        else conversation_text
    )
    inp = _build_input(understood_text)
    draft = generate_draft(inp, use_ai=False)
    full_plan = confirm_draft(inp, draft, use_ai_reflection=False)
    assignments = {
        item.task_id: item for item in full_plan.qa_matrix.assignments
    }
    timeline = {
        item.task_id: item for item in full_plan.timeline.tasks
    }
    lines = [
        f"## {inp.course.name}",
        "",
        f"截止日期：{inp.deadline.isoformat()}",
        f"团队成员：{'、'.join(member.name for member in inp.members)}",
        "",
        "### 任务拆解与智能分工",
    ]
    for index, task in enumerate(full_plan.plan.tasks, 1):
        assignment = assignments.get(task.id)
        schedule = timeline.get(task.id)
        owner = assignment.presenter if assignment else (task.assignee_id or "待确认")
        dates = (
            f"{str(schedule.start_date)[:10]} → {str(schedule.end_date)[:10]}"
            if schedule else "待排期"
        )
        lines.append(
            f"{index}. **{task.name}**（{task.estimated_hours:g}h）"
            f" — 负责人：{owner}；排期：{dates}"
        )
    lines.extend(["", *_render_text_gantt(full_plan)])
    lines.extend([
        "",
        "### 说明",
        "以上方案已综合技能匹配、成员负载、任务依赖和截止日期生成。"
        "你可以继续告诉我需要增删的任务、人员变化或工时限制。",
        "可视化拖拽调整与导出请打开项目工作台："
        "https://collaboration-planner-demo.onrender.com/",
    ])
    answer = "\n".join(lines)
    if max_tokens:
        # 协议字段表示 token 上限；无 tokenizer 时只做保守字符截断。
        answer = answer[:max_tokens * 4]
    return answer


def _usage(messages: list[ChatMessage], answer: str) -> dict[str, int]:
    prompt_chars = sum(len(message.content) for message in messages)
    prompt_tokens = max(1, (prompt_chars + 3) // 4)
    completion_tokens = max(1, (len(answer) + 3) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _chunk_text(text: str, size: int = 18):
    for index in range(0, len(text), size):
        yield text[index:index + size]


@router.get("/models")
def models(authorization: str | None = Header(default=None)):
    _check_auth(authorization)
    return {
        "object": "list",
        "data": [{
            "id": MODEL_ID,
            "object": "model",
            "owned_by": "collaboration-agent",
        }],
    }


@router.post("/chat/completions")
def chat_completions(
    request: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
):
    _check_auth(authorization)
    user_text = _latest_user_message(request.messages)
    if not user_text:
        raise HTTPException(status_code=400, detail="messages 中缺少 user 内容")

    conversation_text = _conversation_user_text(request.messages)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if not request.stream:
        answer = _render_plan(
            user_text, conversation_text, request.max_tokens,
            request.messages)
        usage = _usage(request.messages, answer)
        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": MODEL_ID,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }],
            "usage": usage,
        })

    def event_stream():
        def frame(delta: dict, finish_reason=None, include_usage=False):
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": MODEL_ID,
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }],
            }
            if include_usage:
                payload["usage"] = usage
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # 清小搭要求第一帧恰好为 role，不能在前面插 SSE 注释或空 content。
        # 移动端会用该帧建立“用户问题 → 助手回答”的消息关联。
        yield frame({"role": "assistant"})
        if request.max_tokens != 1 \
                and not _is_planning_request(user_text, conversation_text):
            quick = _quick_general_answer(user_text)
            if quick:
                answer = quick
                yield frame({"content": answer})
            else:
                yield frame({"content": "正在回答…\n\n"})
                dialogue = [
                    {"role": message.role, "content": message.content}
                    for message in request.messages[-8:]
                    if message.role in ("user", "assistant")
                ]
                parts: list[str] = []
                for part in LLMClient.get_shared().stream_messages(
                    system_prompt=_GENERAL_SYSTEM_PROMPT,
                    messages=dialogue,
                    temperature=0.4,
                    timeout=_CHAT_TIMEOUT,
                    max_tokens=request.max_tokens,
                ):
                    if isinstance(part, AgentError):
                        fallback = (
                            "这个问题暂时没有回答成功，请稍后再试；"
                            "项目拆解和分工功能仍可正常使用。")
                        parts.append(fallback)
                        yield frame({"content": fallback})
                        break
                    parts.append(part)
                    yield frame({"content": part})
                answer = "".join(parts)
            usage = _usage(request.messages, answer)
            yield frame({}, finish_reason="stop", include_usage=True)
            yield "data: [DONE]\n\n"
            return

        if request.max_tokens != 1:
            yield frame({"content": "正在生成分工与排期…\n\n"})
        answer = _render_plan(
            user_text, conversation_text, request.max_tokens,
            request.messages)
        usage = _usage(request.messages, answer)
        for chunk in _chunk_text(answer):
            yield frame({"content": chunk})
        yield frame({}, finish_reason="stop", include_usage=True)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
