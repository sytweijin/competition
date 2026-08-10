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
        r"(?:团队成员|成员|团队)\s*[:：]\s*(.+?)(?:\n|截止|项目要求|补充要求|$)",
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


def _looks_like_project_request(text: str) -> bool:
    signals = (
        "项目", "任务", "分工", "成员", "截止", "计划", "活动", "报告",
        "开发", "调研", "策划", "交付", "甘特图", "排期", "进度",
        "PPT", "幻灯片", "演示文稿", "团队", "几个人", "人完成",
    )
    lowered = text.lower()
    return any(signal.lower() in lowered for signal in signals)


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
        timeout=12,
    )
    if isinstance(result, AgentError) or not result.strip():
        return fallback_text
    # 原始文本附在后面，保证规则解析仍能读取模型可能遗漏的数字和相对期限。
    return f"{result.strip()}\n原始用户需求：\n{fallback_text}"[-8000:]


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
    if not _looks_like_project_request(conversation_text):
        return (
            "你好！我可以帮你拆任务、分工和排期。请直接告诉我想完成什么、"
            "有多少人以及多久完成，例如：‘3 个人 5 天完成一个 PPT，并生成甘特图’。"
        )

    if "甘特图" in latest_text and not _has_actionable_scope(conversation_text):
        return (
            "可以生成甘特图。还需要一个项目交付物或目标，例如："
            "‘3 个人 5 天完成一个答辩 PPT’，我就能立即给出任务、负责人和时间轴。"
        )

    understood_text = _understand_with_llm(messages, conversation_text)
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

        # 先发送首帧，再执行规划，避免用户长时间看不到任何响应。
        yield frame({"role": "assistant"})
        yield frame({"reasoning": "正在拆解任务、匹配成员并计算排期…"})
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
