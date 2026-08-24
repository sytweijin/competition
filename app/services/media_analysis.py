"""图片理解（OCR + 视觉内容）与音频转写：可接 OpenAI 兼容模型，无模型时保留元数据。"""

from __future__ import annotations

import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import (
    APP_ALLOW_EXTERNAL_MODELS,
    APP_ASR_API_KEY, APP_ASR_BASE_URL, APP_ASR_MODEL,
    APP_ASR_TRANSCRIPTION_MODE,
    APP_VISION_API_KEY, APP_VISION_BASE_URL, APP_VISION_MODEL,
    ASCEND_OMNI_WS_URL,
    MAP_REALTIME_API_KEY, MAP_REALTIME_MODEL,
)

MAX_OCR_PDF_PAGES = max(1, min(12, int(os.getenv("APP_OCR_MAX_PDF_PAGES", "6"))))
OCR_PDF_DPI = 200
MEDIA_MODEL_TIMEOUT = max(5, min(60, int(os.getenv("APP_MEDIA_TIMEOUT", "20"))))
DASHSCOPE_ASR_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/"
    "aigc/multimodal-generation/generation"
)


def _client(api_key: str, base_url: str):
    if not APP_ALLOW_EXTERNAL_MODELS:
        raise ValueError("外部模型已禁用（合规模式仅使用 MiniCPM-o 4.5）")
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


def _run_realtime_media_chat(
    content_parts: list[dict],
    max_tokens: int,
    omni_mode: bool,
    timeout: float = 180,
) -> str:
    """同步包装 MiniCPM-o Realtime，供媒体分析在独立线程中调用。"""
    import asyncio

    from app.services.realtime_client import RealtimeClient

    async def _call() -> str:
        result = await RealtimeClient().chat(
            messages=[{"role": "user", "content": content_parts}],
            max_new_tokens=max_tokens,
            timeout=timeout,
            omni_mode=omni_mode,
        )
        return result.text

    return asyncio.run(_call())


def _realtime_image_ocr_text(filename: str, content: bytes) -> str:
    """用 MiniCPM-o Realtime 理解图片：提取文字并描述非文字内容。"""
    b64 = base64.b64encode(content).decode("utf-8")
    content_parts = [
        {"type": "text", "text": (
            "请完整理解这张图片：1) 提取图中出现的所有文字；"
            "2) 描述图中非文字的内容（图表、流程图、界面、场景、物品、手绘等）；"
            "3) 结合项目需求场景，提炼可能与项目、交付物、任务相关的信息。"
            "只输出内容，不要客套。"
        )},
        {"type": "image", "data": b64},
    ]
    text = _run_realtime_media_chat(content_parts, 1200, False).strip()
    if not text:
        raise ValueError("MiniCPM-o Realtime 未返回图片理解结果")
    return f"[图片理解] {filename}：{text}"


def _decode_audio_to_pcm16k(content: bytes) -> bytes:
    """用 PyAV 把常见音频解码成 16kHz 单声道 float32 PCM。"""
    import io

    import av
    import numpy as np

    try:
        container = av.open(io.BytesIO(content))
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format="fltp", layout="mono", rate=16000)
        chunks = []
        try:
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray())
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray())
        finally:
            container.close()
    except Exception as exc:
        raise ValueError(
            f"音频解码失败（{type(exc).__name__}）") from exc
    if not chunks:
        raise ValueError("音频解码后没有可用采样")
    pcm = np.concatenate(chunks, axis=1).reshape(-1)
    return pcm.astype("<f4").tobytes()


