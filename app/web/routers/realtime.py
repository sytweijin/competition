"""华为昇腾创新应用赛道：MiniCPM-o Realtime 对话、语音对话与转写路由。"""

import asyncio
import base64
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import (
    ASCEND_OMNI_WS_URL,
    MAP_REALTIME_API_KEY,
    MAP_REALTIME_MAX_TOKENS,
    MAP_REALTIME_MODEL,
)
from app.services.realtime_client import RealtimeClient, RealtimeError

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

INTERVIEW_TURN_INSTRUCTION = (
    "用户正在用语音/视频回答你刚才提出的问题。\n"
    "请严格按以下结构输出，且只输出这四个部分：\n"
    "1) 第一行写【回答摘要】，下一段用不超过两句话概括用户回答的要点；\n"
    "2) 再写【评委回复】，随后以评委身份做两件事：\n"
    "   a. 点评：明确指出用户是否正面回答了问题、是否回避或遗漏了要点、"
    "回答是否全面、依据是否到位；\n"
    "   b. 追问：如果回答不完整、有漏洞或不到位，就同一问题继续追问、"
    "要求补充说明，不要提出新问题；只有回答到位，才提出下一个新问题。\n"
    "不要输出任何占位符、尖括号标签或格式说明。"
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
    if _looks_like_canned_reply(text) or _looks_like_garbage(text):
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
    if _looks_like_canned_reply(text):
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
                )
            except RealtimeError as retry_exc:
                raise HTTPException(
                    status_code=502, detail=str(retry_exc)) from retry_exc
            tts_failed = True
        else:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    transcript = result.transcript or ""
    if ASCEND_OMNI_WS_URL and not transcript:
        # 本地 A3 不返回转写文本：补一次转写，供前端把真实内容写进历史；
        # 转写不可靠（客套回复/幻觉）时保持空，前端回退 "[语音消息]" 占位。
        try:
            from app.services.omni_chat import (
                _looks_like_canned_reply, transcribe_audio)
            candidate = await transcribe_audio(audio_b64, timeout=60)
            if candidate and not _looks_like_canned_reply(candidate):
                transcript = candidate
        except Exception:
            transcript = ""
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
        "audio_wav_base64": wav_base64,
        "tts_failed": tts_failed,
        "backend": backend,
    }


@router.post("/realtime/voice-requirement")
async def realtime_voice_requirement(file: UploadFile = File(...)):
    """语音需求理解：本地直听 / 云端转写后理解，返回整理后的需求要点文本。

    与"转写"不同：本地 A3 能直接听懂音频，转写指令会被模型当成对话回答；
    因此统一走 understand_audio 适配层，两端都返回"理解后的需求要点"，
    供答辩模拟/项目配置等需求输入使用。
    """
    from app.services.media_analysis import _decode_audio_to_pcm16k
    from app.services.omni_chat import understand_audio

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
    from app.services.omni_chat import _looks_like_canned_reply
    if _looks_like_canned_reply(text):
        raise HTTPException(
            status_code=502,
            detail="语音需求理解失败：本地昇腾未返回有效需求要点"
            "（模型可能把指令当成了对话），请重试或改用云端后端"
            "（临时注释 .env 中 ASCEND_OMNI_WS_URL）",
        )
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


def _parse_turn_text(text: str) -> tuple[str, str]:
    """解析答辩回合输出：返回 (回答摘要, 评委回复)。"""
    text = (text or "").strip()
    placeholder_lines = {
        "<摘要>", "<点评与下一个问题>", "<摘要内容>",
        "点评与下一个问题", "<点评>",
    }

    def clean(part: str) -> str:
        lines = [
            line for line in part.splitlines()
            if line.strip() not in placeholder_lines
            and "<点评与下一个问题>" not in line
            and "<摘要>" not in line
        ]
        return "\n".join(lines).strip()

    if "【回答摘要】" in text and "【评委回复】" in text:
        _, rest = text.split("【回答摘要】", 1)
        summary, reply = rest.split("【评委回复】", 1)
        return clean(summary), clean(reply)
    if "【评委回复】" in text:
        _, reply = text.split("【评委回复】", 1)
        return "", clean(reply)
    return "", clean(text)


