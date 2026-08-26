"""音频理解统一入口。

本地昇腾 llama-omni-server 能直接听懂音频（边听边答）；
云端 MAP Realtime 的 turn-based chat 对音频表现为"转写并复述"，
因此云端统一改为：先转写 → 再把转写文本交给模型做理解/回答。
两端对调用方保持同一接口，避免各端点重复实现、行为分叉。
"""

from __future__ import annotations

import base64
import logging
import math
import re

from app.config import ASCEND_OMNI_WS_URL
from app.services.realtime_client import (
    RealtimeChatResult,
    RealtimeClient,
    RealtimeError,
)

logger = logging.getLogger(__name__)

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

_AUDIO_CHUNK_SECONDS = 12
_CHUNK_PCM_BYTES = _AUDIO_CHUNK_SECONDS * 16000 * 4

# 本地 A3 一次 whisper 编码上限约 30 秒；应用层按"静音断句 + ≤12 秒"
# 分片后逐片独立会话处理、过滤劣化分片、再分层合并，实测 3.4 分钟会议可稳定整理。
# 上限设 10 分钟，避免异常输入无限处理。
_LOCAL_AUDIO_MAX_SECONDS = 600

# 本地 A3 把"转写/整理指令"当成对话时常见的客套/自介回复，命中即判为不可靠。
_CANNED_REPLY_PATTERNS = (
    "很高兴为你提供帮助",
    "很高兴为您提供帮助",
    # 云端 MiniCPM-o 常见的"自我介绍式开场白"（截图实测复读原文），
    # 与"高兴为你提供帮助"同属模型未执行指令的客套回复。
    "很高兴认识你",
    "有什么我可以帮",
    "请问有什么可以帮您",
    "请问有什么可以帮你",
    "有什么可以帮您",
    "有什么可以帮你",
    "需要我帮忙吗",
    "需要我帮助您吗",
    "需要我帮你吗",
    "请告诉我你具体需要什么",
    "我是由",  # "我是由 xxx 开发的语言模型"
    "我是一款",
    "我是一个人工智能",
    "我是人工智能助手",
)

_EN_CANNED_PATTERNS = (
    "hello!",
    "hi there",
    "hi! it's",
    "it's great to connect",
    "nice to meet you",
    "glad to meet you",
    "glad to help",
    "i'm here to help",
    "i'd be happy to help",
    "how can i assist",
    "how can i help",
    "it seems like you",
    "as an ai",
    "i'm here to",
    "i'd be happy",
    "certainly!",
    "here's a",
    "please let me know",
)

# 云端 MiniCPM-o 文本/对话输出疑似开场白、客套或自我介绍时，
# 重试一次并在系统提示词后追加这条指令，引导模型直接完成任务。
_NO_CANNED_NUDGE = (
    "【注意】请直接输出任务结果或回答用户的问题本身，不要输出问候语、"
    "开场白或自我介绍，不要出现'很高兴认识你''有什么我可以帮你'等客套话。"
)

MEMORY_EXTRACT_INSTRUCTION = (
    "以下是助手与用户的语音对话片段（语音识别结果可能不准确，仅供参考）。\n"
    "助手回应/转写中出现的对用户的称呼（如\"小红\"\"Xiaohong\"）通常就是"
    "用户的名字。请提取用户的名字或称呼（中文或英文均可），"
    "只输出\"用户的名字是：<名字>\"；确实无法确定就输出\"无\"。不要解释。"
)

VOICE_NAME_REFERENCE_NOTE = (
    "\n如果用户问\"我叫什么名字\"\"我的名字\"等，指的是用户自己的名字，"
    "请用\"你的名字是：<名字>\"这种格式直接回答，不要自我介绍。"
)


