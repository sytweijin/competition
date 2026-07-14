"""
FastAPI 路由（A5：简易只读 Web + B2 Memory + B4 动态编辑）
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import MEMORY_DIR
from app.coordinator import Coordinator
from app.editor import EditError, edit_plan
from app.models.schemas import (
    AssignmentInput, CourseInfo, EditPlanRequest, FullPlan, TeamMember,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RunRequest(BaseModel):
    course: CourseInfo
    members: list[TeamMember]
    deadline: str
    additional_requirements: str = ""
    hours_per_day: float | None = None


@router.post("/run", response_model=FullPlan)
async def run_plan(req: RunRequest):
    """执行完整的 Agent 链路并返回结果。"""
    try:
        inp = AssignmentInput(
            course=req.course,
            members=req.members,
            deadline=date.fromisoformat(req.deadline),
            additional_requirements=req.additional_requirements,
        )
        coordinator = Coordinator(hours_per_day=req.hours_per_day)
        return coordinator.run(inp)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────── B4：动态编辑 ────────────

@router.post("/edit", response_model=FullPlan)
async def edit_plan_endpoint(req: EditPlanRequest):
    """对已有计划应用编辑（add/remove/update）并重算。"""
    try:
        return edit_plan(req)
    except EditError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Edit failed")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────── B2：Memory ────────────

@router.post("/save")
async def save_plan(plan: FullPlan):
    """保存计划到 memory 目录。"""
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        course_name = plan.input.course.name or "plan"
        filename = f"{ts}_{course_name}.json"
        filepath = MEMORY_DIR / filename
        filepath.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Plan saved to %s", filepath)
        return {"status": "ok", "filename": filename}
    except Exception as e:
        logger.exception("Save failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plans")
async def list_plans():
    """列出所有已保存的计划。"""
    try:
        files = sorted(MEMORY_DIR.glob("*.json"), reverse=True)
        plans = [
            {"filename": f.name, "size": f.stat().st_size}
            for f in files
        ]
        return {"plans": plans}
    except Exception as e:
        logger.exception("List failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/load/{filename}")
async def load_plan(filename: str):
    """加载指定计划。"""
    try:
        filepath = MEMORY_DIR / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Load failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/plans/{filename}")
async def delete_plan(filename: str):
    """删除指定计划。"""
    try:
        filepath = MEMORY_DIR / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        filepath.unlink()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Delete failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health():
    return {"status": "ok"}