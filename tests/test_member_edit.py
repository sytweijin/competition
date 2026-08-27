"""
成员变动测试（突发情况处理：退课 / 工时变更）
"""

from datetime import date

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, SubTask,
    ProjectModule, TeamMember, TimelineOutput, QAOutput, QAAssignment,
    ReportOutput, TaskStatus,
)


def _make_test_plan() -> FullPlan:
    """构造一个测试用 FullPlan。"""
    members = [
        TeamMember(name="Alice", skill_tags=["Python"], daily_available_hours=4),
        TeamMember(name="Bob", skill_tags=["Java"], daily_available_hours=4),
        TeamMember(name="Carol", skill_tags=["Go"], daily_available_hours=4),
    ]
    plan = PlanOutput(
        tasks=[
            SubTask(id="T1", name="Backend", estimated_hours=8.0, required_skills=["Python"]),
            SubTask(id="T2", name="API", estimated_hours=6.0, dependencies=["T1"], required_skills=["Java"]),
            SubTask(id="T3", name="Deploy", estimated_hours=4.0, dependencies=["T2"], required_skills=["Go"]),
        ],
        summary="Test plan",
    )
    timeline = TimelineOutput(
        tasks=[],
        critical_path=["T1", "T2", "T3"],
        total_days=5,
    )
    qa = QAOutput(assignments=[
        QAAssignment(task_id="T1", task_name="Backend", presenter="Alice", qa_primary="Bob"),
        QAAssignment(task_id="T2", task_name="API", presenter="Bob", qa_primary="Carol"),
        QAAssignment(task_id="T3", task_name="Deploy", presenter="Carol", qa_primary="Alice"),
    ])
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="Test", description="desc"),
            members=members,
            deadline=date(2026, 8, 1),
        ),
        plan=plan,
        timeline=timeline,
        qa_matrix=qa,
        report=ReportOutput(summary="test"),
    )


def test_member_removal_recomputes():
    """成员退出后，Matcher 和 Timeline 应使用剩余成员重算。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": ["Bob"],
        "updated_members": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    # Bob should no longer appear in assignments
    for a in data["qa_matrix"]["assignments"]:
        assert a["presenter"] != "Bob"
        assert a["qa_primary"] != "Bob"
    # Members list should not contain Bob
    member_names = [m["name"] for m in data["input"]["members"]]
    assert "Bob" not in member_names


def test_member_removal_clears_stale_module_owner():
    """移除成员后，模块负责人引用必须一并清空，避免后续手动分工 500。"""
    client = TestClient(app)
    fp = _make_test_plan()
    fp = fp.model_copy(update={
        "input": fp.input.model_copy(update={
            "project_mode": "large_project",
        }),
        "plan": fp.plan.model_copy(update={
            "modules": [
                ProjectModule(
                    id="M1", name="后端模块", assignee_id="Bob",
                    order=1),
            ],
            "tasks": [
                task.model_copy(update={"module_id": "M1"})
                for task in fp.plan.tasks
            ],
        }),
    })
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": ["Bob"],
        "updated_members": {},
    })
    assert resp.status_code == 200
    data = resp.json()
    module = data["plan"]["modules"][0]
    assert module["assignee_id"] is None


def test_member_hours_change_recomputes():
    """成员每日工时变更后，Timeline 应重算。"""
    client = TestClient(app)
    fp = _make_test_plan()
    original_days = fp.timeline.total_days
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": [],
        "updated_members": {"Alice": 8.0},  # Alice 从 4h -> 8h
    })
    assert resp.status_code == 200
    data = resp.json()
    # Timeline should have been recomputed (shorter with 2x Alice hours)
    assert data["timeline"]["total_days"] < original_days,         f"Expected shorter timeline, got {data['timeline']['total_days']} >= {original_days}"
    # Alice should have updated hours
    for m in data["input"]["members"]:
        if m["name"] == "Alice":
            assert m["daily_available_hours"] == 8.0


def test_member_unavailable_dates_are_updated_and_preserved():
    """成员编辑必须把多选不可用日期传入重排后的成员模型。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "member_unavailable_dates": {
            "Alice": ["2026-08-12", "2026-08-12", "2026-08-13"],
        },
    })
    assert resp.status_code == 200
    alice = next(m for m in resp.json()["input"]["members"]
                 if m["name"] == "Alice")
    assert alice["unavailable_dates"] == ["2026-08-12", "2026-08-13"]