def _split_pcm_b64(
    audio_b64: str,
    max_seconds: int = _AUDIO_CHUNK_SECONDS,
) -> list[str]:
    """把 float32 16kHz 单声道 PCM(base64) 切成 ≤max_seconds 的块。

    本地 llama-omni-server 的 whisper 编码器对超过约 30 秒的音频会抛
    "Position encoding buffer overflow" 直接崩溃；分片后用独立会话处理，
    从根上绕开越界，避免整个 A3 服务被长音频打挂。

    分片优先按**静音断句**切（实测固定间隔切点会把句子切断，A3 对断句分片
    输出"当然我可以帮你…"等模板回复）；找不到可用静音点（纯静音/持续噪音）
    时退化为固定间隔切分，保证每片不超过 max_seconds。
    """
    try:
        raw = base64.b64decode(audio_b64 or "")
    except Exception:
        return [audio_b64]
    if not raw:
        return [audio_b64]
    return [
        base64.b64encode(chunk).decode("ascii")
        for chunk in _split_pcm_bytes(raw, max_seconds)
    ]


def _silence_cut_points(
    raw: bytes,
    sr: int = 16000,
    win: float = 0.02,
    thresh: float = 0.008,
    min_gap: float = 0.35,
) -> list[int]:
    """检测 PCM 中的静音段，返回可作分片点的采样位置（静音段中点）。"""
    try:
        import numpy as np
    except Exception:
        return []
    n_win = max(1, int(sr * win))
    n = len(raw) // 4
    if n < n_win * 10:
        return []
    x = np.frombuffer(raw, dtype="<f4")
    rms = np.array([
        math.sqrt(float(np.mean(x[i:i + n_win] ** 2)))
        if i + n_win <= n else 0.0
        for i in range(0, n, n_win)
    ])
    silent = rms < thresh
    cuts: list[int] = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            if (j - i) * win >= min_gap:
                cuts.append((i + j) // 2 * n_win)
            i = j
        else:
            i += 1
    return cuts


def _split_pcm_bytes(raw: bytes, max_seconds: int) -> list[bytes]:
    """把 PCM 切成 ≤max_seconds 的块：优先静音断句，必要时固定切分。"""
    max_bytes = max_seconds * 16000 * 4
    if len(raw) <= max_bytes:
        return [raw]
    cuts = _silence_cut_points(raw)
    if not cuts:
        return [
            raw[i:i + max_bytes]
            for i in range(0, len(raw), max_bytes)
        ]
    out: list[bytes] = []
    start = 0
    n = len(raw)
    while n - start > max_bytes:
        # 贪心：取 start 之后、start+max_bytes 之前的最后一个静音切点，
        # 尽量装大块（分片越少，A3 处理越快）；找不到就硬切。
        window_end = start + max_bytes
        best = None
        for cp_sample in cuts:
            cp = cp_sample * 4  # 采样点 → 字节偏移
            if cp <= start:
                continue
            if cp > window_end:
                break
            best = cp
        if best is not None and best - start < 3 * 16000 * 4:
            # 切点太靠近起点会产出碎片，直接硬切
            best = None
        if best is None:
            out.append(raw[start:window_end])
            start = window_end
        else:
            out.append(raw[start:best])
            start = best
    if n - start > 0:
        out.append(raw[start:n])
    return out


def _pcm_duration_seconds(audio_b64: str) -> float:
    """估算 float32 16kHz 单声道 PCM(base64) 的时长（秒）。"""
    try:
        raw = base64.b64decode(audio_b64 or "")
    except Exception:
        return 0.0
    return len(raw) / 4 / 16000


def _ensure_local_audio_within_limit(audio_b64: str) -> None:
    """本地 A3 长音频守卫：超限直接报错，而不是分片后输出乱码。"""
    seconds = _pcm_duration_seconds(audio_b64)
    if seconds > _LOCAL_AUDIO_MAX_SECONDS:
        raise RealtimeError(
            f"本地昇腾后端暂不支持超过 {_LOCAL_AUDIO_MAX_SECONDS} 秒的音频"
            "（处理时间过长）："
            f"请将录音/录像分段（每段 ≤{_AUDIO_CHUNK_SECONDS} 秒）后分别上传，"
            "或改用云端后端（临时注释 .env 中 ASCEND_OMNI_WS_URL）",
            "validation_error",
        )


_GARBAGE_FALLBACK_MSG = (
    "本地昇腾模型未能理解这段音频（输出异常）：已自动重试仍未成功，"
    "请再试一次；多次失败可改用云端后端"
    "（临时注释 .env 中 ASCEND_OMNI_WS_URL）"
)

_MEMORY_NAME_RE = re.compile(r"用户的名字是：([^。\n]+)")


def _memory_name(history_text: str | None) -> str:
    """从历史/记忆中提取已知的"用户的名字"。"""
    match = _MEMORY_NAME_RE.search(history_text or "")
    return match.group(1).strip() if match else ""


def _transcript_claims_other_name(transcript: str, known_name: str) -> bool:
    """转写文本是否声称了一个与既有记忆不同的名字（模型幻觉）。"""
    transcript = transcript or ""
    known_name = known_name.strip()
    if not known_name or not transcript:
        return False
    claims = re.findall(
        r"我(?:叫|的名字是|是)[：: ]*([^\s，。！!？?；;、]{1,12})",
        transcript,
    )
    for claim in claims:
        claim = claim.strip()
        if claim and known_name not in claim and claim not in known_name:
            return True
    # 转写里出现"你叫什么名字"等反问，说明模型把转写指令当对话
    if "你叫什么名字" in transcript or "你的名字是" in transcript:
        return True
    return False


def _flatten_history(history: list[dict] | None) -> str:
    """把多轮历史摊平成一段文本。

    实测本地 A3 会忽略分条 ChatML 历史（"我是小红→记住→我叫什么名字"
    仍答"我是助手"），摊平进单条消息后能正确回忆；同时忽略前端历史里的
    "[语音消息]" 占位符（无内容，摊平只会污染上下文）。
    """
    items: list[str] = []
    for message in history or []:
        role = "用户" if message.get("role") == "user" else "助手"
        content = str(message.get("content") or "").strip()
        if content and content != "[语音消息]":
            if content.startswith("【记忆】"):
                # 记忆条目不作为对话回显，标记为独立记忆，避免模型
                # 把"助手：【记忆】…"当成上一轮对话内容复述出来。
                items.append("记忆：" + content[len("【记忆】"):].strip())
            else:
                items.append(f"{role}：{content}")
    return "\n".join(items)


def _looks_like_canned_reply(text: str) -> bool:
    """判断模型输出是否为"把转写/整理指令当成对话"的客套回复。"""
    text = (text or "").strip()
    if not text:
        return True
    if text.startswith(("你好", "您好")):
        # 对话场景下"你好！有什么问题我可以帮您解答吗？"这类以完整疑问句
        # （"吗/么"结尾）承接的问候是正常回复；只有"你好，请问有什么可以
        # 帮您"这类确认式客套（模型未执行指令）才算异常。
        if not text.rstrip("！!。.？? \t").endswith(("吗", "么")):
            if any(token in text for token in ("帮", "请问", "需要")):
                return True
    lowered = text.lower()
    if lowered.startswith(("hello", "hi there", "i'm ", "i am ",
                           "as an ai", "it seems like you", "here's")):
        return True
    return (
        any(pattern in text for pattern in _CANNED_REPLY_PATTERNS)
        or any(pattern in lowered for pattern in _EN_CANNED_PATTERNS)
    )


def _looks_like_self_intro(text: str) -> bool:
    """判断转写输出是否为"模型自我介绍"而非用户原话。

    本地 A3 对"我叫什么名字"这类语音常回应式自我介绍
    （"我叫通义千问，是…研发的模型。你可以叫我通义"），
    若把它当用户内容会覆盖记忆（实测第二轮答"你的名字是：通义"）。
    """
    text = (text or "").strip()
    if not text:
        return False
    lowered = text.lower()
    markers = (
        "语言模型", "大模型", "ai 助手", "ai助手", "人工智能",
        "研发", "你可以叫我", "你可以称呼我",
        "我是由", "我是一款", "我是一个人工智能",
    )
    return any(marker in text or marker in lowered
               for marker in markers)


def _looks_like_garbage(text: str) -> bool:
    """判断模型输出是否为乱码或退化文本（A3 偶发，长上下文时更常见）。

    判定标准：连续问号串 ≥4 个（A3 乱码形态）或问号占比 >30%；
    或输出退化成"复读机"（ironironiron… / inininin… 等无限重复）。
    注意不能按"问号总数 ≥5"判定——合法的多问题列表（答辩问题、追问）
    本身就有 5+ 个问号，按总数会被误杀成"AI 生成失败"。
    """
    text = (text or "").strip()
    if not text:
        return True
    q = text.count("?") + text.count("？")
    max_run = max(
        (len(m.group(0)) for m in re.finditer(r"[?？]{2,}", text)),
        default=0,
    )
    if max_run >= 4 or q / max(1, len(text)) > 0.3:
        return True
    return _looks_like_repetition(text)


def _looks_like_repetition(text: str) -> bool:
    """检测模型退化成"复读机"的输出（如 ironironiron… / inininin…）。

    三类特征任一命中即判为退化：
    1. 整段由同一短单元无间隔循环（ironironiron / 好的好的好的好的…）；
    2. 去空格后字符多样性极低（整段只有几个字符反复出现）；
    3. 同一词重复 ≥6 次且占全文 60% 以上（iron iron iron …）。
    """
    text = (text or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text.lower())
    if not compact:
        return False
    # 1) 单一短单元整段循环；长度门槛避免误伤短笑声/短叠词
    for unit_len in range(2, min(8, len(compact) // 4 + 1)):
        unit = compact[:unit_len]
        if unit and compact == unit * (len(compact) // unit_len) \
                and len(compact) >= max(16, unit_len * 4):
            return True
    # 2) 字符多样性极低：长文本只有几个字符反复出现
    if len(compact) >= 40:
        unique = len(set(compact))
        if unique <= 6 and unique / len(compact) < 0.15:
            return True
    # 3) 空格分隔的同一词高频重复且占全文过半
    words = re.findall(r"[a-z\u4e00-\u9fff]{2,}", text.lower())
    if words:
        from collections import Counter
        top, count = Counter(words).most_common(1)[0]
        if count >= 6 and count * len(top) >= len(compact) * 0.6:
            return True
    return False


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
    """把音频转成文字（本地与云端统一走该路径时，仅云端用于理解前置）。

    本地长音频先按 12 秒分片、每片独立会话转写（绕开 whisper 越界崩溃），
    再拼接各片清洗后的文本；输出为 "?" 乱码时自动重试，避免把问号给用户。

    本地/云端守卫差异（2026-08-26）：
    - 本地 A3 把"转写指令"当对话回应（"你好，小红！很高兴认识你…"），
      必须用客套/自介判定拦截并重试；
    - 云端 ModelBest 转写正常时带确认语尾巴，靠 _clean_transcript 清洗后
      非空即收；v7.1 把本地客套拦截误套到云端，导致云端语音"无法识别"，
      因此客套判定仅对本地生效，云端只保留纯乱码（"?"）守卫。
    """
    if ASCEND_OMNI_WS_URL:
        _ensure_local_audio_within_limit(audio_b64)
    chunks = (
        _split_pcm_b64(audio_b64) if ASCEND_OMNI_WS_URL else [audio_b64])
    last_raw = ""
    for _attempt in range(3):
        parts: list[str] = []
        for chunk in chunks:
            try:
                result = await RealtimeClient().chat(
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": TRANSCRIBE_INSTRUCTION},
                        {"type": "audio", "data": chunk},
                    ]}],
                    max_new_tokens=1024,
                    omni_mode=True,
                    tts_enabled=False,
                    timeout=timeout,
                )
                last_raw = result.text or ""
            except RealtimeError:
                continue
            cleaned = _clean_transcript(result.text or "")
            if cleaned:
                parts.append(cleaned)
        text = "\n".join(parts)
        if text and not _looks_like_garbage(text):
            if not ASCEND_OMNI_WS_URL or not _looks_like_canned_reply(text):
                return text
        logger.warning(
            "transcribe_audio 重试 backend=%s attempt=%d len=%d "
            "garbage=%s canned=%s raw=%r",
            "local" if ASCEND_OMNI_WS_URL else "map",
            _attempt + 1, len(text),
            _looks_like_garbage(text),
            _looks_like_canned_reply(text),
            last_raw[:120],
        )
    if ASCEND_OMNI_WS_URL:
        raise RealtimeError(_GARBAGE_FALLBACK_MSG, "parse_error")
    raise RealtimeError(
        "云端语音识别失败：模型未能返回有效的用户原话，请重试",
        "parse_error",
    )


async def _merge_text_groups(
    groups: list[list[str]],
    instruction: str,
    client: RealtimeClient,
    max_new_tokens: int,
    timeout: float,
    system_prompt: str | None,
) -> list[str]:
    """把多组分片结果逐组合并，每组不超过 3 段，返回合并后的列表。

    合并提示词不能把原始指令（含"严格按格式输出"）放在开头，否则 A3 会输出
    一串 "?"（指令冲突）；采用"分片结果在前、原始要求作参考在后"的写法。
    """
    merged: list[str] = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
            continue
        prompt = (
            "请把以下同一段语音按时间顺序分片处理的结果合并成一份完整、"
            "连贯的最终输出：\n\n"
            + "\n".join(
                f"[第{i}段] {text}"
                for i, text in enumerate(group, 1))
            + "\n\n【原始任务要求】\n" + instruction
        )
        result = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            max_new_tokens=max_new_tokens,
            omni_mode=False,
            tts_enabled=False,
            timeout=timeout,
        )
        merged.append((result.text or "").strip() or group[0])
    return merged


