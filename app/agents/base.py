"""Agent 基类，所有 Agent 继承此基类。"""

import logging
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.llm.client import LLMClient
from app.models.schemas import AgentError

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class BaseAgent(Generic[T]):
    """所有 Agent 的基类。

    子类只需定义：
    - system_prompt: str
    - response_model: type[T]
    - 实现 run() 方法
    """

    system_prompt: str = ""
    response_model: type[T] | None = None

    def __init__(self, llm: LLMClient | None = None):
        # 默认使用全局共享 LLMClient，复用 OpenAI SDK 的 httpx 连接池，
        # 避免每次请求新建客户端导致首次请求冷启动超时。
        self.llm = llm or LLMClient.get_shared()

    def _call_llm(self, user_prompt: str, temperature: float = 0.3,
                  max_retries: int | None = None) -> T | AgentError:
        """调用 LLM 并校验输出格式"""
        if not self.response_model:
            raise NotImplementedError("Subclass must set response_model")
        kwargs = {}
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        return self.llm.chat_structured(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=self.response_model,
            temperature=temperature,
            **kwargs,
        )
