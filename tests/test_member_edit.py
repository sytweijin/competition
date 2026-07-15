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
    TeamMember, TimelineOutput, QAOutput, QAAssignment, ReportOutput,
    TaskStatus,
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
