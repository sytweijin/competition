"""B4 动态编辑 单元测试。"""
from datetime import date

import pytest

from app.agents.validation import PlanValidationError
from app.editor import EditError, apply_edits, edit_plan
from app.models.schemas import (
    AssignmentInput, CourseInfo, EditPlanRequest, FullPlan, PlanOutput,
    QAOutput, ReportOutput, SubTask, TaskEdit, TimelineOutput, TeamMember,
)


def _base_plan():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="A", estimated_hours=4),
        SubTask(id="T2", name="B", estimated_hours=6, dependencies=["T1"]),
    ], summary="s")
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="c", description="d"),
            members=[TeamMember(name="张三", skill_tags=["x"])],
            deadline=date(2026, 7, 20),
        ),
        plan=plan,
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary="ok"),
    )


def test_add_task():
    fp = _base_plan()
    out = edit_plan(EditPlanRequest(plan=fp, edits=[
        TaskEdit(op="add", task=SubTask(id="T3", name="C", estimated_hours=3)),
    ]))
    ids = {t.id for t in out.plan.tasks}
    assert ids == {"T1", "T2", "T3"}


def test_remove_task_cleans_dependencies():
    fp = _base_plan()
    out = edit_plan(EditPlanRequest(plan=fp, edits=[
        TaskEdit(op="remove", task_id="T1"),
    ]))
    assert all(t.id != "T1" for t in out.plan.tasks)
    t2 = next(t for t in out.plan.tasks if t.id == "T2")
    assert t2.dependencies == []  # 指向 T1 的依赖被清理


def test_update_task():
    fp = _base_plan()
    out = edit_plan(EditPlanRequest(plan=fp, edits=[
        TaskEdit(op="update", task_id="T2",
                 task=SubTask(id="T2", name="B改", estimated_hours=10)),
    ]))
    t2 = next(t for t in out.plan.tasks if t.id == "T2")
    assert t2.name == "B改"
    assert t2.estimated_hours == 10


def test_recompute_timeline():
    fp = _base_plan()
    out = edit_plan(EditPlanRequest(plan=fp, edits=[
        TaskEdit(op="add", task=SubTask(id="T3", name="C", estimated_hours=3,
                                         dependencies=["T2"])),
    ], recompute_timeline=True, recompute_matcher=False))
    assert out.timeline.total_days > 0
    assert "T3" in out.timeline.critical_path


def test_recompute_matcher():
    fp = _base_plan()
    out = edit_plan(EditPlanRequest(plan=fp, edits=[
        TaskEdit(op="add", task=SubTask(id="T3", name="C", estimated_hours=3)),
    ], recompute_timeline=False, recompute_matcher=True))
    # B3 重新分配
    assert len(out.qa_matrix.assignments) == 3


def test_add_duplicate_id_raises():
    fp = _base_plan()
    with pytest.raises(EditError):
        edit_plan(EditPlanRequest(plan=fp, edits=[
            TaskEdit(op="add", task=SubTask(id="T1", name="dup", estimated_hours=1)),
        ]))


def test_remove_nonexistent_raises():
    fp = _base_plan()
    with pytest.raises(EditError):
        edit_plan(EditPlanRequest(plan=fp, edits=[
            TaskEdit(op="remove", task_id="NOPE"),
        ]))


def test_apply_edits_creates_cycle_caught_by_validate():
    """构造环，validate_plan 应拒绝。"""
    fp = _base_plan()
    plan = fp.plan
    # 让 T1 依赖 T2，T2 依赖 T1 → 环
    edited = apply_edits(plan, [
        TaskEdit(op="update", task_id="T1",
                 task=SubTask(id="T1", name="A", estimated_hours=4, dependencies=["T2"])),
    ])
    # apply_edits 不校验，但 edit_plan 会校验
    fp2 = fp.model_copy(update={"plan": edited})
    with pytest.raises(EditError):
        edit_plan(EditPlanRequest(plan=fp2, edits=[]))