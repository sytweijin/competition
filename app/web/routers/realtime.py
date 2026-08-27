"""华为昇腾创新应用赛道：MiniCPM-o Realtime 对话、语音对话与转写路由。"""

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import (
    ASCEND_OMNI_WS_URL,
    MAP_REALTIME_API_KEY,
    MAP_REALTIME_MAX_TOKENS,
    MAP_REALTIME_MODEL,
)
from app.services.realtime_client import RealtimeClient, RealtimeError

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_TRANSCRIBE_SIZE = 15 * 1024 * 1024
MAX_PERFORMANCE_SIZE = 60 * 1024 * 1024

PERFORMANCE_PROMPT = (
    "这是用户答辩练习录像的第{n}帧画面。"
    "请只针对答辩者的神情与表现状态进行分析：是否自信、自然、紧张，"
    "眼神是否专注、表情与姿态是否得体。"
    "不要描述画面里的物品、背景、场景或照片内容，不要复述画面中出现的文字。"
    "用中文给出简短观察（2 句话以内）；如果画面中看不清人脸，请如实说明。"
)

# 本地昇腾 A3 专用：模型常无视提示词直接描述外貌/背景/寒暄（2026-08-27
# 实测"戴眼镜、木质橱柜、生活照、你想分享吗"），用强制结构 + 明确禁词，
# 并在应用层做合规过滤（见 _off_topic_performance_observation）。
PERFORMANCE_PROMPT_LOCAL = (
    "这是用户答辩练习录像的第{n}帧画面，画面中的人是正在回答问题的答辩者。\n"
    "请只评价 TA 的答辩表现状态，严格按以下一行格式输出，不要输出任何其他内容：\n"
    "表现：<自信/紧张/平静/专注/犹豫…>；眼神：<专注/回避/看向别处…>；"
    "表情：<自然/僵硬/微笑…>；姿态：<得体/晃动/僵硬…>；回答状态：<流畅/停顿较多…>\n"
    "【绝对禁止】描述人物外貌（发型、眼镜、衣着、长相），描述背景、家具、房间、"
    "光线、照片本身，或进行寒暄反问（如'你想分享什么吗'）。\n"
    "如果看不清答辩者的脸或画面与答辩无关，只输出：无法识别。"
)

_PERFORMANCE_OFF_TOPIC_MARKERS = (
    "眼镜", "头发", "发型", "马尾", "长发", "短发", "衣着", "衣服", "穿着",
    "背景", "柜子", "橱柜", "家具", "房间", "室内", "光线", "灯光",
    "照片", "生活照", "拍摄", "桌子", "沙发", "床", "墙", "窗户", "窗帘",
    "项链", "耳环", "你好呀", "您好呀", "想分享", "分享吗",
)

_PERFORMANCE_RETRY_HINT = (
    "\n\n【上一轮输出不合格】你描述了人物外貌、背景或进行了寒暄。"
    "请只输出答辩者的表现观察（自信程度、眼神、表情、姿态、回答状态），"
    "不要描述长相、衣着、背景或家具；看不清脸就只输出：无法识别。"
)


