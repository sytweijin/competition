"""Webhook notification tests."""

from datetime import date
from unittest.mock import MagicMock, patch

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)
from app.services.notifier import notify_reminders


def _plan():
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试项目", description=""),
            members=[TeamMember(name="小文")],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(
            tasks=[SubTask(id="T1", name="未分配任务", estimated_hours=2)],
            summary="测试",
        ),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_notify_disabled_without_webhook(monkeypatch):
    import app.services.notifier as notifier

    monkeypatch.setattr(notifier, "APP_NOTIFY_WEBHOOK", "")
    result = notify_reminders(_plan())
    assert result["enabled"] is False
    assert result["sent"] is False


def test_notify_sends_payload(monkeypatch):
    import app.services.notifier as notifier

    monkeypatch.setattr(notifier, "APP_NOTIFY_WEBHOOK", "https://example.com/hook")
    mock = MagicMock()
    mock.status = 200
    with patch("urllib.request.urlopen", return_value=mock) as urlopen:
        result = notify_reminders(_plan())
    assert result["enabled"] is True
    assert result["sent"] is True
    request = urlopen.call_args[0][0]
    assert "测试项目" in request.data.decode("utf-8")
