"""外部通知服务：Webhook 推送提醒。"""

from __future__ import annotations

import json
import urllib.request

from app.config import APP_NOTIFY_WEBHOOK
from app.models.schemas import FullPlan
from app.services.collab import reminders


def notify_reminders(plan: FullPlan) -> dict:
    items = reminders(plan)
    if not APP_NOTIFY_WEBHOOK:
        return {
            "sent": False,
            "enabled": False,
            "reminders": items,
        }
    payload = {
        "project": plan.input.course.name,
        "reminders": items,
    }
    request = urllib.request.Request(
        APP_NOTIFY_WEBHOOK,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
        return {
            "sent": True,
            "enabled": True,
            "status": status,
            "reminders": items,
        }
    except Exception as exc:
        return {
            "sent": False,
            "enabled": True,
            "error": str(exc),
            "reminders": items,
        }