def extract_video_frames(
    content: bytes, max_frames: int = 4, max_width: int = 640,
) -> list[bytes]:
    """从视频中均匀抽取若干帧，返回 JPEG bytes 列表（流式采样，不整段载入内存）。

    用于答辩录像的表情分析：只取时间轴上均匀分布的几帧，控制模型输入量。
    """
    import io

    import av
    from PIL import Image

    try:
        container = av.open(io.BytesIO(content))
        stream = container.streams.video[0]
        try:
            rate = stream.average_rate or stream.base_rate or 0
            total = (
                stream.frames
                or (
                    int(stream.duration * rate)
                    if stream.duration and rate else None
                )
            )
            step = max(1, (total // max_frames)) if total else 1
            picked = []
            count = 0
            for frame in container.decode(stream):
                if count % step == 0 and (total or len(picked) < max_frames):
                    picked.append(frame)
                    if total and len(picked) >= max_frames:
                        break
                count += 1
        finally:
            container.close()
    except Exception as exc:
        raise ValueError(
            f"视频解码失败（{type(exc).__name__}）") from exc
    if not picked:
        raise ValueError("视频中没有可分析的画面帧")
    out: list[bytes] = []
    for frame in picked[:max_frames]:
        try:
            img = frame.to_image().convert("RGB")
        except Exception:
            import numpy as np
            arr = frame.to_ndarray(format="rgb24")
            img = Image.fromarray(arr, mode="RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        out.append(buf.getvalue())
    return out


def extract_audio_pcm16k(content: bytes) -> bytes | None:
    """从视频中抽取音频并转成 16kHz 单声道 float32 PCM；无音轨返回 None。"""
    try:
        return _decode_audio_to_pcm16k(content)
    except Exception:
        return None


def _realtime_audio_transcribe_text(filename: str, content: bytes) -> str:
    """用 MiniCPM-o Realtime 直接转写常见音频文件（长音频自动分片防崩溃）。"""
    from app.services.omni_chat import (
        _LOCAL_AUDIO_MAX_SECONDS,
        _looks_like_canned_reply,
        _looks_like_garbage,
        _pcm_duration_seconds,
        _split_pcm_b64,
    )

    pcm = _decode_audio_to_pcm16k(content)
    b64 = base64.b64encode(pcm).decode("utf-8")
    if ASCEND_OMNI_WS_URL and _pcm_duration_seconds(
            b64) > _LOCAL_AUDIO_MAX_SECONDS:
        raise ValueError(
            f"本地昇腾后端暂不支持超过 {_LOCAL_AUDIO_MAX_SECONDS} 秒的音频"
            "（处理时间过长）："
            f"请分段（每段 ≤{_AUDIO_CHUNK_SECONDS} 秒）上传，或改用云端后端")
    parts_out: list[str] = []
    chunks = (
        _split_pcm_b64(b64) if ASCEND_OMNI_WS_URL else [b64])
    for chunk in chunks:
        content_parts = [
            {"type": "text", "text": (
                "这是用户的语音输入。请直接转写用户说出的原话："
                "不要思考过程，不要解释，不要复述，不要补充或确认，"
                "只输出用户说的话本身。"
            )},
            {"type": "audio", "data": chunk},
        ]
        text = _run_realtime_media_chat(content_parts, 2000, True).strip()
        if text and not (_looks_like_garbage(text)
                         or _looks_like_canned_reply(text)):
            parts_out.append(text)
    if not parts_out:
        raise ValueError(
            "MiniCPM-o Realtime 未返回有效转写文本"
            "（输出乱码或把转写指令当成了对话）")
    return "\n".join(parts_out)


def _labeled_transcription(text: str, filename: str, labeled: bool) -> str:
    """统一给转写文本加/不加 [音频转写] 前缀。"""
    cleaned = text.strip()
    if labeled:
        return f"[音频转写] {filename}：{cleaned}"
    return cleaned


WRITTEN_ANSWER_INSTRUCTION = (
    "这是用户的语音回答。请把用户说的内容整理成书面回答文字："
    "忠实于原意和细节，不要添加新的观点，不要评价，不要回复用户，"
    "只输出整理后的文字。"
)


def audio_to_written_answer(filename: str, content: bytes) -> str:
    """把用户口述的回答整理成书面文字（理解后成文，非逐字转写）。"""
    pcm = _decode_audio_to_pcm16k(content)
    b64 = base64.b64encode(pcm).decode("utf-8")
    if ASCEND_OMNI_WS_URL:
        # 本地昇腾：统一走 understand_audio（长音频分片 + 独立会话），
        # 避免 whisper 越界把整个 A3 服务打崩。
        import asyncio

        from app.services.omni_chat import understand_audio

        result = asyncio.run(understand_audio(
            b64, "", WRITTEN_ANSWER_INSTRUCTION,
            max_new_tokens=2000, timeout=180, tts_enabled=False))
        text = (result.text or "").strip()
        if not text:
            raise ValueError("未返回整理后的回答")
        return text
    parts = [
        {"type": "text", "text": WRITTEN_ANSWER_INSTRUCTION},
        {"type": "audio", "data": b64},
    ]
    text = _run_realtime_media_chat(parts, 2000, True).strip()
    if not text:
        raise ValueError("未返回整理后的回答")
    return text


def image_ocr_text(filename: str, content: bytes) -> str:
    """调用视觉模型理解图片（文字 + 非文字内容）；失败或未配置时抛 ValueError。"""
    if MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL:
        try:
            return _realtime_image_ocr_text(filename, content)
        except Exception as realtime_exc:
            if not (APP_VISION_API_KEY and APP_VISION_MODEL):
                raise ValueError(
                    f"视觉模型调用失败（{type(realtime_exc).__name__}）"
                ) from realtime_exc
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
                    {"type": "text", "text": (
                        "请完整理解这张图片：1) 提取图中出现的所有文字；"
                        "2) 描述图中非文字的内容（图表、流程图、界面、场景、物品、手绘等）；"
                        "3) 结合项目需求场景，提炼可能与项目、交付物、任务相关的信息。"
                        "只输出内容，不要客套。"
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            max_tokens=1200,
            timeout=MEDIA_MODEL_TIMEOUT,
        )
        text = response.choices[0].message.content
    except Exception as exc:
        raise ValueError(
            f"视觉模型调用失败（{exc.__class__.__name__}）") from exc
    if not text or not text.strip():
        raise ValueError("视觉模型未返回图片理解结果")
    return f"[图片理解] {filename}：{text}"


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
    def recognize(item: tuple[int, bytes]) -> tuple[int, str]:
        index, page_bytes = item
        label = f"{filename} 第{index}页"
        return index, image_ocr_text(label, page_bytes)

    # 扫描 PDF 原先逐页串行等待视觉模型；最多三路并发，在平台超时预算内完成。
    with ThreadPoolExecutor(max_workers=min(3, len(pages))) as executor:
        recognized = list(executor.map(recognize, enumerate(pages, 1)))
    return "\n".join(text for _, text in sorted(recognized))


def _dashscope_native_transcribe(filename: str, content: bytes) -> str:
    """调用 DashScope 原生 ASR endpoint，供 qwen-audio-* 系列使用。"""
    if not APP_ALLOW_EXTERNAL_MODELS:
        raise ValueError("外部模型已禁用（合规模式仅使用 MiniCPM-o 4.5）")
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
        timeout=MEDIA_MODEL_TIMEOUT,
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


def _asr_transcribe_text(filename: str, content: bytes) -> str:
    """调用专业 ASR 模型（如 DashScope qwen-audio）返回逐字转写文本。"""
    if not APP_ALLOW_EXTERNAL_MODELS:
        raise ValueError("外部模型已禁用（合规模式仅使用 MiniCPM-o 4.5）")
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
                timeout=MEDIA_MODEL_TIMEOUT,
            )
            text = response.choices[0].message.content
        else:
            client = _client(APP_ASR_API_KEY, APP_ASR_BASE_URL)
            response = client.audio.transcriptions.create(
                model=APP_ASR_MODEL,
                file=(filename, io.BytesIO(content), _audio_mime(filename)),
                timeout=MEDIA_MODEL_TIMEOUT,
            )
            text = response.text
    except Exception as exc:
        raise ValueError(
            f"语音模型调用失败（{exc.__class__.__name__}）") from exc
    if not text or not text.strip():
        raise ValueError("语音模型未返回转写文本")
    return text


def audio_transcribe_text(
    filename: str, content: bytes, labeled: bool = True,
) -> str:
    """文件分析链路：优先 MiniCPM-o Realtime，失败回退专业 ASR。

    labeled=True 时带 [音频转写] 前缀（文件分析链路用）。
    """
    if MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL:
        try:
            return _labeled_transcription(
                _realtime_audio_transcribe_text(filename, content),
                filename, labeled,
            )
        except Exception as realtime_exc:
            if not (APP_ASR_API_KEY and APP_ASR_MODEL):
                raise ValueError(
                    f"语音模型调用失败（{type(realtime_exc).__name__}）"
                ) from realtime_exc
    return _labeled_transcription(
        _asr_transcribe_text(filename, content), filename, labeled)


def analyze_audio(filename: str, content: bytes) -> str:
    try:
        return audio_transcribe_text(filename, content)
    except ValueError as exc:
        return (
            f"[音频文件] 文件名：{filename}，大小：{len(content)} 字节。"
            f"语音模型不可用（{exc}），建议人工收听。"
        )
