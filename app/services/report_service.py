"""按需生成自然语言报告，避免 Reporter 占用核心分工响应路径。"""

from __future__ import annotations

from app.agents.reporter import ReporterAgent
from app.coordinator import Coordinator
from app.models.schemas import FullPlan, ReportOutput
from app.performance import stage


def report_is_generated(plan: FullPlan) -> bool:
    """判断当前方案是否已经包含实际报告正文。"""
    report = plan.report
    return bool(
        report.summary.strip()
        or report.timeline_section.strip()
        or report.qa_matrix_section.strip()
    )


def generate_report(plan: FullPlan, *, force: bool = False) -> FullPlan:
    """为当前核心结果生成报告；已有报告默认复用，避免重复 LLM 调用。"""
    if report_is_generated(plan) and not force:
        return plan
    with stage("Reporter"):
        report = ReporterAgent().run(
            plan=plan.plan,
            timeline=plan.timeline,
            qa_matrix=plan.qa_matrix,
        )
    if not isinstance(report, ReportOutput):
        report = ReportOutput(summary=plan.plan.summary, risk_note=str(report))
    risk_note = Coordinator._build_risk_note(
        plan.plan,
        plan.timeline,
        plan.qa_matrix,
        plan.input.members,
        plan.input.deadline,
    )
    return plan.model_copy(update={
        "report": report.model_copy(update={"risk_note": risk_note}),
    })
