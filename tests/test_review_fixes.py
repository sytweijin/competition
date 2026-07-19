# -*- coding: utf-8 -*-
"""针对 workbuddy 审查修复项的回归测试（P2-8/P2-10/P2-11/P1-2/P2-1）。"""

from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.llm.client import LLMClient, _classify_error
from app.agents.scoring import assign_with_balance
from app.editor import edit_plan
from app.models.schemas import (
    PlanOutput, SubTask, TeamMember,
    FullPlan, AssignmentInput, CourseInfo, TimelineOutput,
    QAOutput, ReportOutput, TaskEdit, EditPlanRequest,
)


# ──────────── LLM 客户端 mock 辅助 ────────────

def _resp(parsed=None, content=None):
    """构造形如 SDK 响应的对象：resp.choices[0].message.{parsed,content}"""
    msg = SimpleNamespace(parsed=parsed, content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _make_client(parse_fn=None, create_fn=None):
    """构造一个假的 OpenAI _client，只暴露 beta.chat.completions.parse / chat.completions.create。"""
    parse_obj = SimpleNamespace(parse=parse_fn or (lambda **kw: _resp()))
    beta = SimpleNamespace(chat=SimpleNamespace(completions=parse_obj))
    create_obj = SimpleNamespace(create=create_fn or (lambda **kw: _resp()))
    chat = SimpleNamespace(completions=create_obj)
    return SimpleNamespace(beta=beta, chat=chat)


def _client_with(parse_fn=None, create_fn=None):
    c = LLMClient()
    c._prefer_plain = False
    c._client = _make_client(parse_fn, create_fn)
    return c


def _plan_json():
    return '{"tasks":[{"id":"T1","name":"X","estimated_hours":4}],"summary":"t","reasoning":""}'


# ──────────── P2-8: 真 OpenAI 把结构化结果放 message.parsed ────────────

def test_structured_reads_parsed_field():
    """message.parsed 有值、content 为 None（真 OpenAI 形态）也能正确解析。"""
    plan = PlanOutput(tasks=[SubTask(id="T1", name="X", estimated_hours=4)], summary="t")
    client = _client_with(parse_fn=lambda **kw: _resp(parsed=plan, content=None))
    out = client.chat_structured("sys", "usr", PlanOutput, max_retries=1)
    assert isinstance(out, PlanOutput)
    assert out.tasks[0].id == "T1"


def test_structured_reads_content_json_when_no_parsed():
    """Aliyun 兼容端点：parsed=None、content 为合法 JSON 字符串。"""
    client = _client_with(parse_fn=lambda **kw: _resp(parsed=None, content=_plan_json()))
    out = client.chat_structured("sys", "usr", PlanOutput, max_retries=1)
    assert isinstance(out, PlanOutput)
    assert out.tasks[0].id == "T1"


def test_empty_structured_falls_back_to_plain_create():
    """structured 返回空（parsed=None, content=None）-> 回退 plain create + JSON 提取。"""
    def create_fn(**kw):
        return _resp(parsed=None, content="```json\n" + _plan_json() + "\n```")
    client = _client_with(parse_fn=lambda **kw: _resp(parsed=None, content=None), create_fn=create_fn)
    out = client.chat_structured("sys", "usr", PlanOutput, max_retries=1)
    assert isinstance(out, PlanOutput)
    assert out.tasks[0].id == "T1"


# ──────────── P2-10: parse_error 不重试 structured，直接回退 ────────────

def test_parse_error_skips_structured_retry():
    calls = {"parse": 0, "create": 0}

    def parse_fn(**kw):
        calls["parse"] += 1
        try:
            PlanOutput.model_validate({"tasks": "not-a-list"})
        except ValidationError as ve:
            raise ve

    def create_fn(**kw):
        calls["create"] += 1
        return _resp(parsed=None, content=_plan_json())

    client = _client_with(parse_fn=parse_fn, create_fn=create_fn)
    out = client.chat_structured("sys", "usr", PlanOutput, max_retries=3)
    assert isinstance(out, PlanOutput)
    assert calls["parse"] == 1, "parse_error 不应触发 structured 重试"
    assert calls["create"] == 1, "应回退到 plain create 一次"


def test_plain_response_locally_repairs_plan_fields_and_constraint_task():
    """AI JSON 可读但字段不规范时，本地修复而不是整份退回规则草案。"""
    raw = """{
      "task_list": [
        {
          "title": "实现文件加密功能（制作命令行界面即可，不要求使用图形界面）",
          "hours": "4小时",
          "skills": "Python, 密码学"
        },
        {
          "title": "制作界面即可，不要求使用图形界面）",
          "hours": 1
        }
      ]
    }"""
    client = _client_with(create_fn=lambda **kw: _resp(content=raw))
    client._prefer_plain = True
    out = client.chat_structured("sys", "usr", PlanOutput)
    assert isinstance(out, PlanOutput)
    assert len(out.tasks) == 1
    task = out.tasks[0]
    assert task.name == "实现文件加密功能"
    assert task.estimated_hours == 4
    assert task.required_skills == ["Python", "密码学"]
    assert "不要求使用图形界面" in task.description
    assert "）" not in task.name


# ──────────── P2-11: _classify_error 按异常类型分类 ────────────

def test_classify_error_basic_types():
    assert _classify_error(TimeoutError()) == "timeout"
    try:
        PlanOutput.model_validate({"tasks": "bad"})
    except ValidationError as ve:
        assert _classify_error(ve) == "parse_error"
    assert _classify_error(Exception("random noise")) == "unknown"


# ──────────── P1-2: assign_with_balance 保证全员参与 ────────────

def test_assign_with_balance_guarantees_everyone_participates():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="Frontend", estimated_hours=8, required_skills=["frontend"]),
    ], summary="t")
    members = [
        TeamMember(name="A", skill_tags=["frontend"]),
        TeamMember(name="B", skill_tags=[]),   # 零技能，原本会被忽略
        TeamMember(name="C", skill_tags=["docs"]),
    ]
    out = assign_with_balance(plan, members)
    names = set()
    for a in out.assignments:
        names.add(a.presenter)
        if a.qa_primary:
            names.add(a.qa_primary)
        names.update(a.qa_support or [])
    assert names == {"A", "B", "C"}, f"全员应都至少参与一个角色，实际 {names}"


# ──────────── P2-1: 新增任务时强制重算 matcher ────────────

def _base_plan_for_edit():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="C", description="d"),
            members=[TeamMember(name="A", skill_tags=["x"],
                                 daily_available_hours=4, available_hours=20)],
            deadline=date(2026, 9, 1),
        ),
        plan=PlanOutput(tasks=[SubTask(id="T1", name="orig", estimated_hours=4,
                                       required_skills=["x"])], summary="s", reasoning=""),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=1),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_editor_add_task_forces_matcher_recompute():
    """即使 recompute_matcher=False，新增任务也应触发 matcher 重算（否则新任务不在 qa_matrix）。"""
    req = EditPlanRequest(
        plan=_base_plan_for_edit(),
        edits=[TaskEdit(op="add", task=SubTask(id="T2", name="new",
                                               estimated_hours=4, required_skills=["x"]))],
        recompute_timeline=False,
        recompute_matcher=False,
    )
    result = edit_plan(req)
    task_ids = {a.task_id for a in result.qa_matrix.assignments}
    assert "T2" in task_ids, "新增任务必须被 matcher 覆盖"
