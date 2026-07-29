"""全局配置：API Key、模型选择、路径等。"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# LLM
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
# 默认3次结构化尝试（parse_error 不重试）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
# 单次请求超时（秒）。deepseek-v4-flash 等推理模型思考耗时长、首字延迟高，
# 35s 会频繁触发超时；默认提到 180s，避免“请求未返回就被掐断”误判为 JSON 错误。
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))
LLM_PREFER_PLAIN = os.getenv("LLM_PREFER_PLAIN", "false").lower() in ("1", "true", "yes")
# 单次生成最大 token。推理模型（如 deepseek-v4-flash）会把 reasoning_content 也计入
# 该预算，留给最终 JSON 的空间有限；默认提到 16000，并在被截断时自动翻倍重试。
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16000"))

# App
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
