"""
A1: LLM 调用封装 + Structured Output
所有 Agent 通过此模块调用 LLM，不直接调用 OpenAI SDK。

v1.1 改进：
- 加 timeout 防止永久挂起
- 错误分类：auth_error / rate_limit / parse_error / timeout / unknown
- structured output fallback：parse 失败时回退到普通 create + validate
- chat_text 改用 .create() 而非 .parse()
"""

import json
import logging
import re
from typing import Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_RETRIES
from app.models.schemas import AgentError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

LLM_TIMEOUT = 120  # 秒


def _classify_error(e: Exception) -> str:
    """将异常分类为可操作的 error_type。

    优先用 OpenAI SDK 异常类型，避免关键字误判（兼容非英文报错）。
    """
    import openai as _oai
    def _t(name):
        cls = getattr(_oai, name, None)
        return (cls,) if cls is not None else ()
    if isinstance(e, (TimeoutError,) + _t("APITimeoutError")):
        return "timeout"
    if isinstance(e, _t("AuthenticationError")):
        return "auth_error"
    if isinstance(e, _t("RateLimitError")):
        return "rate_limit"
    if isinstance(e, _t("APIConnectionError")):
        return "timeout"
    if isinstance(e, ValidationError):
        return "parse_error"
    if isinstance(e, _t("BadRequestError")):
        return "parse_error"
    if isinstance(e, _t("APIStatusError")):
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code is not None and 500 <= code < 600:
            return "unknown"
        return "parse_error"
    return "unknown"


class LLMClient:
    """线程安全的 LLM 调用客户端（每个 Agent 可独立实例化）"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or LLM_MODEL
        self._enabled = bool(LLM_API_KEY)
        self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    def chat_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.3,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> T | AgentError:
        """调用 LLM 并返回结构化输出。

        策略：先尝试 structured output（response_format），失败后回退到
        普通 create + 手动 JSON 提取 + model_validate_json。
        """
        if not self._enabled:
            return AgentError(agent="LLMClient", error_type="auth_error",
                              message="LLM_API_KEY 未配置，跳过 LLM 调用",
                              recoverable=False)
        retries = max(1, max_retries)
        for attempt in range(retries):
            try:
                return self._try_structured(system_prompt, user_prompt,
                                            response_model, temperature)
            except Exception as e:
                err_type = _classify_error(e)
                logger.warning("LLM structured attempt %d/%d (%s): %s",
                               attempt + 1, retries, err_type, e)
                if err_type == "auth_error":
                    return AgentError(agent="LLMClient", error_type=err_type,
                                     message=f"API 鉴权失败：{e}",
                                     recoverable=False)
                if err_type == "parse_error":
                    break  # 结构化重试无意义，直接回退 plain create
                # rate_limit / timeout / unknown：可重试，最后一次落到 fallback
        try:
            logger.info("Falling back to plain create + validate")
            return self._try_plain_validate(
                system_prompt, user_prompt,
                response_model, temperature)
        except Exception as e2:
            return AgentError(
                agent="LLMClient",
                error_type=_classify_error(e2),
                message=(f"LLM 调用失败（已尝试 {retries} "
                         f"次结构化 + 1 次回退）：{e2}"),
                recoverable=True,
            )

    def _try_structured(self, system_prompt, user_prompt,
                        response_model, temperature) -> T:
        """尝试使用 beta structured output API。"""
        resp = self._client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_model,
            temperature=temperature,
            timeout=LLM_TIMEOUT,
        )
        msg = resp.choices[0].message
        parsed = getattr(msg, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, response_model):
                return parsed
            return response_model.model_validate(parsed)
        raw = getattr(msg, "content", None)
        if not raw:
            raise ValueError("Empty response from LLM")
        return response_model.model_validate_json(raw)

    def _try_plain_validate(self, system_prompt, user_prompt,
                            response_model, temperature) -> T:
        """回退：普通 create + 手动提取 JSON + 验证。"""
        # 在 prompt 里强调 JSON 输出
        enhanced_system = system_prompt + "\n\n重要：你必须输出合法 JSON，不要包含 markdown 代码块标记。"
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": enhanced_system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            timeout=LLM_TIMEOUT,
        )
        raw = resp.choices[0].message.content or ""
        # 尝试提取 JSON（去掉 markdown 代码块包裹）
        raw = self._extract_json(raw)
        return response_model.model_validate_json(raw)

    @staticmethod
    def _extract_json(raw: str) -> str:
        """从可能包含 markdown 代码块的响应中提取 JSON。"""
        # 去掉 ```json ... ``` 包裹
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 尝试找到第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return raw[start:end + 1]
        return raw.strip()

    def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> str | AgentError:
        """自由文本调用（用于 B1 答辩模拟等无需严格结构化的场景）"""
        if not self._enabled:
            return AgentError(agent="LLMClient", error_type="auth_error", message="LLM_API_KEY 未配置，跳过调用", recoverable=False)
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                timeout=LLM_TIMEOUT,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err_type = _classify_error(e)
            logger.error("LLM text call failed (%s): %s", err_type, e)
            return AgentError(
                agent="LLMClient",
                error_type=err_type,
                message=str(e),
                recoverable=(err_type not in ("auth_error",)),
            )
