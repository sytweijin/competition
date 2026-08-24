"""MiniCPM-o Realtime 客户端与 API 路由测试（不发起真实网络连接）。"""

import json
import base64
import io
import struct

import pytest
import numpy as np
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services import realtime_client
from app.services.realtime_client import (
    RealtimeChatResult,
    RealtimeClient,
    RealtimeError,
)


class FakeWebSocket:
    def __init__(self, events):
        self._events = list(events)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def recv(self):
        raw = self._events.pop(0)
        if isinstance(raw, (dict, list)):
            return json.dumps(raw, ensure_ascii=False)
        return raw

    async def send(self, data):
        self.sent.append(json.loads(data))


class FakeConnector:
    """模拟 websockets.connect() 返回的 awaitable + async context manager。"""

    def __init__(self, fake):
        self.fake = fake

    async def __aenter__(self):
        return self.fake

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_chat_follows_realtime_event_sequence(monkeypatch):
    events = [
        {"type": "session.queue_done"},
        {"type": "session.created"},
        {"type": "response.output.delta", "kind": "text", "text": "你好，"},
        {"type": "response.output.delta", "kind": "text", "text": "世界"},
        {"type": "response.done", "text": "你好，世界", "reason": "turn_end"},
    ]
    fake = FakeWebSocket(events)
    captured = {}

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["headers"] = kwargs.get("additional_headers")
        return FakeConnector(fake)

    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(realtime_client.websockets, "connect", fake_connect)

    result = await RealtimeClient().chat(
        messages=[{"role": "user", "content": "测试"}])

    assert result.text == "你好，世界"
    assert captured["uri"].startswith(
        "wss://api.modelbest.cn/v1/realtime?")
    assert "mode=chat" in captured["uri"]
    assert "model=MiniCPM-o-4.5-Realtime" in captured["uri"]
    assert captured["headers"] == {
        "Authorization": "Bearer test-key"}
    sent_types = [event["type"] for event in fake.sent]
    assert sent_types == [
        "session.init", "input.append", "session.close"]
    input_event = fake.sent[1]
    assert input_event["input"]["streaming"] is True
    assert input_event["input"]["generation"]["max_new_tokens"] == 1024
    assert input_event["input"]["omni_mode"] is False
    assert input_event["input"]["messages"] == [
        {"role": "user", "content": "测试"}]


@pytest.mark.asyncio
async def test_chat_merges_system_prompt_and_multimodal_content(monkeypatch):
    events = [
        {"type": "session.queue_done"},
        {"type": "session.created"},
        {"type": "response.output.delta", "kind": "text", "text": "ok"},
        {"type": "response.done", "text": "ok"},
    ]
    fake = FakeWebSocket(events)

    def fake_connect(uri, **kwargs):
        return FakeConnector(fake)

    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(realtime_client.websockets, "connect", fake_connect)

    content = [{"type": "text", "text": "描述这张图"}]
    await RealtimeClient().chat(
        messages=[{"role": "user", "content": content}],
        system_prompt="你是简洁助手",
        omni_mode=True,
    )

    input_event = fake.sent[1]
    assert input_event["input"]["omni_mode"] is True
    messages = input_event["input"]["messages"]
    assert messages[0] == {"role": "system", "content": "你是简洁助手"}
    assert messages[1]["content"] == content


@pytest.mark.asyncio
async def test_chat_accepts_platform_output_text_events(monkeypatch):
    events = [
        {"type": "session.queue_done"},
        {"type": "session.created"},
        {"type": "response.output_text.delta", "text": "平台版"},
        {"type": "response.output_text.delta", "text": "回复"},
        {"type": "response.output_audio.delta", "audio": "AAAA"},
        {"type": "response.done", "text": "平台版回复"},
    ]
    fake = FakeWebSocket(events)

    def fake_connect(uri, **kwargs):
        return FakeConnector(fake)

    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(realtime_client.websockets, "connect", fake_connect)

    result = await RealtimeClient().chat(
        messages=[{"role": "user", "content": "测试"}],
        tts_enabled=True,
    )

    assert result.text == "平台版回复"
    assert result.audio_chunks == ["AAAA"]


