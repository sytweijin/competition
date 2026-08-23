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
APP_VISION_MODEL = os.getenv("APP_VISION_MODEL", "")
APP_ASR_MODEL = os.getenv("APP_ASR_MODEL", "")
# 视觉/语音可复用 LLM 网关，也可单独配置不同服务商的 API Key 与 Base URL
APP_VISION_API_KEY = os.getenv("APP_VISION_API_KEY", "") or LLM_API_KEY
APP_VISION_BASE_URL = os.getenv("APP_VISION_BASE_URL", "") or LLM_BASE_URL
APP_ASR_API_KEY = os.getenv("APP_ASR_API_KEY", "") or LLM_API_KEY
APP_ASR_BASE_URL = os.getenv("APP_ASR_BASE_URL", "") or LLM_BASE_URL
# auto：DashScope 的 qwen-audio-* 走原生 ASR endpoint，
# 其余 DashScope 系列走 chat completions + input_audio；其他服务走 audio/transcriptions
# 也可显式指定 dashscope / chat / native
APP_ASR_TRANSCRIPTION_MODE = os.getenv(
    "APP_ASR_TRANSCRIPTION_MODE", "auto").lower()
# 默认3次结构化尝试（parse_error 不重试）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
# 单次请求超时（秒）。deepseek-v4-flash 等推理模型思考耗时长、首字延迟高，
# 35s 会频繁触发超时；默认提到 180s，避免“请求未返回就被掐断”误判为 JSON 错误。
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))
LLM_PREFER_PLAIN = os.getenv("LLM_PREFER_PLAIN", "false").lower() in ("1", "true", "yes")
# DeepSeek V4 默认开启 thinking；结构化业务 Agent 显式关闭可避免长推理占用
# 输出预算并触发非流式连接提前中断。其他模型不发送该厂商私有参数。
LLM_DISABLE_THINKING = os.getenv(
    "LLM_DISABLE_THINKING", "true").lower() in ("1", "true", "yes")
# 单次生成最大 token。推理模型（如 deepseek-v4-flash）会把 reasoning_content 也计入
# 该预算，留给最终 JSON 的空间有限；默认提到 16000，并在被截断时自动翻倍重试。
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16000"))

# MiniCPM-o Realtime（华为昇腾创新应用赛道）
# 使用 ModelBest 提供的 Realtime WebSocket API，Chat 模式走 turn-based 对话。
MAP_REALTIME_API_KEY = os.getenv("MAP_REALTIME_API_KEY", "")
MAP_REALTIME_MODEL = os.getenv("MAP_REALTIME_MODEL", "MiniCPM-o-4.5-Realtime")
MAP_REALTIME_BASE_URL = os.getenv(
    "MAP_REALTIME_BASE_URL", "wss://api.modelbest.cn/v1/realtime")
MAP_REALTIME_MODE = os.getenv("MAP_REALTIME_MODE", "chat")
MAP_REALTIME_MAX_TOKENS = int(os.getenv("MAP_REALTIME_MAX_TOKENS", "1024"))
MAP_REALTIME_TIMEOUT = int(os.getenv("MAP_REALTIME_TIMEOUT", "60"))

# 本地 llama-omni-server（昇腾 A3 上启动后，通过 VSCode 端口转发 28099）
# 配置后 Realtime 客户端优先连本地 /backend，MAP_REALTIME_API_KEY 可留空
ASCEND_OMNI_WS_URL = os.getenv("ASCEND_OMNI_WS_URL", "")
ASCEND_OMNI_TIMEOUT = int(os.getenv("ASCEND_OMNI_TIMEOUT", "300"))

# App
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
APP_HTTPS = os.getenv("APP_HTTPS", "1") not in ("0", "false", "False", "no")
APP_HTTPS_PORT = int(os.getenv("APP_HTTPS_PORT", "8443"))
APP_ADMIN_TOKEN = os.getenv("APP_ADMIN_TOKEN", "")
APP_USERS_JSON = os.getenv("APP_USERS_JSON", "")
APP_NOTIFY_WEBHOOK = os.getenv("APP_NOTIFY_WEBHOOK", "")
APP_NOTIFY_WEBHOOKS = os.getenv("APP_NOTIFY_WEBHOOKS", "")

# 持久化：local 使用本地 memory 目录；s3 启用 S3 兼容对象存储同步
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "workbench/").strip("/")
S3_REGION = os.getenv("S3_REGION", "auto")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")

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
