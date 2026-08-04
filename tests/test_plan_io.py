"""Excel/CSV/ICS import/export tests."""

from datetime import date

import pytest

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)
from app.services.plan_io import (
    parse_task_file, plan_to_csv, plan_to_excel, plan_to_ics,
)


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试", description=""),
            members=[TeamMember(name="小文", role="执行成员")],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="调研", estimated_hours=4,
                    assignee_id="小文",
                    start_date=date(2026, 8, 5),
                    end_date=date(2026, 8, 6),
                ),
            ],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_plan_to_csv_and_ics():
    plan = _plan()
    csv_text = plan_to_csv(plan)
    assert "编号" in csv_text
    assert "T1" in csv_text
    assert "调研" in csv_text
    ics_text = plan_to_ics(plan)
    assert "BEGIN:VEVENT" in ics_text
    assert "SUMMARY:调研" in ics_text
    assert "DTEND;VALUE=DATE:20260807" in ics_text


def test_parse_task_file_csv():
    content = (
        "编号,任务,模块,计划工时,负责人,开始日期,结束日期\n"
        "T1,调研,M1,4,小文,2026-08-05,2026-08-06\n"
    ).encode("utf-8")
    plan = parse_task_file(content, "tasks.csv", "large_project")
    assert len(plan.tasks) == 1
    assert plan.tasks[0].id == "T1"
    assert plan.tasks[0].module_id == "M1"
    assert plan.tasks[0].estimated_hours == 4


def test_plan_to_excel():
    pytest.importorskip("openpyxl")
    data = plan_to_excel(_plan())
    assert data[:2] == b"PK"
    from openpyxl import load_workbook

    import io

    wb = load_workbook(io.BytesIO(data))
    assert "任务" in wb.sheetnames
    assert "成员" in wb.sheetnames
    assert "复盘" in wb.sheetnames
