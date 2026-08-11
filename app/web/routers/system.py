"""系统级路由：鉴权、工具调用与健康检查。"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.models.schemas import FullPlan
from app.services.auth_store import (
    auth_enabled,
    create_session,
    verify_login,
)
from app.services.tools import call_tool, list_tools

router = APIRouter()


@router.get("/tools")
async def tools_list():
    return {"tools": list_tools()}


class ToolCallRequest(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    plan: FullPlan | None = None


@router.post("/tools/call")
async def tools_call(request: Request, req: ToolCallRequest):
    try:
        username = getattr(request.state, "username", None)
        result = call_tool(req.tool, req.args, req.plan, username=username)
        return {"ok": True, "result": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str


@router.get("/auth/status")
async def auth_status():
    return {"enabled": auth_enabled()}


@router.post("/auth/login")
async def auth_login(req: LoginRequest):
    if not auth_enabled():
        raise HTTPException(status_code=400, detail="鉴权未启用")
    if not verify_login(req.username, req.password):
        raise HTTPException(status_code=401, detail="密码错误")
    return {"token": create_session(req.username), "username": req.username}


@router.get("/auth/me")
async def auth_me(request: Request):
    """返回当前登录用户。"""
    return {"username": getattr(request.state, "username", None)}


@router.get("/health")
async def health():
    return {"status": "ok"}

