"""
FastAPI 路由（A5：简易只读 Web + B2 Memory + B4 动态编辑）
"""

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
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


def _safe_filepath(filename: str) -> Path:
    """构造安全的 memory 目录文件路径，防止路径穿越。"""
    # 只取文件名部分，去掉任何目录分隔符
    safe = Path(filename).name
    if not safe or safe in (".", "..") or not safe.endswith(".json"):
        raise HTTPException(status_code=400, detail="非法文件名")
    fp = (MEMORY_DIR / safe).resolve()
    # 确保解析后的路径仍在 MEMORY_DIR 内
    if not str(fp).startswith(str(MEMORY_DIR.resolve())):
        raise HTTPException(status_code=400, detail="非法文件名")
    return fp



class RunRequest(BaseModel):
    course: CourseInfo
    members: list[TeamMember]
    deadline: str
    additional_requirements: str = ""


@router.post("/run", response_model=FullPlan)
async def run_plan(req: RunRequest):
    """执行完整的 Agent 链路并返回结果。"""
    try:
        # 校验：至少 1 个有姓名的成员（P1-16）
        valid_members = [m for m in req.members if m.name.strip()]
        if not valid_members:
            raise HTTPException(status_code=400, detail="至少需要 1 名有姓名的团队成员")
        inp = AssignmentInput(
            course=req.course,
            members=valid_members,
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
        # 清洗课程名中的路径分隔符等危险字符，防止路径穿越
        raw_name = plan.input.course.name or "plan"
        course_name = re.sub(r'[^\w\u4e00-\u9fff._-]', "_", raw_name).strip("_") or "plan"
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
        filepath = _safe_filepath(filename)
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
        filepath = _safe_filepath(filename)
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


@router.post("/recompute", response_model=FullPlan)
async def recompute_plan(req: FullPlan):
    """基于任务状态/成员变动重新计算时间线和匹配（不重跑 LLM）。

    前端状态切换（completed/blocked 等）或成员变动后调用此端点，
    确保排期与分配与最新状态保持一致。
    """
    try:
        from app.agents.scoring import assign_with_balance
        from app.agents.timeline import TimelineAgent

        plan = req.plan
        members = req.input.members

        # 重算 Matcher（确定性，即时可见）
        qa_matrix = assign_with_balance(plan, members)

        # 回填负责人，重算 Timeline（会读取 task.status）
        assignments: dict[str, list[str]] = {}
        for a in qa_matrix.assignments:
            people = [a.presenter] if a.presenter else []
            if a.qa_primary and a.qa_primary not in people:
                people.append(a.qa_primary)
            for s in (a.qa_support or []):
                if s not in people:
                    people.append(s)
            assignments[a.task_id] = people

        timeline = TimelineAgent().run(
            plan=plan,
            deadline=req.input.deadline.isoformat(),
            assignments=assignments,
            members=members,
        )

        return FullPlan(
            input=req.input,
            plan=plan,
            timeline=timeline,
            qa_matrix=qa_matrix,
            report=req.report,
        )
    except Exception as e:
        logger.exception("Recompute failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/export/docx")
async def export_docx(plan: FullPlan):
    """导出当前计划为 Word 文档。"""
    try:
        from app.web.exporters import plan_to_docx
        data = plan_to_docx(plan)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="plan_report.docx"'},
        )
    except Exception as e:
        logger.exception("Export docx failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export/pdf")
async def export_pdf(plan: FullPlan):
    """导出当前计划为 PDF 文档。"""
    try:
        from app.web.exporters import plan_to_pdf
        data = plan_to_pdf(plan)
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="plan_report.pdf"'},
        )
    except Exception as e:
        logger.exception("Export pdf failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export/markdown")
async def export_current_plan(plan: FullPlan):
    """导出当前计划为 Markdown（前端「导出」按钮调用，无需先保存）。"""
    try:
        md = _plan_to_markdown(plan.model_dump())
        return Response(
            content=md, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="plan_report.md"'},
        )
    except Exception as e:
        logger.exception("Export current plan failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/{filename}")
async def export_plan(filename: str, fmt: str = "markdown"):
    """Export a saved plan as Markdown or plain text."""
    try:
        filepath = _safe_filepath(filename)
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Plan not found")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        md = _plan_to_markdown(data)
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
    added_members: list = Field(default_factory=list, description="新加入的成员 [{name, daily_available_hours}, ...]")


@router.post("/edit-members", response_model=FullPlan)
async def edit_members_endpoint(req: MemberEditRequest):
    """处理成员变动：删除成员、修改每日工时，然后重算 Matcher + Timeline。"""
    try:
        from app.agents.scoring import assign_with_balance
        from app.agents.timeline import TimelineAgent

        fp = req.plan
        # Update members
        new_members = []
        import math
        remaining = max(1, (fp.input.deadline - date.today()).days)
        for m in fp.input.members:
            if m.name in req.removed_members:
                continue
            if m.name in req.updated_members:
                new_daily = max(0.5, req.updated_members[m.name])
                m = m.model_copy(update={
                    "daily_available_hours": new_daily,
                    "available_hours": max(new_daily, new_daily * remaining),
                })
            new_members.append(m)

        for a in req.added_members:
            nm = a.get("name", "").strip()
            if not nm:
                continue
            dh = max(0.5, float(a.get("daily_available_hours", 4)))
            new_m = TeamMember(name=nm, daily_available_hours=dh, available_hours=max(dh, dh * remaining))
            new_members.append(new_m)
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
            for s in (a.qa_support or []):
                if s not in people:
                    people.append(s)
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
            report=fp.report.model_copy(update={
                "risk_note": (fp.report.risk_note + "\n（成员已变动，此报告可能已过期，建议重新生成）").strip(),
            }),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Edit members failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health():
    return {"status": "ok"}