async def understand_audio(
    audio_b64: str,
    system_prompt: str,
    instruction: str,
    max_new_tokens: int = 1024,
    timeout: float = 180,
    tts_enabled: bool = False,
    history: list[dict] | None = None,
    allow_polite: bool = False,
    prefer_text_answer: bool = False,
):
    """让模型理解一段音频：本地直接听音频，云端先转写再文字理解。

    history 为可选的多轮上下文（[{role, content}, ...]）：本地会拼在音频消息
    之前；云端会拼在转写文本之前，让"边听边答"也能记住前面的对话。
    云端返回的结果会带上转写文本（result.transcript），供调用方存入历史。
    allow_polite=True 时跳过"客套回复"判定（语音对话场景：模型回
    "你好，有什么可以帮您"是正常承接），只保留乱码守卫。
    prefer_text_answer=True 时本地也走"先转写、再纯文本回答"两段式
    （语音对话专用）：本地 A3 直接听音频回答时容易忽略文本历史、
    自我指代混乱（"我叫小红→我叫什么名字"仍答"我叫助手"）；
    先宽松转写（接受模型的回应式文本，其中已含用户关键信息），
    再让文本链路结合历史回答，记忆与回答稳定性显著提升。

    本地路径带"问号守卫"：模型输出 "?" 乱码时自动重试（最多 3 次），
    仍失败则抛友好错误，绝不把问号串返回给界面。
    """
    client = RealtimeClient()
    history = list(history or [])
    if ASCEND_OMNI_WS_URL:
        _ensure_local_audio_within_limit(audio_b64)
        chunks = _split_pcm_b64(audio_b64)
        history_text = _flatten_history(history)
        for _attempt in range(3):
            try:
                if prefer_text_answer:
                    result = await _understand_audio_local_text(
                        client, chunks, history_text, instruction,
                        system_prompt, max_new_tokens, timeout, tts_enabled)
                else:
                    result = await _understand_audio_local(
                        client, chunks, history_text, instruction,
                        system_prompt, max_new_tokens, timeout, tts_enabled)
            except RealtimeError:
                continue
            if not (_looks_like_garbage(result.text)
                    or (_looks_like_canned_reply(result.text)
                        and not allow_polite)):
                return result
        raise RealtimeError(_GARBAGE_FALLBACK_MSG, "parse_error")
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


