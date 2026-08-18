"""当前方案的文件导出路由。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models.schemas import FullPlan
from app.services.plan_io import plan_to_csv, plan_to_excel, plan_to_ics
from app.services.report_service import generate_report

router = APIRouter(prefix="/export")


@router.post("/docx")
def export_docx(plan: FullPlan):
    """导出当前计划为 Word 文档。"""
    from app.web.exporters import plan_to_docx

    data = plan_to_docx(generate_report(plan))
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={"Content-Disposition": 'attachment; filename="plan_report.docx"'},
    )


@router.post("/pdf")
def export_pdf(plan: FullPlan):
    """导出当前计划为 PDF 文档。"""
    from app.web.exporters import plan_to_pdf

    data = plan_to_pdf(generate_report(plan))
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="plan_report.pdf"'},
    )


@router.post("/markdown")
def export_current_plan(plan: FullPlan):
    """导出当前计划为 Markdown，无需先保存。"""
    # 延迟导入兼容 routes._plan_to_markdown 的既有测试与调用方；
    # 下一阶段可将 Markdown 序列化器独立迁入 exporters.py。
    from app.web.routes import _plan_to_markdown

    markdown = _plan_to_markdown(generate_report(plan).model_dump())
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plan_report.md"'},
    )


@router.post("/excel")
def export_excel(plan: FullPlan):
    """导出任务、成员、分工、时间线与复盘工作簿。"""
    try:
        content = plan_to_excel(plan)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="plan_export.xlsx"'},
    )


@router.post("/csv")
def export_csv(plan: FullPlan):
    """导出任务 CSV。"""
    return Response(
        content=plan_to_csv(plan),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plan_tasks.csv"'},
    )


@router.post("/ics")
def export_ics(plan: FullPlan):
    """导出日历 ICS。"""
    return Response(
        content=plan_to_ics(plan),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plan_calendar.ics"'},
    )