@pytest.mark.asyncio
async def test_server_error_event_is_classified(monkeypatch):
    events = [
        {"type": "session.queue_done"},
        {"type": "session.created"},
        {"type": "error", "error": {
            "code": "queue_full",
            "message": "Queue full",
            "type": "server_error",
        }},
    ]
    fake = FakeWebSocket(events)

    def fake_connect(uri, **kwargs):
        return FakeConnector(fake)

    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(realtime_client.websockets, "connect", fake_connect)

    with pytest.raises(RealtimeError) as exc_info:
        await RealtimeClient().chat(
            messages=[{"role": "user", "content": "测试"}])
    assert "Queue full" in str(exc_info.value)
    assert exc_info.value.error_type == "rate_limit"


@pytest.mark.asyncio
async def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL", "")
    with pytest.raises(RealtimeError) as exc_info:
        await RealtimeClient().chat(
            messages=[{"role": "user", "content": "测试"}])
    assert "未配置" in str(exc_info.value)
    assert exc_info.value.error_type == "auth_error"


@pytest.mark.asyncio
async def test_local_chat_skips_queue_and_auth(monkeypatch):
    events = [
        {"type": "session.created"},
        {"type": "response.output.delta", "kind": "text", "text": "本地"},
        {"type": "response.output.delta", "kind": "text", "text": "回复"},
        {"type": "response.done", "text": "本地回复"},
    ]
    fake = FakeWebSocket(events)
    captured = {}

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return FakeConnector(fake)

    monkeypatch.setattr(
        realtime_client.config, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_client.config, "ASCEND_OMNI_WS_URL",
        "ws://127.0.0.1:28099/backend")
    monkeypatch.setattr(realtime_client.websockets, "connect", fake_connect)

    result = await RealtimeClient().chat(
        messages=[{"role": "user", "content": "测试"}])

    assert result.text == "本地回复"
    assert captured["uri"] == "ws://127.0.0.1:28099/backend"
    assert "additional_headers" not in captured["kwargs"]
    sent_types = [event["type"] for event in fake.sent]
    assert sent_types == ["session.init", "input.append"]
    init_payload = fake.sent[0]["payload"]
    assert init_payload["mode"] == "turn_based"
    assert init_payload["use_tts"] is False
    input_event = fake.sent[1]["input"]
    assert "omni_mode" not in input_event
    assert "enable_thinking" not in input_event


