"""音频理解统一入口。

本地昇腾 llama-omni-server 能直接听懂音频（边听边答）；
云端 MAP Realtime 的 turn-based chat 对音频表现为"转写并复述"，
因此云端统一改为：先转写 → 再把转写文本交给模型做理解/回答。
两端对调用方保持同一接口，避免各端点重复实现、行为分叉。
"""

from __future__ import annotations

from app.config import ASCEND_OMNI_WS_URL
from app.services.realtime_client import RealtimeClient, RealtimeError

TRANSCRIBE_INSTRUCTION = (
    "这是用户的语音输入。请直接转写用户说出的原话："
    "不要思考、不要解释、不要复述、不要补充，只输出用户说的话本身。"
)


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
    return (result.text or "").strip()


async def understand_audio(
    audio_b64: str,
    system_prompt: str,
    instruction: str,
    max_new_tokens: int = 1024,
    timeout: float = 180,
    tts_enabled: bool = False,
):
    """让模型理解一段音频：本地直接听音频，云端先转写再文字理解。"""
    client = RealtimeClient()
    if ASCEND_OMNI_WS_URL:
        return await client.chat(
            messages=[{"role": "user", "content": [
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
    return await client.chat(
        messages=[{"role": "user", "content": transcript}],
        system_prompt=merged_system,
        max_new_tokens=max_new_tokens,
        omni_mode=False,
        tts_enabled=tts_enabled,
        timeout=timeout,
    )
