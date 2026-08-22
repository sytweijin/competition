"""音频理解统一入口。

本地昇腾 llama-omni-server 能直接听懂音频（边听边答）；
云端 MAP Realtime 的 turn-based chat 对音频表现为"转写并复述"，
因此云端统一改为：先转写 → 再把转写文本交给模型做理解/回答。
两端对调用方保持同一接口，避免各端点重复实现、行为分叉。
"""

from __future__ import annotations

import re

from app.config import ASCEND_OMNI_WS_URL
from app.services.realtime_client import RealtimeClient, RealtimeError

TRANSCRIBE_INSTRUCTION = (
    "这是用户的语音输入。请只转写用户说出的原话，不要添加任何其他内容："
    "不要思考过程，不要解释，不要复述，不要补充，不要确认，不要回答用户，"
    "不要以'用户说''你说'开头，不要把'好的''请问有什么可以帮您''需要我帮忙吗'"
    "之类的客套话写进结果。只输出用户说的话本身。"
)

_ECHO_TAILS = (
    "好的，请问有什么可以帮您",
    "好的，请问有什么可以帮你",
    "请问有什么可以帮您",
    "请问有什么可以帮你",
    "有什么可以帮您",
    "有什么可以帮你",
    "需要我帮忙吗",
    "需要我帮助您吗",
    "需要我帮你吗",
    "好的，我这就帮您",
    "好的，我这就帮你",
    "好的，我来帮您",
    "好的，我来帮你",
    "好的，没问题",
    "没问题，我这就",
    "我这就帮您",
    "我这就帮你",
    "好的，请问",
    "好的，那我",
)


def _clean_transcript(text: str) -> str:
    """去掉云端转写可能附带的模型确认语/客套尾巴，只保留用户原话。"""
    text = (text or "").strip()
    text = re.sub(
        r"^(用户(说|讲到|的原话是)|你说|我听到(你说)?|你的话是)[：:，,]?\s*",
        "", text,
    ).strip()
    changed = True
    while changed and text:
        changed = False
        for tail in sorted(_ECHO_TAILS, key=len, reverse=True):
            idx = text.find(tail)
            if idx > 0:
                text = text[:idx].rstrip("，,。!！?？ \t").strip()
                changed = True
                break
    return text.strip()


async def transcribe_audio(audio_b64: str, timeout: float = 120) -> str:
    """把音频转成文字（本地与云端统一走该路径时，仅云端用于理解前置）。"""
    result = await RealtimeClient().chat(
        messages=[{"role": "user", "content": [
            {"type": "text", "text": TRANSCRIBE_INSTRUCTION},
            {"type": "audio", "data": audio_b64},
        ]}],
        max_new_tokens=1024,
        omni_mode=True,
        tts_enabled=False,
        timeout=timeout,
    )
    return _clean_transcript(result.text or "")


async def understand_audio(
    audio_b64: str,
    system_prompt: str,
    instruction: str,
    max_new_tokens: int = 1024,
    timeout: float = 180,
    tts_enabled: bool = False,
    history: list[dict] | None = None,
):
    """让模型理解一段音频：本地直接听音频，云端先转写再文字理解。

    history 为可选的多轮上下文（[{role, content}, ...]）：本地会拼在音频消息
    之前；云端会拼在转写文本之前，让"边听边答"也能记住前面的对话。
    云端返回的结果会带上转写文本（result.transcript），供调用方存入历史。
    """
    client = RealtimeClient()
    history = list(history or [])
    if ASCEND_OMNI_WS_URL:
        return await client.chat(
            messages=history + [{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "audio", "data": audio_b64},
            ]}],
            system_prompt=system_prompt,
            max_new_tokens=max_new_tokens,
            omni_mode=True,
            tts_enabled=tts_enabled,
            timeout=timeout,
        )
    transcript = await transcribe_audio(audio_b64, timeout)
    if not transcript:
        raise RealtimeError("未能识别语音内容", "parse_error")
    # 云端第二步：转写文本作为用户消息，指令并入系统提示词，
    # 避免"指令+转写"混在用户消息里导致模型复述用户原话。
    merged_system = (
        f"{system_prompt}\n\n{instruction}"
        if system_prompt else instruction
    )
    result = await client.chat(
        messages=history + [{"role": "user", "content": transcript}],
        system_prompt=merged_system,
        max_new_tokens=max_new_tokens,
        omni_mode=False,
        tts_enabled=tts_enabled,
        timeout=timeout,
    )
    result.transcript = transcript
    return result
