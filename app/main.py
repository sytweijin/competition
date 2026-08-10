"""
FastAPI 应用入口（A5）
"""

import logging
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_ADMIN_TOKEN, APP_HOST, APP_PORT, BASE_DIR, configure_timezone
from app.services.auth_store import auth_enabled, username_by_token

configure_timezone()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="协作分工智能体", version="5.37")

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
    if (
        request.headers.get("x-share-token")
        and request.method != "GET"
        and request.url.path.startswith("/api/")
    ):
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
            "/api/interview",
            "/api/interview/chat",
        }
        if request.url.path not in readonly_safe:
            return JSONResponse(status_code=403, content={"detail": "只读分享模式禁止修改"})
    if auth_enabled() and request.url.path.startswith("/api/"):
        path = request.url.path
        allow = {
            "/api/health",
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

# 注册路由
from app.web.routes import router as api_router
app.include_router(api_router, prefix="/api")
from app.compat.qingxiaoda import router as qingxiaoda_router
app.include_router(qingxiaoda_router)

# 静态文件（前端 demo 页面）：用 BASE_DIR 绝对路径，避免工作目录非项目根时导入即崩
_STATIC_DIR = str(BASE_DIR / "app" / "web" / "static")
_TEMPLATES_DIR = str(BASE_DIR / "app" / "web" / "templates")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
app.mount("/", StaticFiles(directory=_TEMPLATES_DIR, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=True)