def _parse_meeting_text(text: str) -> tuple[str, list[dict], str]:
    """解析会议整理输出：返回 (会议要点, 任务列表, 风险)。"""
    text = (text or "").strip()
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
    ]
    reply = ""
    summary = ""
    audio_hollow = False
    audio_error = ""
    if audio:
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        from app.services.omni_chat import (
            _AUDIO_CHUNK_SECONDS, _looks_like_garbage, understand_audio)

        try:
            result = await understand_audio(
                audio_b64,
                judge_sys,
                "用户正在回答你提出的问题，请听用户的语音并点评追问。",
                max_new_tokens=MAP_REALTIME_MAX_TOKENS,
                history=history_list,
            )
            summary, reply = _parse_turn_text(result.text)
            # 本地 A3 常把音频当对话而非听懂内容：输出"用户尚未提供回答内容"
            # 等空转文本。命中即标记，等待画面观察是否兜底。
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
        parts = [
            {"type": "text", "text": PERFORMANCE_PROMPT.format(n=index)},
            {"type": "image",
             "data": base64.b64encode(frame).decode("utf-8")},
        ]
        try:
            obs = await asyncio.to_thread(
                _run_realtime_media_chat, parts, 300, False, 120)
            if obs and obs.strip() and not _looks_like_garbage(obs):
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


@router.post("/realtime/meeting")
async def realtime_meeting(file: UploadFile = File(...)):
    """会议旁听：听会议录音/看会议录像，整理要点、任务（负责人/截止）与风险。

    录像链路与答辩一致：抽帧看画面 + 抽音频听内容，边看边听整理会议；
    纯录音走音频理解；无声轨的录屏视频仅凭画面理解整理。
    """
    from app.services.media_analysis import (
        _run_realtime_media_chat,
        extract_audio_pcm16k,
        extract_video_frames,
    )
    from app.services.realtime_client import RealtimeClient, RealtimeError

    raw = await file.read(MAX_PERFORMANCE_SIZE + 1)
    if len(raw) > MAX_PERFORMANCE_SIZE:
        raise HTTPException(status_code=413, detail="录音/录像文件超过 60MB 限制")
    try:
        frames = await asyncio.to_thread(extract_video_frames, raw, 3)
    except Exception:
        frames = []
    audio = await asyncio.to_thread(extract_audio_pcm16k, raw)
    if not audio and not frames:
        raise HTTPException(status_code=400, detail="未从录音/录像中提取到音频或画面")

    summary = ""
    tasks: list[dict] = []
    risks = ""
    text = ""
    audio_error = ""
    if audio:
        audio_b64 = base64.b64encode(audio).decode("utf-8")
        from app.services.omni_chat import understand_audio

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
            if obs and obs.strip() and not _looks_like_garbage(obs):
                visual.append(f"第 {index} 帧：{obs.strip()}")
        except Exception:
            continue

    if audio_error and not visual:
        raise HTTPException(status_code=502, detail=audio_error)

    if visual and (not audio or audio_error):
        # 无声轨录屏 / 音频理解失败：把画面理解交给模型再整理成结构化会议结果
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
        parts = [
            {"type": "text",
             "text": PERFORMANCE_PROMPT.format(n=index)},
            {"type": "image",
             "data": base64.b64encode(frame).decode("utf-8")},
        ]
        try:
            obs = await asyncio.to_thread(
                _run_realtime_media_chat, parts, 300, False, 120)
            if obs and obs.strip() and not _looks_like_garbage(obs):
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
    # 文字对话场景不做"客套回复"拦截：模型回"你好，有什么可以帮您"是正常
    # 承接，只有乱码才需要重试/报错。
    from app.services.omni_chat import _looks_like_garbage
    if (ASCEND_OMNI_WS_URL and _looks_like_garbage(result.text)
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
    if ASCEND_OMNI_WS_URL and _looks_like_garbage(result.text):
        raise HTTPException(
            status_code=502,
            detail="本地昇腾模型输出异常（未能理解该问题）：已切换通用模型回答",
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
