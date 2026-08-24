"""
FastAPI 应用入口（A5）
"""

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    APP_ADMIN_TOKEN, APP_HOST, APP_HTTPS, APP_HTTPS_PORT,
    APP_PORT, BASE_DIR, configure_timezone,
)
from app.metrics import request_metrics
from app.services.auth_store import auth_enabled, username_by_token
from app.services.share_store import get_share_entry

configure_timezone()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)
_HTTPS_STARTED = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时预热 LLM 客户端，并按需自动开启 HTTPS 监听（手机语音/录像需要）。"""
    from app.llm.client import LLMClient

    LLMClient.get_shared()
    if APP_HTTPS:
        _start_https_listener()
    yield


app = FastAPI(title="协作分工智能体", version="7.1", lifespan=lifespan)
request_metrics.mark_started(datetime.now(timezone.utc).isoformat())

# 全局异常处理器：意外错误不暴露代码堆栈，返回 JSON 错误信息
_DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理的异常，防止平台暴露 Python 堆栈。"""
    logger.exception("未捕获的错误: %s %s", request.method, request.url.path)
    detail = str(exc) if _DEBUG else "服务器内部错误"
    return JSONResponse(status_code=500, content={"detail": detail})


@app.middleware("http")
async def admin_auth_middleware(request: Request, call_next):
    """启用 APP_ADMIN_TOKEN 时保护 /api 接口（登录、健康检查、分享读取除外）。"""
    share_token = request.headers.get("x-share-token", "")
    if share_token and request.url.path.startswith("/api/"):
        entry = get_share_entry(share_token)
        if not entry:
            return JSONResponse(
                status_code=403,
                content={"detail": "分享链接无效、已过期或已撤销"},
            )
        readonly_safe = {
            "/api/workload",
            "/api/resource-calendar",
            "/api/reminders",
            "/api/org-review",
            "/api/knowledge",
            "/api/agent/ask",
            "/api/tools/call",
            "/api/export/markdown",
            "/api/export/excel",
            "/api/export/csv",
            "/api/export/ics",
            "/api/export/docx",
            "/api/export/pdf",
            "/api/chat",
            "/api/report",
            "/api/interview/materials",
            "/api/interview",
            "/api/interview/chat",
        }
        path = request.url.path
        scoped_share_read = request.method == "GET" and path == f"/api/share/{share_token}"
        allowed = path in readonly_safe or scoped_share_read
        if not allowed:
            return JSONResponse(status_code=403, content={"detail": "只读分享模式禁止修改"})
        # 分享令牌已完成后端校验；无需再要求站点账号登录。
        return await call_next(request)
    if auth_enabled() and request.url.path.startswith("/api/"):
        path = request.url.path
        allow = {
            "/api/health",
            "/api/ready",
            "/api/auth/status",
            "/api/auth/login",
        }
        allow_share = request.method == "GET" and path.startswith("/api/share/")
        if path not in allow and not allow_share:
            auth = request.headers.get("authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            username = username_by_token(token)
            if username is None:
                return JSONResponse(status_code=401, content={"detail": "未授权"})
            request.state.username = username
    elif not auth_enabled():
        request.state.username = "admin"
    return await call_next(request)


@app.middleware("http")
async def request_metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000
        request_metrics.record(500, request.url.path, duration_ms)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    request_metrics.record(response.status_code, request.url.path, duration_ms)
    return response


@app.middleware("http")
async def no_cache_html_middleware(request: Request, call_next):
    """HTML 响应不缓存：保证 index.html 每次都重新校验，加载最新 ?v= 静态资源。

    之前 index.html 无显式 Cache-Control，浏览器启发式缓存旧页面，导致
    前端修复后用户仍加载旧 app.js（老哈希），出现"改了没生效"的错觉。
    JS/CSS 仍按内容哈希 ?v= 缓存，不受影响。
    """
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# 注册路由
from app.web.routes import router as api_router
app.include_router(api_router, prefix="/api")

# 静态文件（前端 demo 页面）：用 BASE_DIR 绝对路径，避免工作目录非项目根时导入即崩
_STATIC_DIR = str(BASE_DIR / "app" / "web" / "static")
_TEMPLATES_DIR = str(BASE_DIR / "app" / "web" / "templates")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
app.mount("/", StaticFiles(directory=_TEMPLATES_DIR, html=True), name="web")


def _lan_ips() -> list[str]:
    """探测本机局域网 IPv4 地址（不依赖外部依赖）。"""
    import socket

    ips: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))  # 仅取路由，不发送数据
        primary = sock.getsockname()[0]
        sock.close()
        if primary and not primary.startswith("127."):
            ips.append(primary)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def _primary_lan_ip() -> str:
    ips = _lan_ips()
    return ips[0] if ips else "localhost"


