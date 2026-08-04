"""图片 OCR 与音频转写：可接 OpenAI 兼容模型，无模型时保留元数据。"""

from __future__ import annotations

import base64
import io

from app.config import (
    APP_ASR_MODEL, APP_VISION_MODEL, LLM_API_KEY, LLM_BASE_URL,
)


def _client():
    from openai import OpenAI
    kwargs = {"api_key": LLM_API_KEY}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
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
    }.get(suffix, "audio/mpeg")


def analyze_image(filename: str, content: bytes) -> str:
    if not (LLM_API_KEY and APP_VISION_MODEL):
        return (
            f"[图片文件] 文件名：{filename}，大小：{len(content)} 字节。"
            "未配置视觉模型，无法 OCR，建议人工查看。"
        )
    try:
        b64 = base64.b64encode(content).decode("utf-8")
        mime = _image_mime(filename)
        response = _client().chat.completions.create(
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
        return f"[图片 OCR] {filename}：{text}"
    except Exception as exc:
        return (
            f"[图片文件] 文件名：{filename}，大小：{len(content)} 字节。"
            f"视觉模型调用失败（{exc.__class__.__name__}），建议人工查看。"
        )


def analyze_audio(filename: str, content: bytes) -> str:
    if not (LLM_API_KEY and APP_ASR_MODEL):
        return (
            f"[音频文件] 文件名：{filename}，大小：{len(content)} 字节。"
            "未配置语音转写模型，建议人工收听。"
        )
    try:
        response = _client().audio.transcriptions.create(
            model=APP_ASR_MODEL,
            file=(filename, io.BytesIO(content), _audio_mime(filename)),
        )
        return f"[音频转写] {filename}：{response.text}"
    except Exception as exc:
        return (
            f"[音频文件] 文件名：{filename}，大小：{len(content)} 字节。"
            f"语音模型调用失败（{exc.__class__.__name__}），建议人工收听。"
        )
