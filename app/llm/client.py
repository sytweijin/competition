"""
A1: LLM 调用封装 + Structured Output
所有 Agent 通过此模块调用 LLM，不直接调用 OpenAI SDK。
"""

import logging
from typing import Optional, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_RETRIES
from app.models.schemas import AgentError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """线程安全的 LLM 调用客户端（每个 Agent 可独立实例化）"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or LLM_MODEL
        self._client = OpenAI(api_key=LLM_API_KEY,
                              base_url=LLM_BASE_URL)

    def chat_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.3,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> T | AgentError:
        """
        调用 LLM 并返回结构化输出。
        返回 response_model 实例，或 AgentError（失败时）。
        """
        for attempt in range(max_retries):
            try:
                resp = self._client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=response_model,
                    temperature=temperature,
                )
                raw = resp.choices[0].message.content
                if not raw:
                    raise ValueError("Empty response from LLM")
                return response_model.model_validate_json(raw)

            except Exception as e:
                logger.warning("LLM call attempt %d failed: %s",
                               attempt + 1, e)
                if attempt == max_retries - 1:
                    return AgentError(
                        agent="LLMClient",
                        error_type="llm_timeout",
                        message=str(e),
                        recoverable=True,
                    )
        return AgentError(
            agent="LLMClient",
            error_type="unknown",
            message="Exhausted retries",
            recoverable=True,
        )

    def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
    ) -> str | AgentError:
        """自由文本调用（用于 B1 答辩模拟等无需严格结构化的场景）"""
        try:
            resp = self._client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM text call failed: %s", e)
            return AgentError(
                agent="LLMClient",
                error_type="llm_timeout",
                message=str(e),
                recoverable=True,
            )
