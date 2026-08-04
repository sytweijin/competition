"""共享项目业务服务测试：Web 与未来协议适配层都依赖这里。"""

from datetime import date

import pytest

from app.models.schemas import (
    AssignmentInput, CourseInfo, DraftOperation, FullPlan, ManualAssignmentRequest,
    PlanOutput, ProjectModule, QAOutput, ReportOutput, SubTask, TeamMember,
    TimelineOutput, Volunteer,
)
from app.services.project_service import (
    ProjectServiceError, apply_manual_assignment, mutate_draft,
    record_task_actual, resource_calendar, update_task_participants,
    workload_snapshot,
)


def _draft():
    return PlanOutput(tasks=[
        SubTask(id="T1", name="框架", estimated_hours=2, order=1),
        SubTask(id="T2", name="文案", estimated_hours=4, dependencies=["T1"], order=2),
    ], summary="测试草案")


def _full_plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="推送", description="制作秀米推送"),
            members=[
                TeamMember(name="小文", skill_tags=["文案"]),
                TeamMember(name="小摄", skill_tags=["摄影"]),
            ],
            deadline=date(2026, 8, 20),
        ),
        plan=_draft(),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_mutate_draft_supports_add_update_and_reorder():
    added = SubTask(id="T3", name="排版", estimated_hours=3, order=3)
    result = mutate_draft(_draft(), [
        DraftOperation(op="add", task=added),
        DraftOperation(
            op="update", task_id="T2",
            task=SubTask(id="T2", name="文案撰写", estimated_hours=5, order=2,
                         dependencies=["T1"])),
        DraftOperation(op="reorder", ordered_ids=["T3", "T1", "T2"]),
    ])
    assert [task.id for task in result.tasks] == ["T3", "T1", "T2"]
    assert result.tasks[2].name == "文案撰写"
    assert result.tasks[2].estimated_hours == 5


def test_mutate_draft_split_and_merge():
    split = mutate_draft(_draft(), [DraftOperation(op="split", task_id="T2")])
    assert len(split.tasks) == 3
    pieces = [task for task in split.tasks if task.name.startswith("文案")]
    assert len(pieces) == 2
    merged = mutate_draft(split, [
        DraftOperation(op="merge", task_ids=[task.id for task in pieces])
    ])
    assert len(merged.tasks) == 2
    assert sum(task.estimated_hours for task in merged.tasks) == 6


def test_reorder_requires_every_task():
    with pytest.raises(ProjectServiceError):
        mutate_draft(_draft(), [
            DraftOperation(op="reorder", ordered_ids=["T1"])
        ])


def test_user_hour_edit_is_marked_as_confirmed(tmp_path, monkeypatch):
    from app.services import duration_estimator

    monkeypatch.setattr(
        duration_estimator, "_FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    original = _draft().model_copy(update={
        "tasks": [_draft().tasks[0].model_copy(update={
            "estimate_reason": "知识库建议", "estimate_confidence": "中",
        }), _draft().tasks[1]]
    })
    corrected = original.tasks[0].model_copy(update={"estimated_hours": 1})
    result = mutate_draft(original, [
        DraftOperation(op="update", task_id="T1", task=corrected)
    ])
    task = result.tasks[0]
    assert task.estimated_hours == 1
    assert task.estimate_confidence == "用户已确认"


def test_manual_assignment_and_workload_share_business_rules():
    plan = _full_plan()
    result = apply_manual_assignment(ManualAssignmentRequest(
        plan=plan,
        assignees={"T1": "小文", "T2": "小文"},
        collaborators={"T2": ["小摄"]},
    ))
    snapshot = workload_snapshot(result)
    assert result.plan.tasks[1].assignee_id == "小文"
    assert result.plan.tasks[1].collaborator_ids == ["小摄"]
    assert snapshot["members"]["小文"]["total_hours"] == 6
    # 小摄作为协作者参与 T2，应被计入工时而非被判定为"尚未分配任务"（P0：协作者工时不再为零）
    assert snapshot["members"]["小摄"]["total_hours"] > 0
    assert snapshot["members"]["小摄"]["assist_count"] == 1
    assert not any("尚未分配任务" in w for w in snapshot["warnings"])


