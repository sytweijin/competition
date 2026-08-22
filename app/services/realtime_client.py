"""MiniCPM-o Realtime API 客户端（Chat 模式）。

协议顺序：session.queue_done -> session.init -> session.created
-> input.append -> response.output.delta / response.done -> session.close。
非浏览器客户端使用 Authorization: Bearer 头鉴权。
"""

import asyncio
import base64
import json
import logging
import struct
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit

import websockets

from app import config

logger = logging.getLogger(__name__)


class RealtimeError(RuntimeError):
    """可向 API 层暴露的 MiniCPM-o Realtime 错误。"""

    def __init__(self, message: str, error_type: str = "unknown"):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class RealtimeChatResult:
    text: str = ""
    audio_chunks: list[str] = field(default_factory=list)
    session_id: str = ""
    response_id: str = ""
    _wav_cache: str = field(default="", repr=False, init=False)

    @property
    def audio_base64(self) -> str:
        return "".join(self.audio_chunks)

    @property
    def audio_wav_base64(self) -> str:
        """把 Realtime 返回的裸 PCM 转成浏览器可直接播放的 WAV（缓存）。"""
        if not self._wav_cache:
            raw = self.audio_base64
            self._wav_cache = (
                RealtimeClient.pcm_to_wav_base64(raw) if raw else "")
        return self._wav_cache


