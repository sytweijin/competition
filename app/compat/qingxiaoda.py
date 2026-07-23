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


def _parse_deadline(text: str) -> date:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if not match:
        return date.today() + timedelta(days=14)
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
        return [TeamMember(
            name="项目负责人",
            skill_tags=["项目策划", "沟通协调", "文案撰写"],
            available_hours=56,
            daily_available_hours=4,
        )]

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
        "开发", "调研", "策划", "交付",
    )
    return len(text) >= 12 and any(signal in text for signal in signals)


def _render_plan(text: str, max_tokens: int | None) -> str:
    if max_tokens == 1:
        return "好"
    if not _looks_like_project_request(text):
        return (
            "你好，我是协作分工智能体。请告诉我项目名称、目标、团队成员及技能、"
            "截止日期和交付要求，我会生成任务拆解、智能分工与排期。\n\n"
            "示例：项目：校园低碳倡议；成员：林悦(调研/数据)、"
            "陈曦(文案/策划)、周航(PPT/摄影)；截止：2026-08-20；"
            "要求：完成调研摘要、宣传图文和复盘报告。"
        )

    inp = _build_input(text)
    draft = generate_draft(inp, use_ai=False)
    full_plan = confirm_draft(inp, draft)
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
    lines.extend([
        "",
        "### 说明",
        "以上方案已综合技能匹配、成员负载、任务依赖和截止日期生成。"
        "你可以继续告诉我需要增删的任务、人员变化或工时限制。",
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

    answer = _render_plan(user_text, request.max_tokens)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    usage = _usage(request.messages, answer)

    if not request.stream:
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

        yield frame({"role": "assistant"})
        yield frame({"reasoning": "正在拆解任务、匹配成员并计算排期…"})
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
