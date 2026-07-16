"""
FastAPI 应用入口（A5）
"""

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import APP_HOST, APP_PORT, BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="小组合作智能体", version="3.0")

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