class RealtimeClient:
    """按官方协议封装一次 turn-based Chat 会话。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        mode: str | None = None,
        max_new_tokens: int | None = None,
        timeout: float | None = None,
        local_ws_url: str | None = None,
    ):
        self.api_key = api_key or config.MAP_REALTIME_API_KEY
        self.model = model or config.MAP_REALTIME_MODEL
        self.base_url = base_url or config.MAP_REALTIME_BASE_URL
        self.mode = mode or config.MAP_REALTIME_MODE
        self.max_new_tokens = max_new_tokens or config.MAP_REALTIME_MAX_TOKENS
        self.local_ws_url = local_ws_url or config.ASCEND_OMNI_WS_URL
        self.timeout = timeout or (
            config.ASCEND_OMNI_TIMEOUT
            if self.local_ws_url
            else config.MAP_REALTIME_TIMEOUT
        )

    def _build_uri(self) -> str:
        if self.local_ws_url:
            return self.local_ws_url
        parts = urlsplit(self.base_url)
        query = parse_qsl(parts.query)
        query.append(("mode", self.mode))
        query.append(("model", self.model))
        return parts._replace(query=urlencode(query)).geturl()

    @staticmethod
    def pcm_to_wav_base64(pcm_base64: str, sample_rate: int = 24000) -> str:
        """把 MiniCPM-o Realtime 输出的裸 PCM 转成 16bit 单声道 WAV base64。

        官方协议输出为 24kHz 单声道 float32 PCM；兼容 int16 PCM 与已经是
        WAV 的输入（本地 llama-omni 等后端格式不确定时直接透传）。
        """
        if not pcm_base64:
            return ""
        try:
            pcm = base64.b64decode(pcm_base64)
        except Exception as exc:
            raise ValueError("TTS 音频 Base64 解码失败") from exc
        if not pcm:
            return ""
        if pcm.startswith(b"RIFF"):
            return pcm_base64
        if len(pcm) % 4 == 0:
            try:
                import numpy as np
                samples = np.frombuffer(pcm, dtype="<f4")
                samples = np.clip(samples, -1.0, 1.0)
                pcm16 = np.rint(samples * 32767).astype("<i2").tobytes()
            except Exception as exc:
                raise ValueError(
                    f"TTS PCM 转换失败（{type(exc).__name__}）") from exc
        elif len(pcm) % 2 == 0:
            pcm16 = pcm
        else:
            raise ValueError("TTS 音频既不是 float32 也不是 int16 PCM")

        data_len = len(pcm16)
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_len, b"WAVE",
            b"fmt ", 16, 1, 1, sample_rate,
            sample_rate * 2, 2, 16,
            b"data", data_len,
        )
        return base64.b64encode(header + pcm16).decode("utf-8")

    @staticmethod
    def _normalize_messages(
        messages: list[dict],
        system_prompt: str | None,
    ) -> list[dict]:
        normalized: list[dict] = []
        if system_prompt and not any(
            item.get("role") == "system" for item in messages
        ):
            normalized.append({"role": "system", "content": system_prompt})
        for item in messages:
            role = str(item.get("role", "user"))
            content = item.get("content")
            if role not in ("system", "user", "assistant"):
                raise RealtimeError(
                    f"不支持的对话角色：{role}", "validation_error")
            if content is None:
                raise RealtimeError(
                    "对话消息 content 不能为空", "validation_error")
            normalized.append({"role": role, "content": content})
        if not normalized:
            raise RealtimeError("对话消息不能为空", "validation_error")
        return normalized

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        max_new_tokens: int | None = None,
        tts_enabled: bool = False,
        enable_thinking: bool = False,
        omni_mode: bool = False,
        timeout: float | None = None,
    ) -> RealtimeChatResult:
        if not self.local_ws_url and not self.api_key:
            raise RealtimeError(
                "MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置，无法调用 MiniCPM-o Realtime",
                "auth_error",
            )

        normalized = self._normalize_messages(messages, system_prompt)
        budget = max_new_tokens or self.max_new_tokens
        wait = timeout or self.timeout
        uri = self._build_uri()
        headers = (
            None
            if self.local_ws_url
            else {"Authorization": f"Bearer {self.api_key}"}
        )
        connect_kwargs = {
            "open_timeout": min(30, wait),
            "max_size": 8 * 1024 * 1024,
        }
        if headers:
            connect_kwargs["additional_headers"] = headers

        try:
            async with websockets.connect(uri, **connect_kwargs) as ws:
                if self.local_ws_url:
                    await self._send_event(ws, {
                        "type": "session.init",
                        "payload": {
                            "mode": "turn_based",
                            "use_tts": bool(tts_enabled),
                        },
                    })
                else:
                    await self._wait_event(ws, {"session.queue_done"}, wait)
                    await self._send_event(
                        ws, {"type": "session.init", "payload": {}})
                await self._wait_event(ws, {"session.created"}, wait)
                input_event = {
                    "messages": normalized,
                    "streaming": True,
                    "generation": {"max_new_tokens": budget},
                }
                if not self.local_ws_url:
                    input_event.update({
                        "tts": {"enabled": bool(tts_enabled)},
                        "enable_thinking": bool(enable_thinking),
                        "omni_mode": bool(omni_mode),
                    })
                await self._send_event(ws, {
                    "type": "input.append",
                    "input": input_event,
                })
                result = RealtimeChatResult()
                while True:
                    event = await self._receive_event(ws, wait)
                    event_type = event.get("type")
                    if event_type in (
                        "response.output.delta",
                        "response.output_text.delta",
                    ):
                        kind = event.get("kind")
                        if kind in (None, "text"):
                            result.text += str(
                                event.get("text") or event.get("delta") or ""
                            )
                        elif kind == "audio":
                            result.audio_chunks.append(str(
                                event.get("audio")
                                or event.get("audio_base64")
                                or ""
                            ))
                        result.session_id = (
                            result.session_id
                            or str(event.get("session_id", ""))
                        )
                        result.response_id = (
                            result.response_id
                            or str(event.get("response_id", ""))
                        )
                    elif event_type == "response.output_audio.delta":
                        result.audio_chunks.append(str(
                            event.get("audio")
                            or event.get("audio_base64")
                            or ""
                        ))
                    elif event_type == "response.done":
                        if not result.text:
                            result.text = str(
                                event.get("text")
                                or event.get("output_text")
                                or ""
                            )
                        if not result.audio_chunks and (
                            event.get("audio") or event.get("audio_base64")
                        ):
                            result.audio_chunks = [str(
                                event.get("audio")
                                or event.get("audio_base64")
                                or ""
                            )]
                        result.session_id = (
                            result.session_id
                            or str(event.get("session_id", ""))
                        )
                        result.response_id = (
                            result.response_id
                            or str(event.get("response_id", ""))
                        )
                        if not self.local_ws_url:
                            await self._safe_send(ws, {
                                "type": "session.close",
                                "reason": "turn_done",
                            })
                        break
                    elif event_type == "error":
                        raise RealtimeError(
                            self._error_message(event),
                            self._error_type(event),
                        )
                    elif event_type == "session.closed":
                        reason = str(event.get("reason", "unknown"))
                        if result.text or event.get("text"):
                            break
                        raise RealtimeError(
                            f"MiniCPM-o Realtime 会话提前关闭：{reason}",
                            "connection_error",
                        )
                return result
        except RealtimeError:
            raise
        except websockets.exceptions.InvalidStatus as exc:
            status = (
                exc.response.status_code
                if getattr(exc, "response", None) is not None else 0
            )
            error_type = (
                "auth_error" if status in (401, 403)
                else "connection_error"
            )
            raise RealtimeError(
                f"MiniCPM-o Realtime 连接失败（HTTP {status}）",
                error_type,
            ) from exc
        except asyncio.TimeoutError as exc:
            raise RealtimeError(
                "MiniCPM-o Realtime 连接或响应超时", "timeout") from exc
        except websockets.exceptions.ConnectionClosed as exc:
            raise RealtimeError(
                "MiniCPM-o Realtime 连接已关闭", "connection_error") from exc
        except Exception as exc:
            logger.warning("MiniCPM-o Realtime 调用失败: %s", exc)
            raise RealtimeError(
                f"MiniCPM-o Realtime 调用失败：{exc}", "unknown") from exc

    @staticmethod
    async def _send_event(ws, event: dict) -> None:
        await ws.send(json.dumps(event, ensure_ascii=False))

    @classmethod
    async def _safe_send(cls, ws, event: dict) -> None:
        try:
            await cls._send_event(ws, event)
        except Exception:
            pass

    @classmethod
    async def _wait_event(cls, ws, expected: set[str], timeout: float) -> dict:
        while True:
            event = await cls._receive_event(ws, timeout)
            event_type = event.get("type")
            if event_type in expected:
                return event
            if event_type == "error":
                raise RealtimeError(
                    cls._error_message(event), cls._error_type(event))
            if event_type == "session.closed":
                reason = str(event.get("reason", "unknown"))
                raise RealtimeError(
                    f"MiniCPM-o Realtime 会话提前关闭：{reason}",
                    "connection_error",
                )

    @staticmethod
    async def _receive_event(ws, timeout: float) -> dict:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout)
        except asyncio.TimeoutError as exc:
            raise RealtimeError(
                "等待 MiniCPM-o Realtime 事件超时", "timeout") from exc
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RealtimeError(
                "MiniCPM-o Realtime 返回了非 JSON 事件",
                "parse_error",
            ) from exc

    @staticmethod
    def _error_message(event: dict) -> str:
        error = event.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return (
                error.get("message")
                or error.get("type")
                or "MiniCPM-o Realtime 服务端错误"
            )
        return "MiniCPM-o Realtime 服务端错误"

    @staticmethod
    def _error_type(event: dict) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "")).lower()
            if code in ("auth_error", "unauthorized", "invalid_api_key"):
                return "auth_error"
            if code in ("queue_full", "rate_limit", "too_many_requests"):
                return "rate_limit"
        return "unknown"
