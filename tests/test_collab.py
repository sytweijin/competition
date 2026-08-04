"""Collaboration, knowledge search and org review tests."""

import json
from datetime import date, timedelta

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, ProjectModule, QAOutput,
    ReportOutput, SubTask, TeamMember, TimelineOutput, Volunteer,
)
from app.services.collab import knowledge_search, org_review, reminders


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试项目", description="做一份调研报告"),
            members=[
                TeamMember(name="小文", role="项目负责人"),
                TeamMember(name="小陈", role="执行成员"),
            ],
            project_mode="large_project",
            deadline=date.today() + timedelta(days=10),
        ),
        plan=PlanOutput(
            tasks=[
                SubTask(
                    id="T1", name="调研任务", estimated_hours=4,
                    assignee_id="小文", module_id="M1",
                    end_date=date.today() + timedelta(days=1),
                    actual_hours=6,
                ),
                SubTask(
                    id="T2", name="未分配任务", estimated_hours=2,
                    module_id="M1",
                ),
            ],
            modules=[ProjectModule(id="M1", name="模块1")],
            summary="测试方案",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
        volunteer_pool=[Volunteer(name="小王", task_id="T1", status="待确认")],
    )


def test_reminders_detect_due_unassigned_volunteer_and_module():
    items = reminders(_plan())
    types = {item["type"] for item in items}
    assert "due" in types
    assert "unassigned" in types
    assert "volunteer" in types
    assert "module" in types


def test_knowledge_search_finds_memory_plans(tmp_path, monkeypatch):
    import app.services.collab as collab

    monkeypatch.setattr(collab, "MEMORY_DIR", tmp_path)
    (tmp_path / "old_plan.json").write_text(
        json.dumps({
            "input": {"course": {"name": "校园低碳生活倡议", "description": "调研与传播"}},
            "plan": {"summary": "先调研再发布", "tasks": [{"name": "数据调研"}]},
        }),
        encoding="utf-8",
    )
    result = knowledge_search("校园低碳数据调研怎么做？", plan=None)
    assert result["sources"]
    assert "校园低碳生活倡议" in result["answer"]


def test_org_review_reports_deviation_and_suggestion():
    review = org_review(_plan())
    assert review["members"]["小文"]["actual_hours"] == 6
    assert review["roles"]["项目负责人"]["planned_hours"] == 4
    assert any("高于计划" in s for s in review["suggestions"])


def test_share_store_roundtrip(tmp_path, monkeypatch):
    import app.services.share_store as share

    monkeypatch.setattr(share, "SHARE_FILE", tmp_path / "shares.json")
    token = share.create_share("plan.json")
    assert share.get_share_filename(token) == "plan.json"