def test_workload_counts_roles_volunteers_and_conflicts():
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试项目", description=""),
            members=[
                TeamMember(name="负责人", role="项目负责人"),
                TeamMember(name="骨干A", role="骨干 / 模块负责人"),
                TeamMember(
                    name="成员B", role="执行成员",
                    unavailable_dates=[date(2026, 8, 5)],
                ),
            ],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="任务1", estimated_hours=10,
                    assignee_id="骨干A", module_id="M1",
                    start_date=date(2026, 8, 4), end_date=date(2026, 8, 8),
                    extra_helpers_needed=1,
                ),
                SubTask(
                    id="T2", name="任务2", estimated_hours=4,
                    assignee_id="成员B",
                    start_date=date(2026, 8, 5), end_date=date(2026, 8, 6),
                ),
            ],
            modules=[ProjectModule(id="M1", name="模块1", assignee_id="骨干A")],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
        volunteer_pool=[
            Volunteer(name="志愿者小王", task_id="T1", status="已确认"),
            Volunteer(name="待定志愿者", task_id="T1", status="待确认"),
        ],
    )
    snapshot = workload_snapshot(plan)
    assert snapshot["members"]["负责人"]["total_hours"] >= 1.0
    assert "统筹" in snapshot["members"]["负责人"]["stage_hours"]
    assert snapshot["members"]["骨干A"]["total_hours"] >= 11.0
    assert snapshot["volunteers"][0]["name"] == "志愿者小王"
    assert snapshot["volunteers"][0]["total_hours"] == 5.0
    assert len(snapshot["volunteers"]) == 1
    assert any("不可用日期" in w for w in snapshot["warnings"])


def test_record_task_actual_updates_and_feedback_once(tmp_path, monkeypatch):
    from app.services import duration_estimator

    monkeypatch.setattr(
        duration_estimator, "_FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    plan = _full_plan().model_copy(update={
        "plan": _draft().model_copy(update={
            "tasks": [
                _draft().tasks[0].model_copy(update={
                    "estimate_reason": "知识库建议 2h",
                    "estimate_confidence": "中",
                }),
                _draft().tasks[1],
            ]
        })
    })
    updated = record_task_actual(plan, "T1", actual_hours=8)
    task = next(t for t in updated.plan.tasks if t.id == "T1")
    assert task.actual_hours == 8
    assert task.actual_feedback_recorded is True
    assert (tmp_path / "feedback.jsonl").exists()
    first_count = len((tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines())

    updated2 = record_task_actual(updated, "T1", actual_hours=9)
    task2 = next(t for t in updated2.plan.tasks if t.id == "T1")
    assert task2.actual_hours == 9
    second_count = len((tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines())
    assert second_count == first_count


def test_update_task_participants_drives_workload():
    plan = _full_plan()
    updated = update_task_participants(plan, "T1", [
        {"name": "小文", "role": "负责人", "contribution_hours": 2, "is_volunteer": False},
        {"name": "小摄", "role": "执行成员", "contribution_hours": 1, "is_volunteer": False},
        {"name": "外援", "role": "志愿者", "contribution_hours": 1, "is_volunteer": True},
    ])
    task = next(t for t in updated.plan.tasks if t.id == "T1")
    assert task.assignee_id == "小文"
    assert task.collaborator_ids == ["小摄"]
    assert task.extra_helpers_needed == 1
    assert len(task.participants) == 3
    snapshot = workload_snapshot(updated)
    assert snapshot["members"]["小文"]["total_hours"] == 2
    assert snapshot["members"]["小摄"]["total_hours"] == 1
    assert snapshot["volunteers"][0]["name"] == "外援"
    assert snapshot["volunteers"][0]["total_hours"] == 1


def test_resource_calendar_detects_daily_overload_and_unavailable():
    plan = FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="日历测试", description=""),
            members=[
                TeamMember(
                    name="小文", role="执行成员",
                    daily_available_hours=2,
                    unavailable_dates=[date(2026, 8, 6)],
                ),
            ],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="任务1", estimated_hours=9,
                    assignee_id="小文",
                    start_date=date(2026, 8, 5),
                    end_date=date(2026, 8, 7),
                ),
            ],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )
    cal = resource_calendar(plan)
    assert cal["days"] == ["2026-08-05", "2026-08-06", "2026-08-07"]
    assert cal["members"]["小文"]["daily_load"]["2026-08-05"] == 3.0
    assert any("不可用日期" in w for w in cal["warnings"])
    assert any("超过可用" in w for w in cal["warnings"])