def _ensure_self_signed_cert() -> tuple[str, str]:
    """生成/复用自签名证书（缓存于 memory/ssl，不入库）。"""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir = BASE_DIR / "memory" / "ssl"
    cert_dir.mkdir(parents=True, exist_ok=True)
    keyfile = cert_dir / "key.pem"
    certfile = cert_dir / "cert.pem"
    if keyfile.exists() and certfile.exists():
        return str(certfile), str(keyfile)

    host = _primary_lan_ip()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    alt_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    try:
        alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        pass
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(certfile), str(keyfile)


def _start_https_listener() -> int | None:
    """后台线程启动 HTTPS 监听（自签名证书），手机经 https 可用语音/录像。"""
    global _HTTPS_STARTED
    if _HTTPS_STARTED or os.getenv("PYTEST_CURRENT_TEST"):
        return None
    # 端口已被占用（可能是热重载时旧实例未完全释放）：跳过，不报错刷屏
    try:
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind((APP_HOST, APP_HTTPS_PORT))
        probe.close()
    except OSError:
        return None
    try:
        certfile, keyfile = _ensure_self_signed_cert()
    except Exception as exc:  # 证书生成失败不影响 HTTP
        logger.warning("自动 HTTPS 未启用（自签名证书生成失败：%s）", exc)
        return None
    config = uvicorn.Config(
        "app.main:app",
        host=APP_HOST,
        port=APP_HTTPS_PORT,
        ssl_certfile=certfile,
        ssl_keyfile=keyfile,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    def _run():
        try:
            server.run()
        except Exception as exc:
            logger.warning("HTTPS 监听启动失败（%s）", exc)

    threading.Thread(
        target=_run, daemon=True, name="https-listener",
    ).start()
    _HTTPS_STARTED = True
    return APP_HTTPS_PORT


def _print_access_banner() -> None:
    """启动时打印本机/手机（局域网）访问地址，方便手机端操作。"""
    print()
    print("=" * 58)
    print("  协作分工智能体已启动")
    print(f"  本机访问   : http://127.0.0.1:{APP_PORT}")
    if APP_HOST in ("0.0.0.0", "::", "") or not APP_HOST.startswith("127."):
        lan_ips = _lan_ips()
        for ip in lan_ips:
            if APP_HTTPS:
                print(f"  手机访问   : https://{ip}:{APP_HTTPS_PORT}"
                      "（含语音/录像/摄像头；首次证书警告点「继续」）")
            else:
                print(f"  手机访问   : http://{ip}:{APP_PORT}")
        if not lan_ips:
            print("  手机/局域网 : 未探测到局域网 IP，请检查网络连接")
        print("  提示：手机与电脑需在同一 WiFi；连不上时请放行防火墙端口")
        print("  提示：鉴权默认关闭，暴露到局域网/公网前建议配置 APP_ADMIN_TOKEN")
    else:
        print("  手机/局域网 : 需要先允许外部访问（当前 APP_HOST=127.0.0.1）")
        if APP_HTTPS:
            print(f"              （HTTP 与 HTTPS 都需要 0.0.0.0 才能被手机访问）")
        print("              启动前执行：")
        print("              $env:APP_HOST='0.0.0.0'; python -m app.main")
    print("=" * 58)
    print()


if __name__ == "__main__":
    _print_access_banner()
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=True)
