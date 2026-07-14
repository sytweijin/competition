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

LLM_TIMEOUT = 60  # 秒


def _classify_error(e: Exception) -> str:
    """将异常分类为可操作的 error_type。"""
    msg = str(e).lower()
    if isinstance(e, (TimeoutError,)):
        return "timeout"
    if "401" in msg or "unauthorized" in msg or "invalid api key" in msg:
        return "auth_error"
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return "rate_limit"
    if isinstance(e, ValidationError) or "json" in msg or "parse" in msg:
        return "parse_error"
    if "connection" in msg or "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "unknown"


class LLMClient:
    """线程安全的 LLM 调用客户端（每个 Agent 可独立实例化）"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or LLM_MODEL
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
        for attempt in range(max_retries):
            try:
                # 尝试 structured output
                resp = self._try_structured(system_prompt, user_prompt,
                                            response_model, temperature)
                return resp
            except Exception as e:
                err_type = _classify_error(e)
                logger.warning("LLM structured attempt %d/%d (%s): %s",
                               attempt + 1, max_retries, err_type, e)

                # 非 retryable 的错误直接返回
                if err_type == "auth_error":
                    return AgentError(agent="LLMClient", error_type=err_type,
                                     message=f"API 鉴权失败：{e}",
                                     recoverable=False)

                if attempt == max_retries - 1:
                    # 最后一次尝试：回退到普通 create
                    try:
                        logger.info("Falling back to plain create + validate")
                        return self._try_plain_validate(
                            system_prompt, user_prompt,
                            response_model, temperature)
                    except Exception as e2:
                        return AgentError(
                            agent="LLMClient",
                            error_type=_classify_error(e2),
                            message=f"LLM 调用失败（已尝试 {max_retries} 次结构化 + 1 次回退）：{e2}",
                            recoverable=True,
                        )
        return AgentError(agent="LLMClient", error_type="unknown",
                         message="Exhausted retries", recoverable=True)

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
        raw = resp.choices[0].message.content
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
