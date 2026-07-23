"""清小搭 OpenAI 兼容接口测试。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
AUTH = {"Authorization": "Bearer test-qingxiaoda-key"}


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
        "role": "assistant",
        "content": "好",
    }
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
