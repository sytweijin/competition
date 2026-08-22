"""OCR/ASR media analysis tests."""

import io

import pytest

from app.file_analysis import extract_text


@pytest.fixture(autouse=True)
def _disable_real_media_calls(monkeypatch):
    import app.services.media_analysis as media

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "")
    monkeypatch.setattr(media, "APP_ASR_API_KEY", "")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "")
    monkeypatch.setattr(media, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(media, "ASCEND_OMNI_WS_URL", "")


def test_scanned_pdf_without_model_raises(monkeypatch):
    import app.services.media_analysis as media

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "")
    with pytest.raises(ValueError, match="APP_VISION_MODEL"):
        media.ocr_scanned_pdf("scan.pdf", b"%PDF")


def test_scanned_pdf_ocr_routes_each_page(monkeypatch):
    import app.services.media_analysis as media

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "key")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "gpt-4o")
    monkeypatch.setattr(
        media, "render_pdf_pages", lambda content: [b"png1", b"png2"])
    monkeypatch.setattr(
        media, "image_ocr_text",
        lambda label, content: f"OCR:{label}")

    text = media.ocr_scanned_pdf("scan.pdf", b"%PDF")
    assert "OCR:scan.pdf 第1页" in text
    assert "OCR:scan.pdf 第2页" in text


def test_extract_text_falls_back_to_ocr_for_blank_pdf(monkeypatch):
    import app.services.media_analysis as media
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.showPage()
    pdf.save()
    blank_pdf = buffer.getvalue()

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "key")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "gpt-4o")
    monkeypatch.setattr(
        media, "ocr_scanned_pdf",
        lambda filename, content: "扫描内容：请完成调研报告")

    text = extract_text("scan.pdf", blank_pdf)
    assert "调研报告" in text


def test_image_without_model_falls_back():
    text = extract_text("photo.png", b"abc")
    assert "未配置视觉模型" in text


def test_audio_without_model_falls_back():
    text = extract_text("voice.mp3", b"abc")
    assert "未配置语音转写模型" in text


def test_image_ocr_with_mock_model(monkeypatch):
    import app.services.media_analysis as media

    class FakeMessage:
        content = "图片中的文字：调研问卷"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Resp", (), {"choices": [FakeChoice()]})

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "key")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "gpt-4o")
    monkeypatch.setattr(
        media, "_client", lambda api_key, base_url: FakeClient())
    text = extract_text("photo.png", b"abc")
    assert "图片理解" in text
    assert "调研问卷" in text


def test_audio_asr_with_mock_model(monkeypatch):
    import app.services.media_analysis as media

    class FakeTranscriptions:
        def create(self, **kwargs):
            assert kwargs["file"][0] == "voice.mp3"
            return type("Resp", (), {"text": "会议录音：下周一完成调研"})()

    class FakeAudio:
        def __init__(self):
            self.transcriptions = FakeTranscriptions()

    class FakeClient:
        def __init__(self):
            self.audio = FakeAudio()

    monkeypatch.setattr(media, "APP_ASR_API_KEY", "key")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "whisper-1")
    monkeypatch.setattr(media, "APP_ASR_TRANSCRIPTION_MODE", "native")
    monkeypatch.setattr(
        media, "_client", lambda api_key, base_url: FakeClient())
    text = extract_text("voice.mp3", b"abc")
    assert "音频转写" in text
    assert "下周一完成调研" in text


def test_vision_uses_own_api_credentials(monkeypatch):
    import app.services.media_analysis as media

    class FakeMessage:
        content = "独立视觉服务返回的文字"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Resp", (), {"choices": [FakeChoice()]})

    class FakeClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    calls = []
    monkeypatch.setattr(media, "APP_VISION_API_KEY", "vision-key")
    monkeypatch.setattr(
        media, "APP_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "vision-model")

    def fake_client(api_key, base_url):
        calls.append((api_key, base_url))
        return FakeClient()

    monkeypatch.setattr(media, "_client", fake_client)
    text = media.image_ocr_text("photo.png", b"abc")
    assert "独立视觉服务返回的文字" in text
    assert calls == [("vision-key", "https://vision.example/v1")]


def test_asr_uses_own_api_credentials(monkeypatch):
    import app.services.media_analysis as media

    class FakeTranscriptions:
        def create(self, **kwargs):
            return type("Resp", (), {"text": "独立语音服务转写"})

    class FakeAudio:
        def __init__(self):
            self.transcriptions = FakeTranscriptions()

    class FakeClient:
        def __init__(self):
            self.audio = FakeAudio()

    calls = []
    monkeypatch.setattr(media, "APP_ASR_API_KEY", "asr-key")
    monkeypatch.setattr(
        media, "APP_ASR_BASE_URL", "https://asr.example/v1")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "asr-model")
    monkeypatch.setattr(media, "APP_ASR_TRANSCRIPTION_MODE", "native")

    def fake_client(api_key, base_url):
        calls.append((api_key, base_url))
        return FakeClient()

    monkeypatch.setattr(media, "_client", fake_client)
    text = media.audio_transcribe_text("voice.mp3", b"abc")
    assert "独立语音服务转写" in text
    assert calls == [("asr-key", "https://asr.example/v1")]