async def _understand_audio_local_text(
    client: RealtimeClient,
    chunks: list[str],
    history_text: str,
    instruction: str,
    system_prompt: str | None,
    max_new_tokens: int,
    timeout: float,
    tts_enabled: bool,
) -> RealtimeChatResult:
    """本地语音对话专用：先宽松转写，再纯文本回答（记忆稳定）。

    实测本地 A3 直接"听音频回答"时，会把当前音频当唯一输入、忽略文本历史
    （"我叫小红→我叫什么名字"仍答"我叫助手"），且把"转写指令"当成对话
    回应（输出"你好，小红！很高兴认识你"而不是用户原话）。
    因此两段式处理：① 转写阶段宽松接受非乱码输出（回应式文本也保留，
    其中已含用户关键信息）；② 回答阶段走纯文本，把历史放进 system prompt
    （A3 对 system prompt 遵循度高），文本链路记忆与回答稳定。
    """
    parts: list[str] = []
    for chunk in chunks:
        try:
            partial = await client.chat(
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": TRANSCRIBE_INSTRUCTION},
                    {"type": "audio", "data": chunk},
                ]}],
                max_new_tokens=512,
                omni_mode=True,
                tts_enabled=False,
                timeout=timeout,
            )
            cleaned = _clean_transcript(partial.text or "")
            # 宽松接受：非空、非乱码、非"模型自我介绍"即保留
            # （A3 常输出"你好，小红！…"式回应文本，其中包含用户关键信息，
            # 不能像严格转写那样丢弃；但"我叫通义千问…"这类自我介绍是
            # 模型回应而非用户原话，丢弃以免覆盖记忆）。
            if cleaned and not _looks_like_garbage(cleaned) \
                    and not _looks_like_self_intro(cleaned):
                parts.append(cleaned)
        except RealtimeError:
            continue
    transcript = "\n".join(parts)

    # 记忆冲突保护：转写声称的名字与既有记忆矛盾时，视为模型幻觉
    # （A3 对"我叫什么名字"常编造"我叫小明/通义"等），丢弃转写，
    # 让回答阶段以历史记忆为准，避免幻觉覆盖真实记忆。
    known_name = _memory_name(history_text)
    if _transcript_claims_other_name(transcript, known_name):
        transcript = ""

    merged_system = (system_prompt or "").strip()
    if history_text:
        merged_system = (
            merged_system
            + "\n\n【历史对话】\n" + history_text
            + "\n\n请结合上面的历史记忆回答用户当前的问题。"
        ).strip()
    user_text = (
        instruction
        + "\n用户刚才在语音中说："
        + (transcript or "（语音未能识别，请根据上下文推断用户意图）")
        + VOICE_NAME_REFERENCE_NOTE
    )
    result = await client.chat(
        messages=[{"role": "user", "content": user_text}],
        system_prompt=merged_system or None,
        max_new_tokens=max_new_tokens,
        omni_mode=False,
        tts_enabled=tts_enabled,
        timeout=timeout,
    )
    result.transcript = transcript
    result.memory = await _extract_user_memory(
        client, history_text, transcript, result.text, timeout)
    return result


