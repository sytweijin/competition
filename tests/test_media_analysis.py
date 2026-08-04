"""OCR/ASR media analysis tests."""

from app.file_analysis import extract_text


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

    monkeypatch.setattr(media, "LLM_API_KEY", "key")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "gpt-4o")
    monkeypatch.setattr(media, "_client", lambda: FakeClient())
    text = extract_text("photo.png", b"abc")
    assert "图片 OCR" in text
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

    monkeypatch.setattr(media, "LLM_API_KEY", "key")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "whisper-1")
    monkeypatch.setattr(media, "_client", lambda: FakeClient())
    text = extract_text("voice.mp3", b"abc")
    assert "音频转写" in text
    assert "下周一完成调研" in text
