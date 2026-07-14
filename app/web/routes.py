"""
FastAPI 路由（A5：简易只读 Web + B2 Memory + B4 动态编辑）
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import MEMORY_DIR
from app.coordinator import Coordinator
from app.editor import EditError, edit_plan
from app.agents.interview_sim import InterviewSimAgent
from app.models.schemas import (
    AssignmentInput, CourseInfo, EditPlanRequest, FullPlan, PlanOutput, QAOutput, TeamMember,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RunRequest(BaseModel):
    course: CourseInfo
    members: list[TeamMember]
    deadline: str
    additional_requirements: str = ""


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
        coordinator = Coordinator()
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
async def list_plans(q: str = ""):
    """List saved plans with optional search filter."""
    try:
        files = sorted(MEMORY_DIR.glob("*.json"), reverse=True)
        plans = []
        for f in files:
            if q and q.lower() not in f.name.lower():
                continue
            plans.append({"filename": f.name, "size": f.stat().st_size})
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


class InterviewRequest(BaseModel):
    plan: PlanOutput
    qa_matrix: QAOutput
    user_requirements: str = ""


@router.post("/interview")
async def interview_sim(req: InterviewRequest):
    """B1: 答辩模拟 - 根据计划和QA矩阵生成模拟答辩问题。"""
    try:
        agent = InterviewSimAgent()
        questions = agent.run(plan=req.plan, qa_matrix=req.qa_matrix, user_requirements=req.user_requirements)
        return {"questions": questions}
    except Exception as e:
        logger.exception("Interview sim failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export/{filename}")
async def export_plan(filename: str, fmt: str = "markdown"):
    """Export a saved plan as Markdown or plain text."""
    try:
        filepath = MEMORY_DIR / filename
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        md = _plan_to_markdown(data)
        from fastapi.responses import Response
        content_type = "text/markdown; charset=utf-8" if fmt == "markdown" else "text/plain; charset=utf-8"
        ext = ".md" if fmt == "markdown" else ".txt"
        return Response(content=md, media_type=content_type,
                        headers={"Content-Disposition": f'attachment; filename="{filename}{ext}"'})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Export failed")
        raise HTTPException(status_code=500, detail=str(e))


def _plan_to_markdown(data: dict) -> str:
    """Convert a FullPlan dict to readable Markdown."""
    lines = []
    inp = data.get("input", {})
    course = inp.get("course", {})
    lines.append(f"# {course.get('name', 'Unknown Course')} - Project Plan")
    lines.append(f"")
    lines.append(f"**Description:** {course.get('description', '')}")
    lines.append(f"**Deadline:** {inp.get('deadline', '')}")
    members = inp.get("members", [])
    if members:
        lines.append(f"**Team:** {', '.join(m.get('name','') for m in members)}")
    lines.append("")

    plan = data.get("plan", {})
    if plan.get("summary"):
        lines.append(f"## Summary")
        lines.append(plan["summary"])
        lines.append("")
    tasks = plan.get("tasks", [])
    if tasks:
        lines.append("## Tasks")
        lines.append("| ID | Name | Hours | Dependencies | Skills |")
        lines.append("|---|---|---|---|---|")
        for t in tasks:
            deps = ", ".join(t.get("dependencies", []))
            skills = ", ".join(t.get("required_skills", []))
            lines.append(f"| {t['id']} | {t['name']} | {t.get('estimated_hours',0)}h | {deps} | {skills} |")
        lines.append("")

    tl = data.get("timeline", {})
    if tl.get("tasks"):
        lines.append("## Timeline")
        lines.append(f"**Total Duration:** {tl.get('total_days', 0)} days")
        cp = tl.get("critical_path", [])
        if cp:
            lines.append(f"**Critical Path:** {' -> '.join(cp)}")
        lines.append("")
        lines.append("| Task | Start | End | Critical | Float |")
        lines.append("|---|---|---|---|---|")
        for t in tl["tasks"]:
            crit = "Yes" if t.get("is_critical") else ""
            lines.append(f"| {t['task_id']} {t['name']} | {t['start_date']} | {t['end_date']} | {crit} | {t.get('float_days',0)}d |")
        lines.append("")

    qa = data.get("qa_matrix", {})
    if qa.get("assignments"):
        lines.append("## QA Matrix")
        lines.append("| Task | Presenter | QA Primary | QA Support | Score |")
        lines.append("|---|---|---|---|---|")
        for a in qa["assignments"]:
            support = ", ".join(a.get("qa_support", []))
            score = f"{a.get('score',0)*100:.0f}%" if a.get("score") else "-"
            lines.append(f"| {a['task_name']} | {a['presenter']} | {a['qa_primary']} | {support} | {score} |")
        lines.append("")

    report = data.get("report", {})
    if report.get("summary"):
        lines.append("## Report")
        lines.append(report["summary"])
        if report.get("risk_note"):
            lines.append("")
            lines.append("**Risks:** " + report["risk_note"])
        lines.append("")

    return "\n".join(lines)



class MemberEditRequest(BaseModel):
    plan: FullPlan
    removed_members: list[str] = Field(default_factory=list, description="要移除的成员名")
    updated_members: dict[str, float] = Field(default_factory=dict, description="更新的每日工时 {姓名: 新工时}")


@router.post("/edit-members", response_model=FullPlan)
async def edit_members_endpoint(req: MemberEditRequest):
    """处理成员变动：删除成员、修改每日工时，然后重算 Matcher + Timeline。"""
    try:
        from app.agents.scoring import assign_with_balance
        from app.agents.timeline import TimelineAgent

        fp = req.plan
        # Update members
        new_members = []
        for m in fp.input.members:
            if m.name in req.removed_members:
                continue
            if m.name in req.updated_members:
                m = m.model_copy(update={"daily_available_hours": max(0.5, req.updated_members[m.name])})
            new_members.append(m)

        if not new_members:
            raise HTTPException(status_code=400, detail="不能删除所有成员")

        new_input = fp.input.model_copy(update={"members": new_members})

        # Recompute matcher with new members
        qa_matrix = assign_with_balance(fp.plan, new_members)

        # Recompute timeline
        assignments = {}
        for a in qa_matrix.assignments:
            people = [a.presenter] if a.presenter else []
            if a.qa_primary and a.qa_primary not in people:
                people.append(a.qa_primary)
            assignments[a.task_id] = people

        timeline = TimelineAgent().run(
            plan=fp.plan,
            deadline=fp.input.deadline.isoformat(),
            assignments=assignments,
            members=new_members,
        )

        return FullPlan(
            input=new_input,
            plan=fp.plan,
            timeline=timeline,
            qa_matrix=qa_matrix,
            report=fp.report,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Edit members failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health():
    return {"status": "ok"}