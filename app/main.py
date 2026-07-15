"""
FastAPI 应用入口（A5）
"""

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import APP_HOST, APP_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="小组合作智能体", version="2.0")

# 注册路由
from app.web.routes import router as api_router
app.include_router(api_router, prefix="/api")

# 静态文件（前端 demo 页面）
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.mount("/", StaticFiles(directory="app/web/templates", html=True),
          name="web")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=APP_HOST, port=APP_PORT, reload=True)
