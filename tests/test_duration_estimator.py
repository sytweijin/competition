from datetime import date

from app.models.schemas import (
    AssignmentInput, CourseInfo, PlanOutput, SubTask, TeamMember,
)
from app.services.duration_estimator import (
    build_duration_context, calibrate_plan_estimates, estimate_task,
    record_duration_feedback,
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


def test_upper_word_limit_is_not_treated_as_full_length():
    upper = estimate_task(SubTask(
        id="T1", name="撰写调研报告",
        description="报告不超过10000字", estimated_hours=12))
    exact = estimate_task(SubTask(
        id="T2", name="撰写调研报告",
        description="完成约10000字报告", estimated_hours=12))
    assert upper.hours < exact.hours
    assert "上限而非默认写满" in upper.reason


def test_required_activity_duration_is_separate_from_effort():
    estimate = estimate_task(SubTask(
        id="T1", name="组织理论讲座",
        description="讲座共2学时并保留记录", estimated_hours=8))
    assert estimate.hours == 2
    assert estimate.required_duration_hours == 2
    assert "不直接当作负责人制作工时" in estimate.reason


def test_three_similar_user_corrections_adjust_future_estimate(tmp_path, monkeypatch):
    from app.services import duration_estimator

    monkeypatch.setattr(
        duration_estimator, "_FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    for number in range(3):
        original = SubTask(
            id=f"T{number}", name="完成秀米排版", estimated_hours=2,
            estimate_reason="知识库自动建议")
        corrected = original.model_copy(update={"estimated_hours": 1})
        assert record_duration_feedback(original, corrected)
    learned = estimate_task(SubTask(
        id="T9", name="完成秀米排版", estimated_hours=5))
    assert learned.hours == 1
    assert learned.confidence == "高"
    assert "3 条相似任务" in learned.reason
