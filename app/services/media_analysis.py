"""图片 OCR 与音频转写：可接 OpenAI 兼容模型，无模型时保留元数据。"""

from __future__ import annotations

import base64
import io

import httpx

from app.config import (
    APP_ASR_API_KEY, APP_ASR_BASE_URL, APP_ASR_MODEL,
    APP_ASR_TRANSCRIPTION_MODE,
    APP_VISION_API_KEY, APP_VISION_BASE_URL, APP_VISION_MODEL,
)

MAX_OCR_PDF_PAGES = 20
OCR_PDF_DPI = 200
DASHSCOPE_ASR_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/"
    "aigc/multimodal-generation/generation"
)


def _client(api_key: str, base_url: str):
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _image_mime(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1]
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(suffix, "image/png")


def _audio_mime(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1]
    return {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "m4a": "audio/mp4",
        "webm": "audio/webm",
    }.get(suffix, "audio/mpeg")


def _asr_audio_format(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1]
    return suffix if suffix in {"mp3", "wav", "m4a", "webm"} else "mp3"


def image_ocr_text(filename: str, content: bytes) -> str:
    """调用视觉模型返回真实 OCR 文本；失败或未配置时抛 ValueError。"""
    if not (APP_VISION_API_KEY and APP_VISION_MODEL):
        raise ValueError("未配置视觉模型（APP_VISION_MODEL）")
    try:
        b64 = base64.b64encode(content).decode("utf-8")
        mime = _image_mime(filename)
        response = _client(
            APP_VISION_API_KEY, APP_VISION_BASE_URL
        ).chat.completions.create(
            model=APP_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请提取这张图片中的文字和关键信息，只输出内容。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            max_tokens=1200,
        )
        text = response.choices[0].message.content
    except Exception as exc:
        raise ValueError(
            f"视觉模型调用失败（{exc.__class__.__name__}）") from exc
    if not text or not text.strip():
        raise ValueError("视觉模型未返回 OCR 文本")
    return f"[图片 OCR] {filename}：{text}"


def analyze_image(filename: str, content: bytes) -> str:
    try:
        return image_ocr_text(filename, content)
    except ValueError as exc:
        return (
            f"[图片文件] 文件名：{filename}，大小：{len(content)} 字节。"
            f"视觉模型不可用（{exc}），建议人工查看。"
        )


def render_pdf_pages(content: bytes) -> list[bytes]:
    """把 PDF 页面渲染成 PNG，供扫描版 OCR 使用。"""
    try:
        import fitz
    except ImportError as exc:
        raise ValueError("PDF 渲染组件不可用（pymupdf 未安装）") from exc
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF 无法渲染：{exc.__class__.__name__}") from exc
    pages: list[bytes] = []
    try:
        for page in doc.pages()[:MAX_OCR_PDF_PAGES]:
            pix = page.get_pixmap(dpi=OCR_PDF_DPI)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    if not pages:
        raise ValueError("PDF 没有可 OCR 的页面")
    return pages


def ocr_scanned_pdf(filename: str, content: bytes) -> str:
    """扫描版 PDF OCR：逐页渲染后交给视觉模型提取文字。"""
    if not (APP_VISION_API_KEY and APP_VISION_MODEL):
        raise ValueError("扫描版 PDF 需要配置视觉模型（APP_VISION_MODEL）")
    pages = render_pdf_pages(content)
    parts: list[str] = []
    for index, page_bytes in enumerate(pages, 1):
        label = f"{filename} 第{index}页"
        parts.append(image_ocr_text(label, page_bytes))
    return "\n".join(parts)


def _dashscope_native_transcribe(filename: str, content: bytes) -> str:
    """调用 DashScope 原生 ASR endpoint，供 qwen-audio-* 系列使用。"""
    data_uri = (
        f"data:{_audio_mime(filename)};base64,"
        f"{base64.b64encode(content).decode('utf-8')}"
    )
    response = httpx.post(
        DASHSCOPE_ASR_URL,
        headers={
            "Authorization": f"Bearer {APP_ASR_API_KEY}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        },
        json={
            "model": APP_ASR_MODEL,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }],
                }],
            },
            "parameters": {
                "format": _asr_audio_format(filename),
                "sample_rate": "16000",
            },
        },
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    text = ((data.get("output") or {}).get("text") or "").strip()
    if not text:
        choices = data.get("choices") or []
        if choices:
            text = (
                ((choices[0].get("message") or {}).get("content") or "")
                .strip()
            )
    return text


def _asr_transcription_mode() -> str:
    mode = APP_ASR_TRANSCRIPTION_MODE
    if mode != "auto":
        return mode
    if "dashscope" in APP_ASR_BASE_URL.lower():
        # qwen-audio-* 只走 DashScope 原生 ASR endpoint；
        # 其余 DashScope ASR 模型仍走 OpenAI 兼容 chat completions。
        if APP_ASR_MODEL.startswith("qwen-audio-"):
            return "dashscope"
        return "chat"
    return "native"


def audio_transcribe_text(filename: str, content: bytes) -> str:
    """调用语音模型返回真实转写文本；失败或未配置时抛 ValueError。"""
    if not (APP_ASR_API_KEY and APP_ASR_MODEL):
        raise ValueError("未配置语音转写模型（APP_ASR_MODEL）")
    mode = _asr_transcription_mode()
    try:
        if mode == "dashscope":
            text = _dashscope_native_transcribe(filename, content)
        elif mode == "chat":
            data_uri = (
                f"data:{_audio_mime(filename)};base64,"
                f"{base64.b64encode(content).decode('utf-8')}"
            )
            client = _client(APP_ASR_API_KEY, APP_ASR_BASE_URL)
            response = client.chat.completions.create(
                model=APP_ASR_MODEL,
                messages=[{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }],
                }],
                max_tokens=2000,
            )
            text = response.choices[0].message.content
        else:
            client = _client(APP_ASR_API_KEY, APP_ASR_BASE_URL)
            response = client.audio.transcriptions.create(
                model=APP_ASR_MODEL,
                file=(filename, io.BytesIO(content), _audio_mime(filename)),
            )
            text = response.text
    except Exception as exc:
        raise ValueError(
            f"语音模型调用失败（{exc.__class__.__name__}）") from exc
    if not text or not text.strip():
        raise ValueError("语音模型未返回转写文本")
    return f"[音频转写] {filename}：{text}"


def analyze_audio(filename: str, content: bytes) -> str:
    try:
        return audio_transcribe_text(filename, content)
    except ValueError as exc:
        return (
            f"[音频文件] 文件名：{filename}，大小：{len(content)} 字节。"
            f"语音模型不可用（{exc}），建议人工收听。"
        )