def test_asr_chat_mode_uses_input_audio(monkeypatch):
    import app.services.media_analysis as media

    class FakeMessage:
        content = "DashScope 转写结果"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "qwen3-asr-flash"
            content = kwargs["messages"][0]["content"][0]
            assert content["type"] == "input_audio"
            assert content["input_audio"]["data"].startswith(
                "data:audio/wav;base64,")
            return type("Resp", (), {"choices": [FakeChoice()]})

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(media, "APP_ASR_API_KEY", "key")
    monkeypatch.setattr(
        media, "APP_ASR_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "qwen3-asr-flash")
    monkeypatch.setattr(media, "APP_ASR_TRANSCRIPTION_MODE", "chat")
    monkeypatch.setattr(
        media, "_client", lambda api_key, base_url: FakeClient())
    text = media.audio_transcribe_text("voice.wav", b"audio-bytes")
    assert "DashScope 转写结果" in text


def test_asr_auto_uses_dashscope_native_for_qwen_audio(monkeypatch):
    import app.services.media_analysis as media

    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output": {"text": "欢迎使用百炼"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = json
        calls["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(media, "APP_ASR_API_KEY", "key")
    monkeypatch.setattr(
        media, "APP_ASR_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(
        media, "APP_ASR_MODEL", "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(media, "APP_ASR_TRANSCRIPTION_MODE", "auto")
    monkeypatch.setattr(media.httpx, "post", fake_post)

    text = media.audio_transcribe_text("voice.mp3", b"audio-bytes")

    assert "欢迎使用百炼" in text
    assert calls["url"] == media.DASHSCOPE_ASR_URL
    assert calls["headers"]["X-DashScope-SSE"] == "disable"
    assert calls["payload"]["model"] == "qwen-audio-3.0-asr-flash"
    assert calls["payload"]["parameters"]["format"] == "mp3"
    assert calls["payload"]["input"]["messages"][0]["content"][0][
        "input_audio"]["data"].startswith("data:audio/mpeg;base64,")


def test_asr_auto_keeps_chat_mode_for_other_dashscope_models(monkeypatch):
    import app.services.media_analysis as media

    class FakeMessage:
        content = "旧模型兼容模式转写结果"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        def create(self, **kwargs):
            return type("Resp", (), {"choices": [FakeChoice()]})

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self):
            self.chat = FakeChat()

    monkeypatch.setattr(media, "APP_ASR_API_KEY", "key")
    monkeypatch.setattr(
        media, "APP_ASR_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "qwen3-asr-flash")
    monkeypatch.setattr(media, "APP_ASR_TRANSCRIPTION_MODE", "auto")
    monkeypatch.setattr(
        media, "_client", lambda api_key, base_url: FakeClient())

    text = media.audio_transcribe_text("voice.wav", b"audio-bytes")

    assert "旧模型兼容模式转写结果" in text


def test_image_ocr_prefers_realtime_when_configured(monkeypatch):
    import app.services.media_analysis as media

    captured = {}

    def fake_realtime(content_parts, max_tokens, omni_mode):
        captured["parts"] = content_parts
        captured["omni_mode"] = omni_mode
        return "图片中的文字：昇腾海报"

    monkeypatch.setattr(media, "MAP_REALTIME_API_KEY", "key")
    monkeypatch.setattr(
        media, "MAP_REALTIME_MODEL", "MiniCPM-o-4.5-Realtime")
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_realtime)

    text = media.image_ocr_text("photo.png", b"abc")

    assert "昇腾海报" in text
    assert captured["parts"][1]["type"] == "image"
    assert captured["parts"][1]["data"]
    assert captured["omni_mode"] is False


def test_audio_transcribe_prefers_realtime_when_configured(monkeypatch):
    import app.services.media_analysis as media

    captured = {}

    def fake_decode(content):
        assert content == b"audio-bytes"
        return b"pcm-bytes"

    def fake_realtime(content_parts, max_tokens, omni_mode):
        captured["parts"] = content_parts
        captured["omni_mode"] = omni_mode
        return "会议录音：下周一完成调研"

    monkeypatch.setattr(media, "MAP_REALTIME_API_KEY", "key")
    monkeypatch.setattr(
        media, "MAP_REALTIME_MODEL", "MiniCPM-o-4.5-Realtime")
    monkeypatch.setattr(media, "_decode_audio_to_pcm16k", fake_decode)
    monkeypatch.setattr(media, "_run_realtime_media_chat", fake_realtime)

    text = media.audio_transcribe_text("voice.mp3", b"audio-bytes")

    assert "下周一完成调研" in text
    assert captured["parts"][1]["type"] == "audio"
    assert captured["parts"][1]["data"] == "cGNtLWJ5dGVz"
    assert captured["omni_mode"] is True


def test_decode_audio_to_pcm16k_returns_raw_pcm():
    import io
    import wave

    import app.services.media_analysis as media

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 160)

    pcm = media._decode_audio_to_pcm16k(buffer.getvalue())

    assert len(pcm) > 0
