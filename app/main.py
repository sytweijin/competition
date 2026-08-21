"""
FastAPI 应用入口（A5）
"""

import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_ADMIN_TOKEN, APP_HOST, APP_PORT, BASE_DIR, configure_timezone
from app.metrics import request_metrics
from app.services.auth_store import auth_enabled, username_by_token
from app.services.share_store import get_share_entry

configure_timezone()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时预热共享 LLM 客户端连接，缩短冷启动首请求等待。"""
    from app.llm.client import LLMClient

    LLMClient.get_shared()
    yield


app = FastAPI(title="协作分工智能体", version="5.76", lifespan=lifespan)
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


# 注册路由
from app.web.routes import router as api_router
app.include_router(api_router, prefix="/api")

# 静态文件（前端 demo 页面）：用 BASE_DIR 绝对路径，避免工作目录非项目根时导入即崩
_STATIC_DIR = str(BASE_DIR / "app" / "web" / "static")
_TEMPLATES_DIR = str(BASE_DIR / "app" / "web" / "templates")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
app.mount("/", StaticFiles(directory=_TEMPLATES_DIR, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=True)