@pytest.mark.asyncio
async def test_realtime_chat_route(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(text="昇腾版回复")

    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/chat",
            json={"message": "你好"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "昇腾版回复"
    assert payload["mode"] == "chat"


@pytest.mark.asyncio
async def test_realtime_status_reflects_config(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/api/realtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["mode"] == "chat"


@pytest.mark.asyncio
async def test_local_status_enabled_without_api_key(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL",
        "ws://127.0.0.1:28099/backend")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/api/realtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["backend"] == "local"


def test_pcm_float32_converted_to_wav():
    """24kHz float32 PCM 应转成 16bit 单声道 WAV，数值不失真。"""
    samples = np.array([0.0, 0.5, -1.0, 0.25], dtype="<f4")
    pcm = samples.tobytes()
    wav_b64 = RealtimeClient.pcm_to_wav_base64(
        base64.b64encode(pcm).decode("utf-8"))
    wav = base64.b64decode(wav_b64)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    fmt = struct.unpack("<4sIHHIIHH", wav[12:36])
    assert fmt[1] == 16          # fmt chunk size
    assert fmt[2] == 1           # PCM
    assert fmt[3] == 1           # mono
    assert fmt[4] == 24000       # sample rate
    assert fmt[7] == 16          # bits
    data = struct.unpack("<4h", wav[44:52])
    assert data == (0, 16384, -32767, 8192)


def test_pcm_wav_input_passthrough():
    raw = b"RIFFfakewaveheader"
    b64 = base64.b64encode(raw).decode("utf-8")
    assert RealtimeClient.pcm_to_wav_base64(b64) == b64


def test_pcm_empty_returns_empty():
    assert RealtimeClient.pcm_to_wav_base64("") == ""


def test_result_audio_wav_property_converts_chunks():
    samples = np.zeros(100, dtype="<f4")
    chunk = base64.b64encode(samples.tobytes()).decode("utf-8")
    result = RealtimeChatResult(text="ok", audio_chunks=[chunk])
    wav = base64.b64decode(result.audio_wav_base64)
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_realtime_chat_route_returns_playable_wav(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    pcm = np.array([0.1, -0.2], dtype="<f4").tobytes()
    audio_b64 = base64.b64encode(pcm).decode("utf-8")

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(
            text="语音回复测试", audio_chunks=[audio_b64])

    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/chat",
            json={"message": "你好", "tts_enabled": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "语音回复测试"
    assert payload["audio_base64"] == audio_b64
    wav = base64.b64decode(payload["audio_wav_base64"])
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_realtime_transcribe_route(monkeypatch):
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    captured = {}

    def fake_transcribe(filename, content, labeled):
        captured["filename"] = filename
        captured["labeled"] = labeled
        return "下周一完成调研"

    monkeypatch.setattr(media, "audio_transcribe_text", fake_transcribe)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/transcribe",
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "下周一完成调研"
    assert payload["source"] == "realtime"
    assert captured["labeled"] is False


@pytest.mark.asyncio
async def test_realtime_transcribe_too_large(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/transcribe",
            files={"file": (
                "voice.webm", b"x" * (15 * 1024 * 1024 + 1), "audio/webm")},
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_realtime_transcribe_error_returns_502(monkeypatch):
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    def fake_transcribe(filename, content, labeled):
        raise ValueError("未配置语音转写模型")

    monkeypatch.setattr(media, "audio_transcribe_text", fake_transcribe)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/transcribe",
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 502
    assert "未配置语音转写模型" in response.json()["detail"]


@pytest.mark.asyncio
async def test_realtime_chat_tts_failure_retries_text_only(monkeypatch):
    """TTS 导致会话关闭时应自动降级为纯文本重试，对话不中断。"""
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    calls = []

    async def fake_chat(self, **kwargs):
        calls.append(kwargs.get("tts_enabled"))
        if kwargs.get("tts_enabled"):
            raise RealtimeError("连接已关闭", "connection_error")
        return RealtimeChatResult(text="纯文本回复")

    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/chat",
            json={"message": "你好", "tts_enabled": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "纯文本回复"
    assert payload["tts_failed"] is True
    assert payload["audio_wav_base64"] == ""
    assert calls == [True, False]


@pytest.mark.asyncio
async def test_voice_chat_route_returns_reply(monkeypatch):
    """直接语音对话：录音作为音频消息发给 MiniCPM-o，返回文本回答。"""
    import app.web.routers.realtime as realtime_router
    import app.services.omni_chat as omni_chat
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(omni_chat, "ASCEND_OMNI_WS_URL", "")
    captured = {}

    def fake_decode(content):
        captured["bytes"] = content
        return b"pcm-bytes"

    async def fake_chat(self, **kwargs):
        captured.setdefault("omni_mode", []).append(kwargs.get("omni_mode"))
        captured.setdefault("tts", []).append(kwargs.get("tts_enabled"))
        captured.setdefault("sys", []).append(kwargs.get("system_prompt"))
        return RealtimeChatResult(text="语音对话回复")

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/voice-chat",
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "语音对话回复"
    assert payload["backend"] == "map"
    assert payload["tts_failed"] is False
    assert payload["audio_wav_base64"] == ""
    assert True in captured["omni_mode"]  # 音频转写/理解调用走 omni
    assert captured["tts"] == [False, False]
    assert any(captured["sys"])  # 主对话调用带系统提示词


@pytest.mark.asyncio
async def test_voice_chat_tts_failure_retries_text_only(monkeypatch):
    """语音对话开启 TTS 失败时降级纯文本重试。"""
    import app.web.routers.realtime as realtime_router
    import app.services.omni_chat as omni_chat
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(omni_chat, "ASCEND_OMNI_WS_URL", "")
    calls = []

    def fake_decode(content):
        return b"pcm-bytes"

    async def fake_chat(self, **kwargs):
        calls.append(kwargs.get("tts_enabled"))
        if kwargs.get("tts_enabled"):
            raise RealtimeError("连接已关闭", "connection_error")
        return RealtimeChatResult(text="纯文本回复")

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/voice-chat",
            data={"tts_enabled": "true"},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "纯文本回复"
    assert payload["tts_failed"] is True
    assert payload["audio_wav_base64"] == ""
    # 云端两步：第一次尝试 = 转写(False)+主调用(True,失败)；重试 = 转写(False)+主调用(False)
    assert calls == [False, True, False, False]


@pytest.mark.asyncio
async def test_voice_chat_carries_history_and_returns_transcript(monkeypatch):
    """语音对话携带多轮历史，云端返回转写文本供前端存入记忆。"""
    import app.services.media_analysis as media
    import app.services.realtime_client as rt
    import app.web.routers.realtime as realtime_router
    import app.services.omni_chat as omni_chat

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(omni_chat, "ASCEND_OMNI_WS_URL", "")
    call_count = [0]
    captured = {}

    def fake_decode(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        call_count[0] += 1
        captured["messages_%d" % call_count[0]] = kwargs.get("messages")
        if call_count[0] == 1:
            return RealtimeChatResult(text="测试转写文本")
        return RealtimeChatResult(text="最终回答")

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    history = [
        {"role": "user", "content": "[语音] 谁的负担最重"},
        {"role": "assistant", "content": "王五的设计任务最重"},
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/voice-chat",
            data={"history": json.dumps(history)},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "最终回答"
    assert payload["transcript"] == "测试转写文本"
    # 云端两步：第一步转写；第二步 历史 + 转写文本
    assert call_count[0] == 2
    second = captured["messages_2"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[0]["content"] == "[语音] 谁的负担最重"
    assert second[-1]["content"] == "测试转写文本"


@pytest.mark.asyncio
async def test_understand_audio_local_carries_history(monkeypatch):
    """本地昇腾路径：历史摊平进文本上下文（A3 忽略分条消息，摊平实测有效）。"""
    import app.services.omni_chat as omni
    import app.services.realtime_client as rt

    captured = {}

    async def fake_chat(self, **kwargs):
        captured["messages"] = kwargs.get("messages")
        return RealtimeChatResult(text="本地回答")

    monkeypatch.setattr(
        omni, "ASCEND_OMNI_WS_URL", "ws://127.0.0.1:28099/backend")
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    result = await omni.understand_audio(
        "YXVkaW8=", "系统提示", "请听音频", history=[
            {"role": "user", "content": "上一轮用户"},
            {"role": "assistant", "content": "上一轮回答"},
        ])
    assert result.text == "本地回答"
    msgs = captured["messages"]
    assert [m["role"] for m in msgs] == ["user"]
    parts = msgs[0]["content"]
    assert parts[0]["type"] == "text"
    assert "上一轮用户" in parts[0]["text"]
    assert "上一轮回答" in parts[0]["text"]
    assert parts[1]["type"] == "audio"
    assert parts[1]["data"] == "YXVkaW8="


def test_split_pcm_b64_chunks_long_audio():
    """本地长音频按 12 秒上限分片（无静音点时固定切分），避免崩溃。"""
    import base64
    from app.services.omni_chat import _split_pcm_b64

    raw = b"\x00\x00\x00\x00" * (45 * 16000)
    b64 = base64.b64encode(raw).decode("ascii")
    chunks = _split_pcm_b64(b64)
    assert len(chunks) == 4
    decoded = b"".join(base64.b64decode(c) for c in chunks)
    assert decoded == raw
    assert all(
        len(base64.b64decode(c)) <= 12 * 16000 * 4 for c in chunks)
    assert len(_split_pcm_b64(base64.b64encode(b"abc").decode())) == 1


def test_split_pcm_b64_prefers_silence_cuts():
    """有静音断句时按句子切分，而不是固定间隔切断句子。"""
    import base64
    import math
    import numpy as np
    from app.services.omni_chat import _split_pcm_b64

    sr = 16000
    # 7 段语音（各 3 秒 500Hz 正弦）+ 7 段 1 秒静音 + 尾段 3 秒 → 31 秒
    # 超过 12 秒上限才触发分片；贪心分组应得 3 片（10.5+10.5+10 秒）
    def tone(seconds):
        n = int(sr * seconds)
        t = np.arange(n) / sr
        return (0.05 * np.sin(2 * math.pi * 500 * t)).astype("<f4").tobytes()

    silence = b"\x00\x00\x00\x00" * (sr * 1)
    raw = (tone(3) + silence) * 7 + tone(3)
    chunks = _split_pcm_b64(base64.b64encode(raw).decode("ascii"))
    assert len(chunks) == 3
    assert b"".join(base64.b64decode(c) for c in chunks) == raw


def test_looks_like_canned_reply_rejects_assistant_smalltalk():
    from app.services.omni_chat import _looks_like_canned_reply

    assert _looks_like_canned_reply("")
    assert _looks_like_canned_reply(
        "你好！很高兴为你提供帮助。请告诉我你具体需要什么。")
    assert _looks_like_canned_reply("我是由阿里云开发的语言模型。")
    assert _looks_like_canned_reply(
        "Hello! It seems like you might have made a mistake.")
    assert not _looks_like_canned_reply("下周一完成调研")
    assert not _looks_like_canned_reply("你好，我叫小红")


def test_flatten_history_skips_placeholder():
    from app.services.omni_chat import _flatten_history

    text = _flatten_history([
        {"role": "user", "content": "我是小红"},
        {"role": "assistant", "content": "好的，小红你好"},
        {"role": "user", "content": "[语音消息]"},
    ])
    assert "我是小红" in text
    assert "好的，小红你好" in text
    assert "[语音消息]" not in text


@pytest.mark.asyncio
async def test_understand_audio_local_merges_chunks(monkeypatch):
    """本地长音频：分片后走合并步骤，合并提示含分片结果与原始要求。"""
    import base64
    import app.services.omni_chat as omni
    import app.services.realtime_client as rt

    calls = []

    async def fake_chat(self, **kwargs):
        calls.append(kwargs.get("messages"))
        return RealtimeChatResult(text="分片回答")

    monkeypatch.setattr(
        omni, "ASCEND_OMNI_WS_URL", "ws://127.0.0.1:28099/backend")
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    raw = b"\x00\x00\x00\x00" * (58 * 16000)  # 58 秒 → 5 片（守卫内）
    b64 = base64.b64encode(raw).decode("ascii")
    chunks = omni._split_pcm_b64(b64)
    await omni.understand_audio(b64, "", "请整理会议", max_new_tokens=128)
    # 期望调用数 = 分片数 + 分层合并次数（每层每组 ≤3）
    merge_calls = 0
    n = len(chunks)
    while n > 1:
        groups = (n + 2) // 3
        merge_calls += groups
        n = groups
    assert len(calls) == len(chunks) + merge_calls
    merge_msg = calls[-1][0]["content"]
    assert "分片处理的结果" in merge_msg
    assert "原始任务要求" in merge_msg
    assert "请整理会议" in merge_msg


@pytest.mark.asyncio
async def test_voice_requirement_returns_understanding(monkeypatch):
    """语音需求理解：云端转写后理解，返回需求要点而非原话。"""
    import app.services.media_analysis as media
    import app.services.realtime_client as rt
    import app.web.routers.realtime as realtime_router
    import app.services.omni_chat as omni_chat

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(omni_chat, "ASCEND_OMNI_WS_URL", "")
    call_count = [0]

    def fake_decode(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return RealtimeChatResult(text="我需要重点围绕创新点提问")
        return RealtimeChatResult(
            text="需求要点：请评委重点围绕创新点与技术架构提问。")

    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/realtime/voice-requirement",
            files={"file": ("v.webm", b"fake-audio", "audio/webm")},
        )
    assert resp.status_code == 200
    assert resp.json()["text"] == (
        "需求要点：请评委重点围绕创新点与技术架构提问。")
    assert call_count[0] == 2  # 云端两步：转写 → 理解


@pytest.mark.asyncio
async def test_voice_chat_not_configured_503(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/voice-chat",
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_realtime_tts_returns_wav(monkeypatch):
    """文本朗读接口应返回可播放 WAV。"""
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    pcm = np.array([0.1, -0.2], dtype="<f4").tobytes()
    audio_b64 = base64.b64encode(pcm).decode("utf-8")
    captured = {}

    async def fake_chat(self, **kwargs):
        captured["tts"] = kwargs.get("tts_enabled")
        assert "朗读" in kwargs["messages"][0]["content"]
        return RealtimeChatResult(text="", audio_chunks=[audio_b64])

    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/tts", json={"text": "你好"}
        )

    assert response.status_code == 200
    assert captured["tts"] is True
    wav = base64.b64decode(response.json()["audio_wav_base64"])
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_realtime_tts_not_configured_503(monkeypatch):
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/tts", json={"text": "你好"}
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_realtime_tts_local_backend_rejected(monkeypatch):
    """本地昇腾 TTS 已知不可用，朗读接口应直接拒绝而不触发模型调用。"""
    import app.web.routers.realtime as realtime_router

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL",
        "ws://127.0.0.1:28099/backend")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/tts", json={"text": "你好"}
        )

    assert response.status_code == 501
    assert "本地昇腾" in response.json()["detail"]


def test_extract_video_frames_returns_jpegs():
    """从生成的测试视频中均匀抽帧，返回 JPEG。"""
    import av
    import numpy as np

    from app.services.media_analysis import extract_video_frames

    buf = io.BytesIO()
    container = av.open(buf, mode="w", format="mp4")
    stream = container.add_stream("mpeg4", rate=10)
    stream.width = 64
    stream.height = 48
    for i in range(12):
        arr = np.full((48, 64, 3), (i * 20) % 255, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

    frames = extract_video_frames(buf.getvalue(), max_frames=4)
    assert 1 <= len(frames) <= 4
    assert frames[0][:2] == b"\xff\xd8"  # JPEG SOI 标记


def test_parse_turn_text():
    """答辩回合输出应按标记解析为 摘要 + 评委回复。"""
    from app.web.routers.realtime import _parse_turn_text

    summary, reply = _parse_turn_text(
        "【回答摘要】\n我负责调研并完成报告。\n【评委回复】\n回答清晰，请补充数据来源。")
    assert summary == "我负责调研并完成报告。"
    assert reply == "回答清晰，请补充数据来源。"

    summary, reply = _parse_turn_text("没有标记的纯文本")
    assert summary == ""
    assert reply == "没有标记的纯文本"


def test_clean_transcript_strips_echo_tails():
    """云端转写应去掉模型附带的确认语/客套尾巴，只保留用户原话。"""
    from app.services.omni_chat import _clean_transcript

    assert _clean_transcript("帮我看看任务安排，好的，请问有什么可以帮您？") == \
        "帮我看看任务安排"
    assert _clean_transcript("你好") == "你好"
    assert _clean_transcript("用户说：帮我拆分任务。好的，我这就帮您看看") == \
        "帮我拆分任务"
    assert _clean_transcript("好的，就这么办") == "好的，就这么办"
    assert _clean_transcript("今天组会确定，调研报告下周一提交。") == \
        "今天组会确定，调研报告下周一提交。"


@pytest.mark.asyncio
async def test_realtime_performance_returns_analysis(monkeypatch):
    """答辩录像分析：抽帧看表情 + 抽音频转写回答。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    captured = {}

    def fake_frames(content, max_frames):
        captured["frames"] = max_frames
        return [b"JPEG1", b"JPEG2"]

    def fake_audio(content):
        return b"pcm"

    def fake_run(parts, max_tokens, omni_mode, timeout=180):
        assert any(p["type"] == "image" for p in parts)
        return "表现自信，建议放慢语速"

    def fake_written(filename, content):
        return "我的回答内容"

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_run)
    monkeypatch.setattr(media, "audio_to_written_answer", fake_written)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/performance",
            files={"file": ("answer.webm", b"fake-video", "video/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "表现自信" in payload["analysis"]
    assert payload["analysis"].count("第") == 2
    assert payload["answer"] == "我的回答内容"
    assert payload["frames"] == 2
    assert captured["frames"] == 4


@pytest.mark.asyncio
async def test_realtime_performance_without_frames(monkeypatch):
    """视频无画面帧时应返回空分析而不报错。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")

    def fake_frames(content, max_frames):
        raise ValueError("视频解码失败")

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/performance",
            files={"file": ("answer.webm", b"bad-video", "video/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"] == ""
    assert payload["frames"] == 0
    assert payload["warning"]


@pytest.mark.asyncio
async def test_interview_turn_parses_summary_and_reply(monkeypatch):
    """答辩直接语音对话：评委听懂音频后返回 摘要 + 点评/追问。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    captured = {}

    def fake_frames(content, max_frames):
        return []

    def fake_audio(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        captured["sys"] = kwargs.get("system_prompt")
        captured.setdefault("omni", []).append(kwargs.get("omni_mode"))
        return RealtimeChatResult(
            text="【回答摘要】\n我负责调研并完成报告。\n"
                 "【评委回复】\n回答清晰，请补充数据来源。")

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/interview-turn",
            data={"system_prompt": "你是答辩评委"},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "我负责调研并完成报告。"
    assert payload["reply"] == "回答清晰，请补充数据来源。"
    assert True in captured["omni"]
    assert "答辩评委" in captured["sys"]


@pytest.mark.asyncio
async def test_interview_turn_video_adds_observations(monkeypatch):
    """答辩视频对话：评委听回答 + 看画面，回复附带表现观察。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    def fake_frames(content, max_frames):
        return [b"JPEG1", b"JPEG2"]

    def fake_audio(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(
            text="【回答摘要】\n要点\n【评委回复】\n追问内容")

    def fake_run(parts, max_tokens, omni_mode, timeout=180):
        return "表情自然，眼神专注"

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/interview-turn",
            files={"file": ("answer.webm", b"fake-video", "video/webm")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "要点"
    assert "追问内容" in payload["reply"]
    assert "📹 表现观察" in payload["reply"]
    assert "第 1 帧" in payload["reply"]


@pytest.mark.asyncio
async def test_interview_turn_hollow_reply_returns_502(monkeypatch):
    """答辩语音回合空转（评委未听懂）应返回 502 明确提示而非空转结果。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    def fake_frames(content, max_frames):
        return []

    def fake_audio(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        return RealtimeChatResult(
            text="用户尚未提供具体的回答内容，无法判断其是否正面回答了问题。")

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(
        realtime_router.RealtimeClient, "chat", fake_chat)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/realtime/interview-turn",
            data={"system_prompt": "你是答辩评委"},
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )

    assert response.status_code == 502
    assert "未能听懂" in response.json()["detail"]


@pytest.mark.asyncio
async def test_interview_turn_carries_history(monkeypatch):
    """答辩语音/视频轮次携带完整历史，评委有全程记忆。"""
    import app.services.media_analysis as media
    import app.services.realtime_client as rt
    import app.web.routers.realtime as realtime_router
    import app.services.omni_chat as omni_chat

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    monkeypatch.setattr(omni_chat, "ASCEND_OMNI_WS_URL", "")
    call_count = [0]
    captured = {}

    def fake_frames(content, max_frames):
        return []

    def fake_audio(content):
        return b"pcm"

    async def fake_chat(self, **kwargs):
        call_count[0] += 1
        captured["messages_%d" % call_count[0]] = kwargs.get("messages")
        if call_count[0] == 1:
            return RealtimeChatResult(text="历史问题转写")
        return RealtimeChatResult(text=(
            "【回答摘要】\n要点\n【评委回复】\n点评与追问"))

    monkeypatch.setattr(media, "extract_video_frames", fake_frames)
    monkeypatch.setattr(media, "extract_audio_pcm16k", fake_audio)
    monkeypatch.setattr(rt.RealtimeClient, "chat", fake_chat)

    history = [
        {"role": "assistant", "content": "第一个问题"},
        {"role": "user", "content": "🎤 [语音回答] 要点：我是张三负责调研"},
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/realtime/interview-turn",
            data={
                "system_prompt": "你是答辩评委",
                "history": json.dumps(history),
            },
            files={"file": ("voice.webm", b"fake-audio", "audio/webm")},
        )
    assert resp.status_code == 200
    assert resp.json()["summary"] == "要点"
    second = captured["messages_2"]
    # 云端两步：转写（call1）→ 历史 + 转写文本（call2）
    assert second[0]["content"] == "第一个问题"
    assert second[1]["content"] == "🎤 [语音回答] 要点：我是张三负责调研"
    assert second[-1]["content"] == "历史问题转写"
