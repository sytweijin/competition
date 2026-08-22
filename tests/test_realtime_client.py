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
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
    captured = {}

    def fake_decode(content):
        captured["bytes"] = content
        return b"pcm-bytes"

    async def fake_chat(self, **kwargs):
        captured["omni_mode"] = kwargs.get("omni_mode")
        captured["tts"] = kwargs.get("tts_enabled")
        assert kwargs.get("system_prompt")
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
    assert captured["omni_mode"] is True
    assert captured["tts"] is False


@pytest.mark.asyncio
async def test_voice_chat_tts_failure_retries_text_only(monkeypatch):
    """语音对话开启 TTS 失败时降级纯文本重试。"""
    import app.web.routers.realtime as realtime_router
    import app.services.media_analysis as media

    monkeypatch.setattr(
        realtime_router, "MAP_REALTIME_API_KEY", "test-key")
    monkeypatch.setattr(
        realtime_router, "ASCEND_OMNI_WS_URL", "")
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
    assert calls == [True, False]


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
        captured["omni"] = kwargs.get("omni_mode")
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
    assert captured["omni"] is True
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
