"""共享项目业务服务测试：Web 与未来协议适配层都依赖这里。"""

from datetime import date

import pytest

from app.models.schemas import (
    AssignmentInput, CourseInfo, DraftOperation, FullPlan, ManualAssignmentRequest,
    PlanOutput, QAOutput, ReportOutput, SubTask, TeamMember, TimelineOutput,
)
from app.services.project_service import (
    ProjectServiceError, apply_manual_assignment, mutate_draft, workload_snapshot,
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
    assert any("小摄" in warning for warning in snapshot["warnings"])