def test_member_edit_recalculates_and_avoids_unavailable_dates():
    """成员不可用日期变动后，重算的时间线必须避开新日期，并回填到任务日期。"""
    client = TestClient(app)
    fp = _make_test_plan()
    # 给 Alice 设置整个排期窗口内都不可用：重算后 T1 应避开这些日期
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": [],
        "updated_members": {},
        "member_unavailable_dates": {
            "Alice": [
                "2026-07-28", "2026-07-29", "2026-07-30",
                "2026-07-31", "2026-08-01",
            ],
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    alice_unavailable = {
        tuple(m["unavailable_dates"]) for m in data["input"]["members"]
        if m["name"] == "Alice"
    }
    assert any("2026-07-28" in d for d in alice_unavailable)
    # 时间线任务日期已回填到任务自身，且不与 Alice 不可用日重叠
    t1 = next(t for t in data["plan"]["tasks"] if t["id"] == "T1")
    assert t1["start_date"] is not None and t1["end_date"] is not None
    from datetime import date as _date
    start = _date.fromisoformat(t1["start_date"])
    end = _date.fromisoformat(t1["end_date"])
    alice_dates = next(
        (m["unavailable_dates"] for m in data["input"]["members"]
         if m["name"] == "Alice"),
        [],
    )
    for d in alice_dates:
        day = _date.fromisoformat(d)
        assert not (start <= day <= end), (
            f"T1 排期 {start}~{end} 与 Alice 不可用日 {day} 重叠"
        )


def test_added_member_accepts_unavailable_dates():
    """大型项目阶段新增骨干时也不能丢失不可用日期。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "added_members": [{
            "name": "Dave",
            "daily_available_hours": 4,
            "unavailable_dates": ["2026-08-14"],
        }],
    })
    assert resp.status_code == 200
    dave = next(m for m in resp.json()["input"]["members"]
                if m["name"] == "Dave")
    assert dave["unavailable_dates"] == ["2026-08-14"]


def test_cannot_remove_all_members():
    """不能删除所有成员。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": ["Alice", "Bob", "Carol"],
        "updated_members": {},
    })
    assert resp.status_code == 400


def test_add_member_with_no_skills_gets_nonzero_workload():
    """新增一名无技能标签的成员后，其工作量不应为零（P0：新增成员零工时）。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": [],
        "updated_members": {},
        "added_members": [{"name": "Dave", "daily_available_hours": 4, "skill_tags": []}],
    })
    assert resp.status_code == 200
    data = resp.json()
    names = [m["name"] for m in data["input"]["members"]]
    assert "Dave" in names
    # Dave 至少作为协作者或负责人参与某项任务（修复前会被完全排除 → 零工时）
    dave_works = any(
        (a.get("presenter") == "Dave")
        or (a.get("qa_primary") == "Dave")
        or ("Dave" in (a.get("qa_support") or []))
        for a in data["qa_matrix"]["assignments"]
    )
    assert dave_works, "新增成员 Dave 被完全排除在分工之外（零工时 bug 复现）"


def test_no_changes_returns_plan_unchanged():
    """没有变动时返回原计划。"""
    client = TestClient(app)
    fp = _make_test_plan()
    resp = client.post("/api/edit-members", json={
        "plan": json.loads(fp.model_dump_json()),
        "removed_members": [],
        "updated_members": {},
    })
    # Same hours, no removal -> should return OK (recomputed with same params)
    assert resp.status_code == 200
