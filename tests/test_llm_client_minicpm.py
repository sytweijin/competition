"""合规模式（APP_MODEL_MODE=minicpm）下 LLMClient 的单元测试。

创新应用赛道要求"不得使用其他模型"，合规模式下所有文本调用都走
MiniCPM-o Realtime（本地 A3 / 云端），不再创建 DeepSeek 客户端。
"""

import pytest
from pydantic import BaseModel

from app.llm.client import LLMClient
from app.models.schemas import AgentError
from app.services.realtime_client import RealtimeChatResult


@pytest.fixture
def minicpm_mode(monkeypatch):
    import app.config as config
    import app.llm.client as llm_client

    monkeypatch.setattr(config, "APP_MODEL_MODE", "minicpm")
    monkeypatch.setattr(llm_client, "APP_MODEL_MODE", "minicpm")
    monkeypatch.setattr(
        llm_client, "ASCEND_OMNI_WS_URL", "ws://127.0.0.1:28099/backend")
    monkeypatch.setattr(llm_client, "MAP_REALTIME_API_KEY", "test-key")


class _MiniQA(BaseModel):
    answer: str


def test_minicpm_mode_does_not_create_external_client(minicpm_mode):
    client = LLMClient()
    assert client._mode == "minicpm"
    assert client._client is None
    assert client._enabled is True


def test_chat_messages_minicpm_routes_to_realtime(monkeypatch, minicpm_mode):
    import app.services.realtime_client as rt

    captured = {}

    async def fake_chat(self, **kwargs):
        captured["omni"] = kwargs.get("omni_mode")
        return RealtimeChatResult(text="正常回复")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)
    client = LLMClient()
    result = client.chat_messages(
        "你是助手", [{"role": "user", "content": "你好"}], timeout=30)
    assert result == "正常回复"
    assert captured["omni"] is False


def test_chat_text_minicpm_routes_to_realtime(monkeypatch, minicpm_mode):
    """合规模式下 chat_text（答辩问题生成）也必须走 MiniCPM-o Realtime。"""
    import app.services.realtime_client as rt

    captured = {}

    async def fake_chat(self, **kwargs):
        captured["sys"] = kwargs.get("system_prompt")
        captured["messages"] = kwargs.get("messages")
        captured["omni"] = kwargs.get("omni_mode")
        return RealtimeChatResult(text="请介绍你的项目创新点。")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)
    client = LLMClient()
    result = client.chat_text(
        "你是答辩评委", "请生成 10-15 道答辩问题", temperature=0.6)
    assert result == "请介绍你的项目创新点。"
    assert "你是答辩评委" in captured["sys"]
    assert captured["messages"][0]["content"] == "请生成 10-15 道答辩问题"
    assert captured["omni"] is False


def test_chat_structured_minicpm_parses_json(monkeypatch, minicpm_mode):
    import app.services.realtime_client as rt

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text='{"answer":"合规回复"}')

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)
    client = LLMClient()
    result = client.chat_structured(
        "系统", "拆解", _MiniQA, max_retries=1)
    assert isinstance(result, _MiniQA)
    assert result.answer == "合规回复"


def test_chat_messages_minicpm_garbage_returns_error(monkeypatch, minicpm_mode):
    import app.services.realtime_client as rt

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text="????????????")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)
    client = LLMClient()
    result = client.chat_messages(
        "你是助手", [{"role": "user", "content": "hi"}], timeout=30)
    assert isinstance(result, AgentError)
    assert "MiniCPM-o" in str(result)


def test_realtime_text_cloud_retries_canned_once(monkeypatch):
    """云端文本链路命中开场白/客套时，带防客套指令重试一次。"""
    import app.llm.client as llm_client
    import app.services.realtime_client as rt

    monkeypatch.setattr(llm_client, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(llm_client, "MAP_REALTIME_API_KEY", "test-key")
    calls = []

    async def fake_chat(self, **kwargs):
        calls.append(kwargs.get("system_prompt"))
        if len(calls) == 1:
            return RealtimeChatResult(
                text="你好，很高兴认识你。有什么我可以帮你的吗？")
        return RealtimeChatResult(text="请介绍你的项目核心创新点。")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    client = LLMClient()
    text = client._realtime_text(
        "你是答辩评委", [{"role": "user", "content": "请生成答辩问题"}])
    assert text == "请介绍你的项目核心创新点。"
    assert len(calls) == 2
    assert "不要输出问候语" in (calls[1] or "")


def test_realtime_text_cloud_garbage_retries_then_errors(monkeypatch):
    """云端文本链路连续乱码时，重试一次后仍报错，不把问号串交付。"""
    import app.llm.client as llm_client
    import app.services.realtime_client as rt

    monkeypatch.setattr(llm_client, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(llm_client, "MAP_REALTIME_API_KEY", "test-key")
    calls = {"n": 0}

    async def fake_chat(self, **kwargs):
        calls["n"] += 1
        return RealtimeChatResult(text="????????????")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    client = LLMClient()
    with pytest.raises(ValueError):
        client._realtime_text(
            "你是答辩评委", [{"role": "user", "content": "请生成答辩问题"}])
    assert calls["n"] == 2


def test_realtime_text_local_canned_raises_without_retry(monkeypatch,
                                                         minicpm_mode):
    """本地 A3 命中客套/乱码保持单次即抛，避免推理慢再翻倍等待。"""
    import app.services.realtime_client as rt

    calls = {"n": 0}

    async def fake_chat(self, **kwargs):
        calls["n"] += 1
        return RealtimeChatResult(
            text="你好，很高兴认识你。有什么我可以帮你的吗？")

    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    client = LLMClient()
    with pytest.raises(ValueError):
        client._realtime_text(
            "你是答辩评委", [{"role": "user", "content": "请生成答辩问题"}])
    assert calls["n"] == 1
