"""全局配置：API Key、模型选择、路径等。"""

import os
from datetime import date, datetime, timedelta, timezone
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

# 应用时区：部署在 Render(UTC) 时仍按东八区判断“今天”，
# 避免排期边界（倒推起始日、可用工作日数）整体偏移一天。
APP_TZ_OFFSET = int(os.getenv("APP_TZ_OFFSET", "8"))
APP_TZ = timezone(timedelta(hours=APP_TZ_OFFSET))


def today() -> date:
    """返回应用时区的当前日期（默认东八区）。"""
    return datetime.now(APP_TZ).date()


def now() -> datetime:
    """返回应用时区的当前时间（默认东八区）。"""
    return datetime.now(APP_TZ)


def configure_timezone():
    """在进程启动时设置系统时区。

    在 Linux（如 Render）上调用 time.tzset() 让整个进程的
    date.today() / datetime.now() 都使用东八区，避免排期边界偏移。
    Windows 无 tzset，使用系统默认时区即可。
    """
    import time
    tz = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    os.environ["TZ"] = tz
    try:
        time.tzset()
    except AttributeError:
        pass  # Windows 无 tzset
