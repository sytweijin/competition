"""清小搭 OpenAI 兼容接口测试。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.llm.client import LLMClient
from app.main import app


client = TestClient(app)
AUTH = {"Authorization": "Bearer test-qingxiaoda-key"}


@pytest.fixture(autouse=True)
def disable_live_qingxiaoda_ai(monkeypatch):
    """协议回归测试不访问真实千问；生产环境默认开启。"""
    monkeypatch.setenv("QINGXIAODA_USE_AI", "false")


def test_models_requires_configured_key(monkeypatch):
    monkeypatch.delenv("QINGXIAODA_API_KEY", raising=False)

    response = client.get("/v1/models")

    assert response.status_code == 503


def test_models_rejects_missing_or_wrong_bearer(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    assert client.get("/v1/models").status_code == 401
    assert (
        client.get(
            "/v1/models",
            headers={"Authorization": "Bearer wrong-key"},
        ).status_code
        == 401
    )


def test_models_returns_openai_compatible_shape(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.get("/v1/models", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["data"][0]["id"] == "collaboration-planner"
    assert payload["data"][0]["object"] == "model"


def test_non_stream_completion_accepts_null_model_and_min_tokens(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "model": None,
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
            "max_tokens": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {
        "role": "assistant", "content": "好"}
    assert payload["choices"][0]["finish_reason"] == "stop"
    assert payload["usage"]["completion_tokens"] == 1


def test_stream_must_be_json_boolean(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "stream": "false",
        },
    )

    assert response.status_code == 422


def test_stream_completion_emits_role_content_stop_and_done(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "model": "collaboration-planner",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    data_lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert data_lines[-1] == "[DONE]"

    frames = [json.loads(line) for line in data_lines[:-1]]
    role_frames = [
        frame
        for frame in frames
        if frame["choices"][0]["delta"].get("role") == "assistant"
    ]
    content_frames = [
        frame
        for frame in frames
        if frame["choices"][0]["delta"].get("content")
    ]
    stop_frames = [
        frame
        for frame in frames
        if frame["choices"][0]["finish_reason"] == "stop"
    ]

    assert len(role_frames) == 1
    assert all("reasoning" not in frame["choices"][0]["delta"]
               for frame in frames)
    assert content_frames
    assert len(stop_frames) == 1
    assert stop_frames[0]["usage"]["completion_tokens"] >= 1


def test_project_prompt_runs_complete_planning_flow(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "项目：校园科技节宣传；截止日期：2026-08-10；"
                        "成员：小林(文案,统筹)、小陈(视觉设计,PPT制作)、"
                        "小周(数据分析,视频剪辑)。请完成任务拆解和智能分工。"
                    ),
                }
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200
    answer = response.json()["choices"][0]["message"]["content"]
    assert "校园科技节宣传" in answer
    assert "任务拆解与智能分工" in answer
    assert "排期" in answer
    assert "甘特图（文本版）" in answer
    assert "项目工作台" in answer


def test_relative_deadline_member_count_and_gantt_are_understood(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{
                "role": "user",
                "content": "我们3个人5天要完成一个PPT，生成一个甘特图",
            }],
            "stream": False,
        },
    )

    assert response.status_code == 200
    answer = response.json()["choices"][0]["message"]["content"]
    assert "PPT 制作项目" in answer
    assert "成员1、成员2、成员3" in answer
    assert (date.today() + timedelta(days=4)).isoformat() in answer
    assert "甘特图（文本版）" in answer
    assert "█" in answer


def test_followup_gantt_uses_previous_user_context(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "项目：迎新展示；成员：小林(PPT)、小陈(文案)、"
                        "小周(视觉)；7天完成答辩演示。"),
                },
                {"role": "assistant", "content": "可以，我先整理计划。"},
                {"role": "user", "content": "请继续生成甘特图"},
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200
    answer = response.json()["choices"][0]["message"]["content"]
    assert "迎新展示" in answer
    assert "小林、小陈、小周" in answer
    assert "甘特图（文本版）" in answer


def test_gantt_request_without_scope_asks_one_targeted_question(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{"role": "user", "content": "能生成甘特图吗？"}],
            "stream": False,
        },
    )

    answer = response.json()["choices"][0]["message"]["content"]
    assert "可以生成甘特图" in answer
    assert "项目交付物或目标" in answer


def test_qwen_normalizes_requirements_before_local_planning(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")
    monkeypatch.setenv("QINGXIAODA_USE_AI", "true")
    calls = []

    class StubLLM:
        def chat_messages(self, **kwargs):
            calls.append(kwargs)
            return "项目：千问整理项目；3个人；5天；交付物：PPT和甘特图"

    monkeypatch.setattr(LLMClient, "get_shared", lambda: StubLLM())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "项目：千问整理项目；成员：甲(PPT)、乙(文案)、丙(设计)；"
                        "5天完成PPT和甘特图。"),
                },
                {"role": "assistant", "content": "我先生成了初步计划。"},
                {"role": "user", "content": "请把演练任务增加进去并调整排期。"},
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200
    answer = response.json()["choices"][0]["message"]["content"]
    assert len(calls) == 1
    assert calls[0]["timeout"] == 18
    assert "千问整理项目" in answer
    assert "甲、乙、丙" in answer


def test_general_question_is_answered_without_planning(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")
    monkeypatch.setenv("QINGXIAODA_USE_AI", "true")
    calls = []

    class StubLLM:
        def chat_messages(self, **kwargs):
            calls.append(kwargs)
            return "北京是中国的首都。"

    monkeypatch.setattr(LLMClient, "get_shared", lambda: StubLLM())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{"role": "user", "content": "北京是中国的首都吗？"}],
            "stream": False,
        },
    )

    answer = response.json()["choices"][0]["message"]["content"]
    assert answer == "北京是中国的首都。"
    assert len(calls) == 1
    assert "任务拆解与智能分工" not in answer


def test_concept_question_about_gantt_is_not_planned(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")
    monkeypatch.setenv("QINGXIAODA_USE_AI", "true")

    class StubLLM:
        def chat_messages(self, **kwargs):
            return "甘特图是一种用时间条展示任务进度的项目管理图表。"

    monkeypatch.setattr(LLMClient, "get_shared", lambda: StubLLM())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"messages": [{"role": "user", "content": "什么是甘特图？"}]},
    )

    answer = response.json()["choices"][0]["message"]["content"]
    assert answer.startswith("甘特图是一种")
    assert "团队成员" not in answer


def test_how_to_question_about_project_plan_is_general_qa(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")

    class StubLLM:
        def chat_messages(self, **kwargs):
            return "制定项目计划通常先明确目标，再拆分里程碑并评估资源。"

    monkeypatch.setattr(LLMClient, "get_shared", lambda: StubLLM())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"messages": [{"role": "user", "content": "如何制定项目计划？"}]},
    )

    answer = response.json()["choices"][0]["message"]["content"]
    assert answer.startswith("制定项目计划通常")
    assert "团队成员" not in answer


def test_simple_structured_plan_uses_fast_local_path(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")
    monkeypatch.setenv("QINGXIAODA_USE_AI", "true")

    class FailIfCalled:
        def chat_messages(self, **kwargs):
            raise AssertionError("简单结构化规划不应等待千问二次整理")

    monkeypatch.setattr(LLMClient, "get_shared", lambda: FailIfCalled())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{
                "role": "user",
                "content": "我们3个人5天完成一个PPT，请生成甘特图",
            }],
        },
    )

    assert response.status_code == 200
    assert "甘特图（文本版）" in response.json()["choices"][0]["message"]["content"]


def test_general_stream_forwards_llm_content(monkeypatch):
    monkeypatch.setenv("QINGXIAODA_API_KEY", "test-qingxiaoda-key")
    calls = []

    class StubLLM:
        def stream_messages(self, **kwargs):
            calls.append(kwargs)
            yield "天空呈蓝色，"
            yield "主要与瑞利散射有关。"

    monkeypatch.setattr(LLMClient, "get_shared", lambda: StubLLM())
    response = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={
            "messages": [{"role": "user", "content": "天空为什么是蓝色的？"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "正在回答" in response.text
    assert "天空呈蓝色" in response.text
    assert "瑞利散射" in response.text
    assert calls[0]["timeout"] == 18
