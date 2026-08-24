"""系统级路由：鉴权、工具调用与健康检查。"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import (
    APP_ASR_API_KEY, APP_ASR_MODEL, APP_VISION_API_KEY, APP_VISION_MODEL,
    ASCEND_OMNI_WS_URL, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    MAP_REALTIME_API_KEY, MEMORY_DIR, S3_BUCKET, STORAGE_BACKEND,
)
from app.metrics import request_metrics
from app.models.schemas import FullPlan
from app.services.auth_store import (
    auth_enabled,
    create_session,
    verify_login,
)
from app.services.tools import call_tool, list_tools
from app.services.report_service import generate_report
from app.performance import llm_metrics
from app.services.storage import get_object_storage

router = APIRouter()


@router.post("/report", response_model=FullPlan)
def report_generate(plan: FullPlan):
    """用户明确打开/生成报告时才调用 Reporter。"""
    return generate_report(plan)


@router.get("/performance/llm")
def performance_llm():
    """返回进程生命周期内按阶段聚合的非敏感 LLM 指标。"""
    return {"stages": llm_metrics.snapshot()}


@router.get("/metrics")
def request_metrics_endpoint():
    """返回请求量、错误率、响应时间分桶和路径聚合指标。"""
    return request_metrics.snapshot()


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
    return {
        "status": "ok",
        "version": "7.1",
        "checks": {
            "storage": MEMORY_DIR.exists(),
            "llm_configured": bool(
                LLM_API_KEY and LLM_BASE_URL and LLM_MODEL),
            "vision_model_configured": bool(
                APP_VISION_MODEL and APP_VISION_API_KEY),
            "asr_model_configured": bool(
                APP_ASR_MODEL and APP_ASR_API_KEY),
            "realtime_configured": bool(
                MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL),
            "realtime_backend": (
                "local" if ASCEND_OMNI_WS_URL else "map"),
        },
    }


@router.get("/ready")
def readiness():
    """严格就绪检查：正式部署缺少模型、鉴权或持久存储时返回 503。"""
    checks = {
        "llm_configured": bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL),
        "durable_storage_configured": STORAGE_BACKEND == "s3" and bool(S3_BUCKET),
        "durable_storage_reachable": False,
    }
    if checks["durable_storage_configured"]:
        try:
            storage = get_object_storage()
            checks["durable_storage_reachable"] = bool(
                storage and storage.check())
        except Exception:
            checks["durable_storage_reachable"] = False
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "version": "7.1",
            "checks": checks,
        },
    )
