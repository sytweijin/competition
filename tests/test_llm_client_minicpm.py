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
