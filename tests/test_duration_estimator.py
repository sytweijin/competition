from datetime import date

from app.models.schemas import (
    AssignmentInput, CourseInfo, PlanOutput, SubTask, TeamMember,
)
from app.services.duration_estimator import (
    build_duration_context, calibrate_plan_estimates, estimate_task,
)
from app.services.project_service import generate_draft


def _input(available_hours: float) -> AssignmentInput:
    return AssignmentInput(
        course=CourseInfo(name="课程展示", description="完成一份6分钟汇报PPT"),
        members=[TeamMember(name="甲", available_hours=available_hours)],
        deadline=date(2026, 8, 1),
    )


def test_light_submission_is_not_inflated():
    task = SubTask(id="T1", name="检查并提交已有文档", estimated_hours=8)
    estimate = estimate_task(task)
    assert estimate.hours <= 1.5
    assert estimate.confidence == "中"


def test_scope_changes_report_estimate():
    short = estimate_task(SubTask(
        id="T1", name="撰写个人总结报告",
        description="完成约1000字个人总结", estimated_hours=5))
    long = estimate_task(SubTask(
        id="T2", name="撰写调研报告",
        description="形成不超过10000字的正式调研报告", estimated_hours=5))
    assert long.hours > short.hours
    assert long.max_hours > short.max_hours


def test_calibration_adds_explanation_fields():
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="完成秀米排版", estimated_hours=8)],
        summary="测试",
    )
    calibrated = calibrate_plan_estimates(plan)
    task = calibrated.tasks[0]
    assert task.estimated_hours == 2
    assert task.estimate_min_hours == 1
    assert task.estimate_max_hours == 3.5
    assert task.estimate_reason


def test_available_capacity_does_not_inflate_fast_draft():
    low = generate_draft(_input(10), use_ai=False)
    high = generate_draft(_input(100), use_ai=False)
    assert [(task.name, task.estimated_hours) for task in low.tasks] == [
        (task.name, task.estimated_hours) for task in high.tasks
    ]


def test_retrieval_context_contains_capacity_guardrail():
    context = build_duration_context("制作课程汇报PPT并检查提交")
    assert "汇报 PPT" in context
    assert "不得为了填满产能" in context