async def _extract_user_memory(
    client: RealtimeClient,
    history_text: str,
    transcript: str,
    reply: str,
    timeout: float,
) -> str:
    """从语音对话中抽取用户个人信息（纯文本，供下一轮注入记忆）。

    本地 A3 的"转写"常输出回应式文本（如"你好，小红！…"），模型回答也
    不稳定；文本链路抽取比依赖逐字转写可靠（实测能从"你好，小红！"稳定
    提取出"用户的名字是：小红"）。
    """
    try:
        # 记忆冲突保护：当前转写若与既有记忆矛盾（模型幻觉），
        # 直接沿用旧记忆，不让幻觉污染前端记忆。
        known_name = _memory_name(history_text)
        if _transcript_claims_other_name(transcript, known_name):
            return f"用户的名字是：{known_name}"
        excerpt = "\n".join(
            part for part in (history_text, transcript, reply) if part)
        if not excerpt:
            return ""
        prompt = (
            MEMORY_EXTRACT_INSTRUCTION
            + "\n\n对话片段：\n" + excerpt[:2000]
        )
        result = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_new_tokens=128,
            omni_mode=False,
            tts_enabled=False,
            timeout=timeout,
        )
        memory = (result.text or "").strip()
        if memory in ("无", "无。", "暂无", "没有", ""):
            return ""
        return memory
    except RealtimeError:
        return ""


