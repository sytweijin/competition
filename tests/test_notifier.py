"""Webhook notification tests."""

import json
from datetime import date
from unittest.mock import patch

from app.models.schemas import (
    AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
    SubTask, TeamMember, TimelineOutput,
)
from app.services.notifier import notify_reminders


class _FakeResponse:
    """模拟 urllib 响应：status 为整数、read 返回字节，供状态码比较。"""

    def __init__(self, status=200, body=b'{"errcode":0,"errmsg":"ok"}'):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


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
    import app.config as config

    monkeypatch.setattr(config, "APP_NOTIFY_WEBHOOK", "")
    monkeypatch.setattr(config, "APP_NOTIFY_WEBHOOKS", "")
    result = notify_reminders(_plan())
    assert result["enabled"] is False
    assert result["sent"] is False


def test_notify_sends_payload(monkeypatch):
    import app.services.notifier as notifier
    import app.config as config

    monkeypatch.setattr(
        config,
        "APP_NOTIFY_WEBHOOK",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
    )
    monkeypatch.setattr(config, "APP_NOTIFY_WEBHOOKS", "")
    with patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen:
        result = notify_reminders(_plan())
    assert result["enabled"] is True
    assert result["sent"] is True
    request = urlopen.call_args[0][0]
    body = request.data.decode("utf-8")
    assert "测试项目" in body
    # 企业微信群机器人要求的消息格式
    payload = json.loads(body)
    assert payload["msgtype"] == "markdown"
    assert "测试项目" in payload["markdown"]["content"]


def test_notify_feishu_payload(monkeypatch):
    import app.config as config

    monkeypatch.setattr(
        config,
        "APP_NOTIFY_WEBHOOK",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    )
    monkeypatch.setattr(config, "APP_NOTIFY_WEBHOOKS", "")
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(body=b'{"code":0,"msg":"ok"}'),
    ) as urlopen:
        result = notify_reminders(_plan())
    assert result["enabled"] is True
    assert result["sent"] is True
    body = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
    assert body["msg_type"] == "interactive"
    assert body["card"]["elements"][0]["text"]["content"].startswith("**")


def test_notify_multiple_webhooks(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "APP_NOTIFY_WEBHOOK", "")
    monkeypatch.setattr(
        config,
        "APP_NOTIFY_WEBHOOKS",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a,"
        "https://open.feishu.cn/open-apis/bot/v2/hook/b,"
        "https://oapi.dingtalk.com/robot/send?access_token=c",
    )
    with patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen:
        result = notify_reminders(_plan())
    assert result["sent"] is True
    assert urlopen.call_count == 3