def _off_topic_performance_observation(text: str) -> bool:
    """表现观察合规过滤（本地/云端通用）：描述外貌/背景/寒暄视为跑偏，不展示。"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _PERFORMANCE_OFF_TOPIC_MARKERS)


async def _analyze_performance_frame(frame: bytes, index: int) -> str:
    """分析单帧答辩画面；本地昇腾启用严格提示词 + 合规过滤与一次重试。"""
    from app.services.media_analysis import _run_realtime_media_chat

    if ASCEND_OMNI_WS_URL:
        prompt = PERFORMANCE_PROMPT_LOCAL.format(n=index)
    else:
        prompt = PERFORMANCE_PROMPT.format(n=index)

    parts = [
        {"type": "text", "text": prompt},
        {"type": "image", "data": base64.b64encode(frame).decode("utf-8")},
    ]
    obs = (
        await asyncio.to_thread(
            _run_realtime_media_chat, parts, 300, False, 120)
        or ""
    ).strip()
    if obs and _off_topic_performance_observation(obs):
        retry_parts = [
            {"type": "text", "text": prompt + _PERFORMANCE_RETRY_HINT},
            {"type": "image",
             "data": base64.b64encode(frame).decode("utf-8")},
        ]
        obs = (
            await asyncio.to_thread(
                _run_realtime_media_chat, retry_parts, 300, False, 120)
            or ""
        ).strip()
    return obs

INTERVIEW_TURN_INSTRUCTION = (
    "用户正在用语音/视频回答你刚才提出的问题。\n"
    "请严格按以下结构输出，且只输出这四个部分：\n"
    "1) 第一行写【回答摘要】，下一段用不超过两句话概括用户回答的要点；\n"
    "2) 再写【评委回复】，随后以评委身份做两件事：\n"
    "   a. 点评：明确指出用户是否正面回答了问题、是否回避或遗漏了要点、"
    "回答是否全面、依据是否到位；\n"
    "   b. 追问：如果回答不完整、有漏洞或不到位，就同一问题继续追问、"
    "要求补充说明，不要提出新问题；只有回答到位，才提出下一个新问题。\n"
    "不要输出任何占位符、尖括号标签或格式说明。\n"
    "绝对不要重复或复述你上一轮已经给出的点评与追问；"
    "若上一轮追问用户已回答，请换一个新维度继续提问。\n"
    "追问时不要与上一轮使用完全相同的句子：如果用户回答已覆盖要点"
    "（哪怕不完整），不要要求其逐字复述材料内容，直接基于其回答继续"
    "追问依据、逻辑、数据或与方案其他部分的联系，或换一个更具体的角度。"
)

MEETING_PROMPT = (
    "这是团队会议的录音。请整理会议内容：\n"
    "1) 会议要点总结；\n"
    "2) 明确提到的任务（每条一行：任务内容 | 负责人 | 截止时间，"
    "没有负责人或截止时间就写\"无\"）；\n"
    "3) 成员变动、风险或待确认事项。\n"
    "严格按以下格式输出，且只输出这四个部分：\n"
    "【总结】\n会议要点\n【任务】\n- 任务内容 | 负责人 | 截止\n"
    "【风险】\n风险或待确认事项，没有就写\"无\""
)

MEETING_FRAME_PROMPT = (
    "这是团队会议视频的第{n}帧画面。请描述画面中与项目/任务相关的信息：\n"
    "1) 屏幕或白板上展示的内容（PPT、文档、代码、图表、手绘等，尽量提取可见文字）；\n"
    "2) 画面中的人物与动作（谁在讲、在演示什么）；\n"
    "3) 结合会议场景，提炼可能成为任务的事项。\n"
    "只输出内容，不要客套，看不清就如实说明。"
)

MEETING_VISUAL_SYNTHESIS = (
    "以下是团队会议视频各帧画面的理解（没有声音轨道）。"
    "请基于画面内容整理会议要点与可能产生的任务，严格按以下格式输出：\n"
    "【总结】\n会议要点\n【任务】\n- 任务内容 | 负责人 | 截止\n"
    "【风险】\n风险或待确认事项，没有就写\"无\""
)

VOICE_CHAT_SYSTEM_PROMPT = (
    "你是协作分工助手，帮助团队做任务拆解、分工和排期。"
    "用户通过语音与你对话：请直接、自然、简洁地回答用户的问题或承接用户的话。"
    "如果用户问你是谁，介绍自己是协作分工助手，绝不介绍背后的模型、厂商或技术。"
    "不要转写用户的话，不要复述，不要输出思考过程。"
)

VOICE_CHAT_INSTRUCTION = (
    "用户正在用语音与你对话。请直接回答用户的问题或承接用户的话："
    "不要转写，不要复述，不要重复用户的原话，"
    "不要以'你说的是''你的问题是''你提到'等开头，直接给出自然、简洁的回答。"
)

VOICE_REQUIREMENT_INSTRUCTION = (
    "用户用语音补充项目/答辩需求。请理解用户想表达的需求要点，"
    "整理成简洁的中文要点（不超过几行）："
    "不要转写原话，不要复述，不要客套，不要提问，不要输出思考过程，"
    "只输出整理后的需求要点。"
)

class RealtimeChatRequest(BaseModel):
    messages: list[dict] = Field(
        default_factory=list,
        description="多轮对话消息，role 支持 system/user/assistant",
    )
    message: str = Field(
        default="", description="单条用户消息，与 messages 二选一或追加在末尾")
    system_prompt: str | None = Field(
        default=None, description="可选的系统提示词")
    max_new_tokens: int | None = Field(
        default=None, ge=1, le=16384, description="单次生成最大 token 数")
    tts_enabled: bool = Field(
        default=False, description="是否生成语音输出")
    enable_thinking: bool = Field(
        default=False, description="是否启用模型 thinking")


@router.post("/realtime/transcribe")
async def realtime_transcribe(file: UploadFile = File(...)):
    """把麦克风录音转成文字：优先 MiniCPM-o Realtime，未配置时回退 ASR 模型。

    返回纯文本（不带 [音频转写] 前缀），供前端填入输入框后由用户确认再发送。
    """
    from app.services.media_analysis import audio_transcribe_text

    raw = await file.read(MAX_TRANSCRIBE_SIZE + 1)
    if len(raw) > MAX_TRANSCRIBE_SIZE:
        raise HTTPException(status_code=413, detail="录音文件超过 15MB 限制")
    filename = file.filename or "语音输入.webm"
    try:
        text = await asyncio.to_thread(
            audio_transcribe_text, filename, raw, False)
    except ValueError as exc:
        hint = "语音识别暂不可用"
        if ASCEND_OMNI_WS_URL:
            hint += "：本地昇腾后端未连接，请确认 llama-omni-server 已启动"
        raise HTTPException(status_code=502, detail=f"{hint}（{exc}）")
    from app.services.omni_chat import _looks_like_canned_reply
    from app.services.omni_chat import _looks_like_garbage
    # 客套判定仅对本地 A3 生效（v7.1 误套到云端导致云端语音"无法识别"）；
    # 云端保留纯乱码守卫。
    if (ASCEND_OMNI_WS_URL and _looks_like_canned_reply(text)) \
            or _looks_like_garbage(text, local=bool(ASCEND_OMNI_WS_URL)):
        hint = "语音识别暂不可用"
        if ASCEND_OMNI_WS_URL:
            hint += ("：本地昇腾把转写指令当成了对话，未返回用户原话，"
                     "请重试或改用云端后端"
                     "（临时注释 .env 中 ASCEND_OMNI_WS_URL）")
        raise HTTPException(status_code=502, detail=hint)
    if not text.strip():
        raise HTTPException(status_code=502, detail="未能识别到语音内容")
    source = (
        "realtime"
        if (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL)
        else "asr"
    )
    return {"text": text, "source": source}


@router.post("/realtime/dictate")
async def realtime_dictate(file: UploadFile = File(...)):
    """语音整理：把用户口述的回答整理成书面文字（理解后成文）。

    答辩模拟等场景用：MiniCPM-o 直接听懂语音并整理成回答文字，
    不做逐字转写，避免对话模型"作答式转写"污染回答框。
    """
    from app.services.media_analysis import audio_to_written_answer

    raw = await file.read(MAX_TRANSCRIBE_SIZE + 1)
    if len(raw) > MAX_TRANSCRIBE_SIZE:
        raise HTTPException(status_code=413, detail="录音文件超过 15MB 限制")
    try:
        text = await asyncio.to_thread(
            audio_to_written_answer, "answer.webm", raw)
    except ValueError as exc:
        hint = "语音整理暂不可用"
        if ASCEND_OMNI_WS_URL:
            hint += "：本地昇腾后端未连接，请确认 llama-omni-server 已启动"
        raise HTTPException(status_code=502, detail=f"{hint}（{exc}）")
    from app.services.omni_chat import _looks_like_canned_reply
    if ASCEND_OMNI_WS_URL and _looks_like_canned_reply(text):
        hint = "语音整理暂不可用"
        if ASCEND_OMNI_WS_URL:
            hint += ("：本地昇腾未返回整理结果（模型可能把指令当成了对话），"
                     "请重试或改用云端后端"
                     "（临时注释 .env 中 ASCEND_OMNI_WS_URL）")
        raise HTTPException(status_code=502, detail=hint)
    if not text.strip():
        raise HTTPException(status_code=502, detail="未能整理出回答内容")
    return {"text": text}


@router.post("/realtime/voice-chat")
async def realtime_voice_chat(
    file: UploadFile = File(...),
    system_prompt: str = Form(""),
    history: str = Form(""),
    tts_enabled: bool = Form(False),
):
    """直接语音对话：录音作为音频消息发给 MiniCPM-o，返回文本回答与可选语音。

    与"转写进输入框"不同，这里不做语音转文字，而是让模型直接听懂音频并作答，
    是 MiniCPM-o 全模态能力的核心用法。支持携带多轮对话历史（history 为 JSON
    数组），让语音对话也有上下文记忆；返回 transcript 供前端存入历史。
    """
    import base64

    from app.services.media_analysis import _decode_audio_to_pcm16k

    if not (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL):
        raise HTTPException(
            status_code=503,
            detail="MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置",
        )
    raw = await file.read(MAX_TRANSCRIBE_SIZE + 1)
    if len(raw) > MAX_TRANSCRIBE_SIZE:
        raise HTTPException(status_code=413, detail="录音文件超过 15MB 限制")
    try:
        pcm = await asyncio.to_thread(_decode_audio_to_pcm16k, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audio_b64 = base64.b64encode(pcm).decode("utf-8")
    from app.services.omni_chat import understand_audio

    try:
        history_list = json.loads(history) if history.strip() else []
        if not isinstance(history_list, list):
            history_list = []
    except (json.JSONDecodeError, TypeError):
        history_list = []
    history_list = [
        m for m in history_list
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and m.get("content")
    ][-16:]

    tts_failed = False
    try:
        result = await understand_audio(
            audio_b64,
            system_prompt.strip() or VOICE_CHAT_SYSTEM_PROMPT,
            VOICE_CHAT_INSTRUCTION,
            max_new_tokens=MAP_REALTIME_MAX_TOKENS,
            tts_enabled=tts_enabled,
            history=history_list,
            allow_polite=True,
            prefer_text_answer=True,
        )
    except RealtimeError as exc:
        if tts_enabled:
            try:
                result = await understand_audio(
                    audio_b64,
                    system_prompt.strip() or VOICE_CHAT_SYSTEM_PROMPT,
                    VOICE_CHAT_INSTRUCTION,
                    max_new_tokens=MAP_REALTIME_MAX_TOKENS,
                    tts_enabled=False,
                    history=history_list,
                    allow_polite=True,
                    prefer_text_answer=True,
                )
            except RealtimeError as retry_exc:
                raise HTTPException(
                    status_code=502, detail=str(retry_exc)) from retry_exc
            tts_failed = True
        else:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    transcript = result.transcript or ""
    try:
        wav_base64 = (
            result.audio_wav_base64
            if (tts_enabled and not tts_failed) else "")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    backend = "local" if ASCEND_OMNI_WS_URL else "map"
    return {
        "reply": result.text,
        "transcript": transcript,
        "memory": result.memory or "",
        "audio_wav_base64": wav_base64,
        "tts_failed": tts_failed,
        "backend": backend,
    }


@router.post("/realtime/voice-requirement")
async def realtime_voice_requirement(file: UploadFile = File(...)):
    """语音需求输入：本地逐字转写；云端保持"整理需求要点"行为。

    本地 A3 实测会把"整理要点/转写"指令当对话回答（输出"您可以从以下几个
    角度入手…"建议），因此本地复用配置页同款 audio_transcribe_text 逐字转写
    链路（12 秒分片 + 乱码/客套守卫 + 重试）；云端 MiniCPM-o 行为正常
    （返回需求要点），保持原逻辑不变（2026-08-27 按后端隔离）。
    """

    if not (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL):
        raise HTTPException(
            status_code=503,
            detail="MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置",
        )
    raw = await file.read(MAX_TRANSCRIBE_SIZE + 1)
    if len(raw) > MAX_TRANSCRIBE_SIZE:
        raise HTTPException(status_code=413, detail="录音文件超过 15MB 限制")
    filename = file.filename or "语音需求.webm"
    if ASCEND_OMNI_WS_URL:
        from app.services.media_analysis import audio_transcribe_text

        try:
            text = await asyncio.to_thread(
                audio_transcribe_text, filename, raw, False)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "语音转写失败：本地昇腾未返回有效转写文本"
                    "（模型可能把转写指令当成了对话），请重试或改用云端后端"
                    "（临时注释 .env 中 ASCEND_OMNI_WS_URL）"
                    f"（{exc}）"
                ),
            )
        text = (text or "").strip()
        if not text:
            raise HTTPException(status_code=502, detail="未能转写到语音内容")
        return {"text": text}

    # 云端：保持原有"整理需求要点"行为（实测正常）
    from app.services.media_analysis import _decode_audio_to_pcm16k
    from app.services.omni_chat import understand_audio

    try:
        pcm = await asyncio.to_thread(_decode_audio_to_pcm16k, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audio_b64 = base64.b64encode(pcm).decode("utf-8")
    try:
        result = await understand_audio(
            audio_b64,
            "",
            VOICE_REQUIREMENT_INSTRUCTION,
            max_new_tokens=512,
        )
    except RealtimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    text = (result.text or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="未能理解语音需求")
    return {"text": text}


class RealtimeTTSRequest(BaseModel):
    text: str = Field(
        ..., min_length=1, max_length=2000, description="要朗读的文本")


@router.post("/realtime/tts")
async def realtime_tts(req: RealtimeTTSRequest):
    """把指定文本转为语音（朗读），返回可播放 WAV；TTS 失败返回 502。

    用于答辩模拟等场景的 AI 回复播报；本地昇腾 910C TTS 已知不可用，
    前端仅在云端后端启用播报。
    """
    if not (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL):
        raise HTTPException(
            status_code=503,
            detail="MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置",
        )
    if ASCEND_OMNI_WS_URL:
        # 910C 的 TTS 算子已知会挂起单会话服务；朗读接口在本地后端直接拒绝，
        # 避免误调用把 A3 拖挂，前端播报开关也只在云端启用。
        raise HTTPException(
            status_code=501,
            detail="本地昇腾 910C TTS 暂不可用，请切换到云端后端"
            "（临时注释 .env 中 ASCEND_OMNI_WS_URL）",
        )
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")
    client = RealtimeClient()
    try:
        result = await client.chat(
            messages=[{"role": "user", "content": (
                "请朗读以下内容，逐字读出，不要添加任何解释或其他内容：\n"
                + text
            )}],
            system_prompt="你是朗读助手，只把给定内容读出来。",
            max_new_tokens=1024,
            tts_enabled=True,
            omni_mode=False,
        )
    except RealtimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        wav = result.audio_wav_base64
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not wav:
        raise HTTPException(status_code=502, detail="TTS 未返回音频")
    return {"audio_wav_base64": wav}


def _reply_similar(left: str, right: str) -> float:
    """计算两段评委回复的相似度，用于检测"复读上一轮"。"""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left or "", right or "").ratio()


def _normalize_literal_newlines(text: str) -> str:
    """把模型偶发输出的字面转义序列（"\\n"）还原为真实换行。

    MiniCPM-o 偶发把结构分隔写成字面 "\\n\\n"（截图/日志实测），
    前端 pre-wrap 会原样显示成反斜杠 n，必须先归一化再解析与展示。
    """
    return (
        (text or "")
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
    )


def _parse_turn_text(text: str) -> tuple[str, str]:
    """解析答辩回合输出：返回 (回答摘要, 评委回复)。

    标准格式：【回答摘要】…【评委回复】a. 点评… b. 追问…
    MiniCPM-o 偶发简写：【评】点评…【追】追问…——注意【评】是"点评"、
    属于评委回复的一部分，不是用户回答摘要；摘要缺失时返回空串，
    由调用方用云端转写文本兜底（避免把评委点评误存成用户回答）。
    """
    text = (text or "").strip()
    text = _normalize_literal_newlines(text)
    shorthand_markers = ("【评】", "【点评】", "【追】", "【追问】")
    placeholder_lines = {
        "<摘要>", "<点评与下一个问题>", "<摘要内容>",
        "点评与下一个问题", "<点评>",
    }

    def clean(part: str) -> str:
        lines = [
            line for line in part.splitlines()
            if line.strip()
            and line.strip() not in placeholder_lines
            and "<点评与下一个问题>" not in line
            and "<摘要>" not in line
        ]
        return "\n".join(lines).strip()

    def strip_shorthand(part: str) -> str:
        """把简写标记还原为换行（点评/追问同属评委回复）。"""
        for marker in shorthand_markers:
            part = part.replace(marker, "\n")
        return clean(part)

    if "【回答摘要】" in text and "【评委回复】" in text:
        _, rest = text.split("【回答摘要】", 1)
        summary, reply = rest.split("【评委回复】", 1)
        return clean(summary), strip_shorthand(reply)
    if "【评委回复】" in text:
        _, reply = text.split("【评委回复】", 1)
        return "", strip_shorthand(reply)
    # 摘要用全称、回复用简写（如 【回答摘要】…【评】…【追】…）
    sum_pos = text.find("【回答摘要】")
    if sum_pos >= 0:
        rest = text[sum_pos + len("【回答摘要】"):]
        rep_pos = min(
            (rest.find(m) for m in shorthand_markers if m in rest),
            default=-1,
        )
        if rep_pos >= 0:
            summary = clean(rest[:rep_pos])
            reply = strip_shorthand(rest[rep_pos:])
            return summary, reply
    # 只有简写标记：整段都是评委回复（点评 + 追问），摘要为空
    if any(m in text for m in shorthand_markers):
        return "", strip_shorthand(text)
    return "", clean(text)


def _parse_meeting_text(text: str) -> tuple[str, list[dict], str]:
    """解析会议整理输出：返回 (会议要点, 任务列表, 风险)。"""
    text = _normalize_literal_newlines(text).strip()
    placeholder_lines = {
        "<会议要点>", "<任务>", "<风险>", "会议要点", "任务内容 | 负责人 | 截止",
        "风险或待确认事项，没有就写\"无\"",
        "风险或待确认事项，没有则写\"无\"",
    }

    def clean(part: str) -> str:
        kept: list[str] = []
        for line in part.splitlines():
            s = line.strip()
            if s.startswith("【") and kept:
                # 模型偶尔会多输出【其他】等额外段落，遇到新段落即截断
                break
            if s not in placeholder_lines:
                kept.append(line)
        return "\n".join(kept).strip()

    tasks: list[dict] = []
    summary = ""
    risks = ""
    if "【总结】" in text:
        _, rest = text.split("【总结】", 1)
        if "【任务】" in rest:
            summary, rest = rest.split("【任务】", 1)
        else:
            summary = rest
            rest = ""
        summary = clean(summary)
        if "【风险】" in rest:
            task_block, risks = rest.split("【风险】", 1)
            risks = clean(risks)
        else:
            task_block = rest
        for line in task_block.splitlines():
            line = line.strip().lstrip("-• ")
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            tasks.append({
                "name": parts[0] if parts else line,
                "owner": parts[1] if len(parts) > 1 and parts[1] != "无" else "",
                "deadline": parts[2] if len(parts) > 2 and parts[2] != "无" else "",
            })
    return summary, tasks, risks


@router.post("/realtime/interview-turn")
async def realtime_interview_turn(
    file: UploadFile = File(...),
    system_prompt: str = Form(""),
    history: str = Form(""),
):
    """答辩评委直接语音/视频对话：评委听（看）用户回答后点评并追问。

    不做"整理成书面回答"：音频（视频画面）直接作为多模态输入交给
    MiniCPM-o，评委听完/看完当场回复，返回 回答摘要（用于多轮记忆）与 评委回复。
    history 为可选的完整对话历史（JSON 数组），让语音/视频轮次与文字轮次
    一样拥有全程记忆。
    """
    from app.services.media_analysis import (
        _run_realtime_media_chat,
        extract_audio_pcm16k,
        extract_video_frames,
    )
    from app.services.realtime_client import RealtimeClient, RealtimeError

    raw = await file.read(MAX_PERFORMANCE_SIZE + 1)
    if len(raw) > MAX_PERFORMANCE_SIZE:
        raise HTTPException(status_code=413, detail="录像文件超过 60MB 限制")
    try:
        frames = await asyncio.to_thread(extract_video_frames, raw, 3)
    except Exception:
        frames = []
    audio = await asyncio.to_thread(extract_audio_pcm16k, raw)
    if not audio and not frames:
        raise HTTPException(status_code=400, detail="未从录音/录像中提取到音频或画面")

    judge_sys = (
        (system_prompt.strip() or "你是答辩模拟的评委。")
        + "\n\n" + INTERVIEW_TURN_INSTRUCTION
    )
    try:
        history_list = json.loads(history) if history.strip() else []
        if not isinstance(history_list, list):
            history_list = []
    except (json.JSONDecodeError, TypeError):
        history_list = []
    history_list = [
        m for m in history_list
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and m.get("content")
    ][-16:]
    reply = ""
    summary = ""
    audio_hollow = False
    audio_error = ""
    last_reply = ""
    if history_list and history_list[-1].get("role") == "assistant":
        last_reply = str(history_list[-1].get("content") or "")
    if audio:
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        from app.services.omni_chat import (
            _AUDIO_CHUNK_SECONDS, _looks_like_garbage,
            _trim_repetition_tail, understand_audio)

        try:
            # 评委输出守卫（两端通用）：
            # - 乱码/复读退化（"ösösös…/MAIL MAIL…/ironiron…"）：云端与本地
            #   都可能出现，命中即带防乱码+防复读指令重试一次，仍失败判空转
            #   （交给下方 502，绝不把乱码当评委回复展示）；
            # - 相似度复读（本地 A3 被上一轮回复锚定逐字重复）：仅本地判定。
            #   云端仅在"已拿到真实回答内容（摘要非空）却仍复读同一追问"
            #   时才判定复读——没听清（摘要为空）时重复追问是合法行为。
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                instruction = (
                    "用户正在回答你提出的问题，请听用户的语音并点评追问。"
                    if attempt == 1 else
                    "用户正在回答你提出的问题。注意：你第一次生成的点评与"
                    "追问是乱码或与历史回复完全相同，这是输出错误。请基于"
                    "用户本次的新回答重新生成有效的点评与追问；绝对不要输出"
                    "重复的无意义字符，绝对不要重复历史中已有的任何点评或"
                    "追问；若上一轮追问用户已回答，请换一个新维度继续提问。"
                )
                result = await understand_audio(
                    audio_b64,
                    judge_sys,
                    instruction,
                    max_new_tokens=MAP_REALTIME_MAX_TOKENS,
                    history=history_list,
                )
                summary, reply = _parse_turn_text(result.text)
                # 本地 A3 偶发"回复前半段正常、后半段无限复读"（实测"妖精"循环）：
                # 先截掉复读尾巴再走乱码/复读守卫，保留有效前缀而不是整轮丢弃。
                # 仅本地启用，云端保持原逻辑（2026-08-27 按后端隔离）。
                if ASCEND_OMNI_WS_URL:
                    summary = _trim_repetition_tail(summary)
                    reply = _trim_repetition_tail(reply)
                # 模型漏输出【回答摘要】时，用云端转写文本兜底，避免
                # 评委点评被误存成用户回答、历史失真导致复读循环。
                if not summary.strip() and (result.transcript or "").strip():
                    summary = (result.transcript or "").strip()[:300]
                bad_similar = (
                    ASCEND_OMNI_WS_URL and last_reply and reply
                    and len(reply) >= 30
                    and _reply_similar(reply, last_reply) >= 0.85)
                bad_repeat = (
                    not ASCEND_OMNI_WS_URL and summary.strip() and reply
                    and len(reply) >= 20 and last_reply
                    and _reply_similar(reply, last_reply) >= 0.9)
                bad_garbage = _looks_like_garbage(
                    summary + " " + reply,
                    local=bool(ASCEND_OMNI_WS_URL))
                if attempt == 1 and (bad_garbage or bad_similar or bad_repeat):
                    summary = ""
                    reply = ""
                    continue
                break
            if _looks_like_garbage(
                    summary + " " + reply,
                    local=bool(ASCEND_OMNI_WS_URL)):
                audio_hollow = True
                audio_error = "评委输出异常（乱码/重复内容），请重试"
                summary = ""
                reply = ""
                logger.warning(
                    "interview-turn 评委输出乱码：%r",
                    (result.text or "")[:120],
                )
            elif (not ASCEND_OMNI_WS_URL and summary.strip() and reply
                    and len(reply) >= 20 and last_reply
                    and _reply_similar(reply, last_reply) >= 0.9):
                audio_hollow = True
                audio_error = (
                    "评委连续两轮输出相同追问（未基于新回答推进），请重试")
                summary = ""
                reply = ""
                logger.warning(
                    "interview-turn 云端评委复读：len=%d last_len=%d "
                    "similarity=%.3f",
                    len(reply), len(last_reply),
                    _reply_similar(reply, last_reply),
                )
            elif (ASCEND_OMNI_WS_URL and last_reply and reply
                    and len(reply) >= 30
                    and _reply_similar(reply, last_reply) >= 0.85):
                audio_hollow = True
                audio_error = "评委连续两轮输出相同回复（模型复读），请重试"
                summary = ""
                reply = ""
                logger.warning(
                    "interview-turn 本地评委复读：len=%d last_len=%d "
                    "similarity=%.3f",
                    len(reply), len(last_reply),
                    _reply_similar(reply, last_reply),
                )
            if ASCEND_OMNI_WS_URL:
                # 本地 A3 常把音频当对话而非听懂内容：输出"用户尚未提供
                # 回答内容"等空转文本。命中即标记，等待画面观察是否兜底。
                # 云端不判空转——"没有听到/无法识别"是评委的正常回应，
                # 应当展示给用户而不是整轮报错。
                hollow_markers = (
                    "尚未提供", "未提供", "没有听到", "未听到",
                    "无法判断", "无法识别", "没有收到", "没有捕获",
                )
                if not (summary or reply) or any(
                        marker in (summary + " " + reply)
                        for marker in hollow_markers):
                    audio_hollow = True
        except RealtimeError as exc:
            if (getattr(exc, "error_type", "") == "validation_error"
                    and "超过" in str(exc)):
                raise HTTPException(
                    status_code=502,
                    detail="答辩录音/录像的音频超过本地昇腾单次处理上限"
                    f"（约 {_AUDIO_CHUNK_SECONDS} 秒）：请缩短回答，"
                    "或改用云端后端（临时注释 .env 中 ASCEND_OMNI_WS_URL）",
                ) from exc
            # 音频理解失败但录像有画面：标记空转，交给帧观察兜底，
            # 避免"能看画面却整轮报错"。
            audio_hollow = True
            audio_error = str(exc)

    observations: list[str] = []
    for index, frame in enumerate(frames, 1):
        try:
            obs = await _analyze_performance_frame(frame, index)
            if obs and obs.strip() and not _looks_like_garbage(
                    obs, local=bool(ASCEND_OMNI_WS_URL)) \
                    and not _off_topic_performance_observation(obs):
                observations.append(f"第 {index} 帧：{obs.strip()}")
        except Exception:
            continue
    if observations:
        reply = (
            (reply + "\n\n📹 表现观察：\n" + "\n".join(observations))
            .strip()
        )

    if audio_hollow and not observations:
        hint = audio_error or (
            "评委未能听懂本次语音/视频回答"
            "（本地昇腾可能把音频当成了对话，或未捕获到内容）")
        if ASCEND_OMNI_WS_URL:
            hint += "：请重试，或改用云端后端（临时注释 .env 中 ASCEND_OMNI_WS_URL）"
        raise HTTPException(status_code=502, detail=hint)

    return {"reply": reply, "summary": summary}


def _sse_headers() -> dict:
    """SSE 响应头：禁用代理缓冲，避免增量被攒住一次性下发。"""
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _meeting_analysis(raw: bytes, emit=None) -> dict:
    """会议分析核心逻辑（供普通 JSON 与 SSE 流式两个端点共用）。

    emit 为可选的进度回调（async callable: emit(message)），
    流式端点用它逐阶段上报进度，普通端点不传即无感知。
    """
    from app.services.media_analysis import (
        _run_realtime_media_chat,
        extract_audio_pcm16k,
        extract_video_frames,
    )
    from app.services.realtime_client import RealtimeClient, RealtimeError

    async def _emit(message: str):
        if emit is not None:
            await emit(message)

    await _emit("正在抽取视频画面…")
    try:
        frames = await asyncio.to_thread(extract_video_frames, raw, 3)
    except Exception:
        frames = []
    await _emit("正在提取音频…")
    audio = await asyncio.to_thread(extract_audio_pcm16k, raw)
    if not audio and not frames:
        raise HTTPException(
            status_code=400,
            detail="未从录音/录像中提取到音频或画面",
        )

    summary = ""
    tasks: list[dict] = []
    risks = ""
    text = ""
    audio_error = ""
    if audio:
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        from app.services.omni_chat import understand_audio

        await _emit("正在听会议音频…")
        try:
            result = await understand_audio(
                audio_b64,
                "",
                MEETING_PROMPT,
                max_new_tokens=MAP_REALTIME_MAX_TOKENS,
            )
            text = (result.text or "").strip()
            summary, tasks, risks = _parse_meeting_text(text)
        except RealtimeError as exc:
            # 音频理解失败但有画面：保留错误信息，尝试仅凭画面整理；
            # 纯录音则直接报错，避免静默返回"未能整理出要点"。
            audio_error = str(exc)

    visual: list[str] = []
    from app.services.omni_chat import _looks_like_garbage
    if frames:
        await _emit(f"正在理解 {len(frames)} 帧画面…")
    for index, frame in enumerate(frames, 1):
        parts = [
            {"type": "text",
             "text": MEETING_FRAME_PROMPT.format(n=index)},
            {"type": "image",
             "data": base64.b64encode(frame).decode("utf-8")},
        ]
        try:
            obs = await asyncio.to_thread(
                _run_realtime_media_chat, parts, 500, False, 120)
            if obs and obs.strip() and not _looks_like_garbage(
                    obs, local=bool(ASCEND_OMNI_WS_URL)):
                visual.append(f"第 {index} 帧：{obs.strip()}")
        except Exception:
            continue

    if audio_error and not visual:
        raise HTTPException(status_code=502, detail=audio_error)

    if visual and (not audio or audio_error):
        # 无声轨录屏 / 音频理解失败：把画面理解交给模型再整理成结构化会议结果
        await _emit("正在整理会议纪要…")
        try:
            result = await RealtimeClient().chat(
                messages=[{"role": "user", "content": (
                    MEETING_VISUAL_SYNTHESIS
                    + "\n\n画面理解：\n" + "\n".join(visual)
                )}],
                max_new_tokens=MAP_REALTIME_MAX_TOKENS,
            )
            text = (result.text or "").strip()
            summary, tasks, risks = _parse_meeting_text(text)
        except RealtimeError:
            pass

    return {
        "summary": summary or text[:1000] or "（未能整理出要点）",
        "tasks": tasks,
        "risks": risks or "",
        "visual": "\n".join(visual),
        "has_video": bool(frames),
        "raw": text[:2000],
    }


@router.post("/realtime/meeting")
async def realtime_meeting(file: UploadFile = File(...)):
    """会议旁听：听会议录音/看会议录像，整理要点、任务（负责人/截止）与风险。

    录像链路与答辩一致：抽帧看画面 + 抽音频听内容，边看边听整理会议；
    纯录音走音频理解；无声轨的录屏视频仅凭画面理解整理。
    """
    raw = await file.read(MAX_PERFORMANCE_SIZE + 1)
    if len(raw) > MAX_PERFORMANCE_SIZE:
        raise HTTPException(status_code=413, detail="录音/录像文件超过 60MB 限制")
    return await _meeting_analysis(raw)


@router.post("/realtime/meeting/stream")
async def realtime_meeting_stream(file: UploadFile = File(...)):
    """会议旁听流式版：逐阶段上报进度，末尾返回 done（结构化会议结果）。

    与普通端点共享同一套分析逻辑，只是把"正在抽帧/正在听音频/正在看画面"
    等进度实时推给前端，1-3 分钟的处理不再让用户干等。
    """
    raw = await file.read(MAX_PERFORMANCE_SIZE + 1)
    if len(raw) > MAX_PERFORMANCE_SIZE:
        raise HTTPException(status_code=413, detail="录音/录像文件超过 60MB 限制")

    async def event_gen():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(message: str):
            await queue.put({"type": "progress", "message": message})

        async def run():
            try:
                data = await _meeting_analysis(raw, emit=emit)
                await queue.put({"type": "done", "data": data})
            except HTTPException as exc:
                await queue.put({"type": "error", "detail": str(exc.detail)})
            except Exception:
                logger.exception("会议流式分析失败")
                await queue.put({"type": "error", "detail": "会议分析失败，请重试"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(item)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=_sse_headers())


@router.post("/realtime/performance")
async def realtime_performance(file: UploadFile = File(...)):
    """答辩录像分析：抽帧让 MiniCPM-o 看表情 + 抽音频转写回答。

    一条链路同时使用视觉（表情）与听觉（回答内容），返回表现点评与回答文本。
    """
    from app.services.media_analysis import (
        _run_realtime_media_chat,
        audio_to_written_answer,
        extract_audio_pcm16k,
        extract_video_frames,
    )
    from app.services.omni_chat import (
        _AUDIO_CHUNK_SECONDS, _looks_like_garbage)

    raw = await file.read(MAX_PERFORMANCE_SIZE + 1)
    if len(raw) > MAX_PERFORMANCE_SIZE:
        raise HTTPException(status_code=413, detail="录像文件超过 60MB 限制")
    warning = ""
    try:
        frames = await asyncio.to_thread(
            extract_video_frames, raw, 4)
    except Exception:
        frames = []
        warning = "未能解析录像画面，请确认画面完整后重试"
    audio = await asyncio.to_thread(extract_audio_pcm16k, raw)

    observations: list[str] = []
    for index, frame in enumerate(frames, 1):
        try:
            obs = await _analyze_performance_frame(frame, index)
            if obs and obs.strip() and not _looks_like_garbage(
                    obs, local=bool(ASCEND_OMNI_WS_URL)) \
                    and not _off_topic_performance_observation(obs):
                observations.append(
                    f"第 {index} 帧：{obs.strip()}")
        except Exception as exc:
            warning = warning or f"表情分析部分失败：{type(exc).__name__}"
    if observations:
        analysis = "\n".join(observations)
    else:
        analysis = ""

    answer = ""
    if audio:
        try:
            # 传入原始文件字节（含音轨的视频/纯音频），内部统一解码；
            # 不能传已解码 PCM（会被当作媒体文件二次解码而必然失败）。
            answer = await asyncio.to_thread(
                audio_to_written_answer, "answer.webm", raw)
        except Exception as exc:
            answer = ""
            msg = str(exc)
            if "超过" in msg and ASCEND_OMNI_WS_URL:
                warning = warning or (
                    "答辩录像音频超过本地昇腾单次处理上限"
                    f"（约 {_AUDIO_CHUNK_SECONDS} 秒），回答转写已跳过："
                    "请缩短回答或改用云端后端"
                    "（临时注释 .env 中 ASCEND_OMNI_WS_URL）")
            else:
                warning = warning or f"回答转写失败：{type(exc).__name__}"
    if not answer and not analysis:
        warning = warning or "未能提取到回答与画面，请重试或缩短录制时长"

    return {
        "analysis": (analysis or "").strip(),
        "answer": (answer or "").strip(),
        "frames": len(frames),
        "warning": warning,
    }


@router.get("/realtime/status")
def realtime_status():
    """返回 MiniCPM-o Realtime 配置状态，供 Demo 前端快速判断可用性。"""
    backend = "local" if ASCEND_OMNI_WS_URL else "map"
    return {
        "enabled": bool(MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL),
        "model": "llama.cpp-omni" if ASCEND_OMNI_WS_URL else MAP_REALTIME_MODEL,
        "mode": "chat",
        "backend": backend,
    }


@router.post("/realtime/chat")
async def realtime_chat(req: RealtimeChatRequest):
    """调用 MiniCPM-o Chat 模式（云端 Realtime 或本地 llama-omni-server）。"""
    if not (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL):
        raise HTTPException(
            status_code=503,
            detail="MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置",
        )
    messages = list(req.messages)
    if req.message.strip():
        messages.append({"role": "user", "content": req.message})
    if not messages:
        raise HTTPException(
            status_code=400,
            detail="messages 或 message 至少需要一项",
        )

    bare_last_content = None
    if ASCEND_OMNI_WS_URL and len(messages) > 1:
        # 本地 A3 会忽略分条多轮消息（实测"我是小红→我叫什么名字"仍答
        # "我是助手"）；把前文摊平成单条上下文，和语音记忆同一套兼容写法。
        from app.services.omni_chat import _flatten_history

        *prev, last = messages
        history_text = _flatten_history([
            m for m in prev
            if m.get("role") in ("user", "assistant")
        ])
        content = last.get("content")
        if history_text and isinstance(content, str):
            bare_last_content = content
            last = dict(last, content=(
                f"【对话上下文】\n{history_text}\n\n"
                f"【当前问题】\n{content}"
            ))
            messages = [last]

    client = RealtimeClient()
    tts_failed = False
    try:
        result = await client.chat(
            messages=messages,
            system_prompt=req.system_prompt,
            max_new_tokens=req.max_new_tokens or MAP_REALTIME_MAX_TOKENS,
            tts_enabled=req.tts_enabled,
            enable_thinking=req.enable_thinking,
        )
    except RealtimeError as exc:
        if req.tts_enabled:
            # 部分后端（如 910C 的 TTS 算子未就绪）会在 TTS 输出时直接关闭
            # 会话；降级为纯文本重试一次，保证对话不中断。
            try:
                result = await client.chat(
                    messages=messages,
                    system_prompt=req.system_prompt,
                    max_new_tokens=(
                        req.max_new_tokens or MAP_REALTIME_MAX_TOKENS),
                    tts_enabled=False,
                    enable_thinking=req.enable_thinking,
                )
            except RealtimeError as retry_exc:
                raise HTTPException(
                    status_code=502, detail=str(retry_exc)) from retry_exc
            tts_failed = True
        else:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 本地 A3 偶发输出全 "?" 乱码（长上下文时更常见）：去掉摊平的长上下文，
    # 用裸问句重试一次，提升抽屉对话的演示稳定性。
    # 本地文字对话不拦"客套回复"（"你好，有什么可以帮您"可能是正常承接），
    # 只对乱码报错；云端命中开场白/乱码时走下方带防客套指令的重试。
    from app.services.omni_chat import (
        _NO_CANNED_NUDGE, _looks_like_canned_reply, _looks_like_garbage)
    if (ASCEND_OMNI_WS_URL and _looks_like_garbage(
            result.text, local=True)
            and bare_last_content is not None):
        try:
            result = await client.chat(
                messages=[{"role": "user", "content": bare_last_content}],
                system_prompt=req.system_prompt,
                max_new_tokens=req.max_new_tokens or MAP_REALTIME_MAX_TOKENS,
                tts_enabled=False,
                enable_thinking=req.enable_thinking,
            )
        except RealtimeError:
            pass
    if ASCEND_OMNI_WS_URL and _looks_like_garbage(
            result.text, local=True):
        raise HTTPException(
            status_code=502,
            detail="本地昇腾模型输出异常（未能理解该问题），请重试或改用云端后端",
        )

    # 云端 MiniCPM-o 偶发输出开场白/自介式客套（如"你好，很高兴认识你。
    # 有什么我可以帮你的吗？"）或乱码：带防客套指令重试一次。重试后仍为
    # 纯乱码才报错；仍是客套则直接返回（比 502 丢整轮更可接受，前端还有
    # 兜底链路可继续追问）。
    if (not ASCEND_OMNI_WS_URL
            and (_looks_like_garbage(result.text)
                 or _looks_like_canned_reply(result.text))):
        logger.warning(
            "realtime/chat 云端输出异常（乱码/客套），带防客套指令重试：%r",
            (result.text or "")[:120],
        )
        retry_system = (
            (req.system_prompt or "") + "\n\n" + _NO_CANNED_NUDGE).strip()
        try:
            result = await client.chat(
                messages=messages,
                system_prompt=retry_system,
                max_new_tokens=req.max_new_tokens or MAP_REALTIME_MAX_TOKENS,
                tts_enabled=False,
                enable_thinking=req.enable_thinking,
            )
        except RealtimeError as retry_exc:
            raise HTTPException(
                status_code=502, detail=str(retry_exc)) from retry_exc
        if _looks_like_garbage(result.text):
            raise HTTPException(
                status_code=502,
                detail="云端 MiniCPM-o 输出异常（未能理解该问题），请重试",
            )

    model = "llama.cpp-omni" if ASCEND_OMNI_WS_URL else MAP_REALTIME_MODEL
    backend = "local" if ASCEND_OMNI_WS_URL else "map"
    try:
        wav_base64 = (
            result.audio_wav_base64 if req.tts_enabled else "")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "reply": result.text,
        "model": model,
        "mode": "chat",
        "backend": backend,
        "session_id": result.session_id,
        "audio_base64": result.audio_base64 if req.tts_enabled else "",
        "audio_wav_base64": wav_base64,
        "tts_failed": tts_failed,
    }


@router.post("/realtime/chat/stream")
async def realtime_chat_stream(req: RealtimeChatRequest):
    """SSE 流式对话：文本增量实时推送，末尾返回 done（含 TTS 音频）。

    与 /realtime/chat 语义一致（本地摊平历史、乱码重试、TTS 降级），
    只是把模型输出按增量推给前端，消除"攒完才显示"的等待感。
    """
    if not (MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL):
        raise HTTPException(
            status_code=503,
            detail="MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置",
        )
    messages = list(req.messages)
    if req.message.strip():
        messages.append({"role": "user", "content": req.message})
    if not messages:
        raise HTTPException(
            status_code=400,
            detail="messages 或 message 至少需要一项",
        )

    bare_last_content = None
    if ASCEND_OMNI_WS_URL and len(messages) > 1:
        from app.services.omni_chat import _flatten_history

        *prev, last = messages
        history_text = _flatten_history([
            m for m in prev
            if m.get("role") in ("user", "assistant")
        ])
        content = last.get("content")
        if history_text and isinstance(content, str):
            bare_last_content = content
            last = dict(last, content=(
                f"【对话上下文】\n{history_text}\n\n"
                f"【当前问题】\n{content}"
            ))
            messages = [last]

    async def event_gen():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_delta(chunk: str):
            await queue.put({"type": "delta", "delta": chunk})

        async def run():
            from app.services.omni_chat import (
                _NO_CANNED_NUDGE, _looks_like_canned_reply,
                _looks_like_garbage)

            client = RealtimeClient()
            tts_failed = False
            try:
                result = await client.chat(
                    messages=messages,
                    system_prompt=req.system_prompt,
                    max_new_tokens=(
                        req.max_new_tokens or MAP_REALTIME_MAX_TOKENS),
                    tts_enabled=req.tts_enabled,
                    enable_thinking=req.enable_thinking,
                    on_text_delta=on_delta,
                )
            except RealtimeError as exc:
                if req.tts_enabled:
                    try:
                        await queue.put({
                            "type": "info",
                            "message": "语音生成暂不可用，已返回文字回答",
                        })
                        result = await client.chat(
                            messages=messages,
                            system_prompt=req.system_prompt,
                            max_new_tokens=(
                                req.max_new_tokens or MAP_REALTIME_MAX_TOKENS),
                            tts_enabled=False,
                            enable_thinking=req.enable_thinking,
                            on_text_delta=on_delta,
                        )
                        tts_failed = True
                    except RealtimeError as retry_exc:
                        await queue.put({
                            "type": "error", "detail": str(retry_exc),
                        })
                        return
                else:
                    await queue.put({"type": "error", "detail": str(exc)})
                    return

            # 本地 A3 偶发输出全 "?"：去摊平长上下文、用裸问句重试一次
            if (ASCEND_OMNI_WS_URL and _looks_like_garbage(
                    result.text, local=True)
                    and bare_last_content is not None):
                await queue.put({"type": "reset"})
                try:
                    result = await client.chat(
                        messages=[{"role": "user", "content": bare_last_content}],
                        system_prompt=req.system_prompt,
                        max_new_tokens=(
                            req.max_new_tokens or MAP_REALTIME_MAX_TOKENS),
                        tts_enabled=False,
                        enable_thinking=req.enable_thinking,
                        on_text_delta=on_delta,
                    )
                except RealtimeError:
                    pass
            if ASCEND_OMNI_WS_URL and _looks_like_garbage(
                    result.text, local=True):
                await queue.put({
                    "type": "error",
                    "detail": (
                        "本地昇腾模型输出异常（未能理解该问题），"
                        "请重试或改用云端后端"
                    ),
                })
                return

            # 云端 MiniCPM-o 偶发输出开场白/自介式客套或乱码：带防客套指令
            # 重试一次（前端收到 reset 清空已展示文本）；重试后仍为纯乱码
            # 才报错，客套内容直接返回。
            if (not ASCEND_OMNI_WS_URL
                    and (_looks_like_garbage(result.text)
                         or _looks_like_canned_reply(result.text))):
                await queue.put({"type": "reset"})
                logger.warning(
                    "realtime/chat/stream 云端输出异常（乱码/客套），"
                    "带防客套指令重试：%r",
                    (result.text or "")[:120],
                )
                retry_system = (
                    (req.system_prompt or "")
                    + "\n\n" + _NO_CANNED_NUDGE).strip()
                try:
                    result = await client.chat(
                        messages=messages,
                        system_prompt=retry_system,
                        max_new_tokens=(
                            req.max_new_tokens or MAP_REALTIME_MAX_TOKENS),
                        tts_enabled=False,
                        enable_thinking=req.enable_thinking,
                        on_text_delta=on_delta,
                    )
                except RealtimeError:
                    pass
            if (not ASCEND_OMNI_WS_URL
                    and _looks_like_garbage(result.text)):
                await queue.put({
                    "type": "error",
                    "detail": (
                        "云端 MiniCPM-o 输出异常（未能理解该问题），请重试"
                    ),
                })
                return

            try:
                wav_base64 = (
                    result.audio_wav_base64
                    if (req.tts_enabled and not tts_failed) else "")
            except ValueError as exc:
                await queue.put({"type": "error", "detail": str(exc)})
                return
            await queue.put({
                "type": "done",
                "reply": result.text,
                "model": (
                    "llama.cpp-omni" if ASCEND_OMNI_WS_URL
                    else MAP_REALTIME_MODEL),
                "backend": "local" if ASCEND_OMNI_WS_URL else "map",
                "audio_wav_base64": wav_base64,
                "tts_failed": tts_failed,
            })

        async def _run_wrapped():
            # 无论正常完成、提前 return 还是异常，都放结束哨兵，
            # 避免 event_gen 永久等待导致连接挂起。
            try:
                await run()
            finally:
                await queue.put(None)

        task = asyncio.create_task(_run_wrapped())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(item)
            await task
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=_sse_headers())