async def _understand_audio_local(
    client: RealtimeClient,
    chunks: list[str],
    history_text: str,
    instruction: str,
    system_prompt: str | None,
    max_new_tokens: int,
    timeout: float,
    tts_enabled: bool,
) -> RealtimeChatResult:
    """本地昇腾路径：单段直听 / 长音频分片 + 分层合并。"""
    if len(chunks) == 1:
        context = instruction
        if history_text:
            context += (
                "\n\n【历史对话】\n" + history_text
                + "\n\n【当前】请结合上面的历史与当前语音，直接给出回答。"
            )
        return await client.chat(
            messages=[{"role": "user", "content": [
                {"type": "text", "text": context},
                {"type": "audio", "data": chunks[0]},
            ]}],
            system_prompt=system_prompt,
            max_new_tokens=max_new_tokens,
            omni_mode=True,
            tts_enabled=tts_enabled,
            timeout=timeout,
        )
    # 长音频分片：每片独立会话（避免 whisper 越界崩溃），
    # 再分层合并（每层每组 ≤3 段，避免单次合并提示过长导致乱码）。
    # 分片与中间合并用小 token 预算（只产出简明片段），
    # 最终合并才用调用方预算，避免 50 token/s 下每片生成过长。
    chunk_budget = min(max_new_tokens, 128)
    merge_budget = min(max_new_tokens, 256)
    partials: list[str] = []
    for chunk in chunks:
        try:
            partial = await client.chat(
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": instruction},
                    {"type": "audio", "data": chunk},
                ]}],
                system_prompt=system_prompt,
                max_new_tokens=chunk_budget,
                omni_mode=True,
                tts_enabled=False,
                timeout=timeout,
            )
            if (partial.text or "").strip():
                cleaned_partial = partial.text.strip()
                # 过滤劣化分片：A3 偶发输出 "?"、客套/模板回复，
                # 混进合并链会带崩最终结果（实测 17 片时必现）。
                if ("?" in cleaned_partial[:20]
                        or _looks_like_canned_reply(cleaned_partial)):
                    continue
                partials.append(cleaned_partial)
        except RealtimeError:
            continue
    if not partials:
        raise RealtimeError(
            "音频分片处理未返回有效内容（本地昇腾未听懂该音频，"
            "请重试或改用云端后端）",
            "parse_error",
        )
    texts = partials
    while len(texts) > 1:
        groups = [
            texts[i:i + 3] for i in range(0, len(texts), 3)
        ]
        # 只有最终合并才用完整预算（无历史时）；中间层用小预算避免单次
        # 生成过长。有历史时最终合并由下方带历史的完整预算调用承担。
        final_level = len(groups) == 1
        level_budget = (
            max_new_tokens if (final_level and not history_text)
            else merge_budget
        )
        texts = await _merge_text_groups(
            groups, instruction, client,
            level_budget, timeout, system_prompt)
    merge_prompt = texts[0]
    if history_text:
        merge_prompt += "\n\n【历史对话】\n" + history_text
        return await client.chat(
            messages=[{"role": "user", "content": merge_prompt}],
            system_prompt=system_prompt,
            max_new_tokens=max_new_tokens,
            omni_mode=False,
            tts_enabled=tts_enabled,
            timeout=timeout,
        )
    return RealtimeChatResult(text=merge_prompt)
