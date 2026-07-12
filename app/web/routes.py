"""
FastAPI 路由（A5：简易只读 Web）
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.coordinator import Coordinator
from app.models.schemas import AssignmentInput, CourseInfo, TeamMember, FullPlan

logger = logging.getLogger(__name__)
router = APIRouter()


class RunRequest(BaseModel):
    course: CourseInfo
    members: list[TeamMember]
    deadline: str
    additional_requirements: str = ""


@router.post("/run", response_model=FullPlan)
async def run_plan(req: RunRequest):
    """执行完整的 Agent 链路并返回结果"""
    try:
        from datetime import date
        inp = AssignmentInput(
            course=req.course,
            members=req.members,
            deadline=date.fromisoformat(req.deadline),
            additional_requirements=req.additional_requirements,
        )
        coordinator = Coordinator()
        result = coordinator.run(inp)
        return result
    except Exception as e:
        logger.exception("Run failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health():
    return {"status": "ok"}
