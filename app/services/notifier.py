"""外部通知服务：Webhook 推送提醒（企业微信 / 飞书 / 钉钉群机器人格式）。"""

from __future__ import annotations

import json
import urllib.request

from app.config import APP_NOTIFY_WEBHOOK, APP_NOTIFY_WEBHOOKS
from app.config import now as app_now
from app.models.schemas import FullPlan
from app.services.collab import reminders


def _webhook_urls() -> list[str]:
    """解析全部通知地址：兼容旧 APP_NOTIFY_WEBHOOK，新增 APP_NOTIFY_WEBHOOKS 逗号/分号分隔。"""
    import app.config as config

    urls: list[str] = []
    for raw in (config.APP_NOTIFY_WEBHOOK, config.APP_NOTIFY_WEBHOOKS):
        if not raw:
            continue
        for url in raw.replace("；", ";").split(";"):
            for item in url.split(","):
                item = item.strip()
                if item and item.startswith("http") and item not in urls:
                    urls.append(item)
    return urls


def _webhook_kind(url: str) -> str:
    """按域名识别 webhook 平台：企业微信 / 飞书 / 钉钉 / 未知。"""
    if "qyapi.weixin.qq.com" in url or "qyapi.wechat" in url:
        return "wecom"
    if "open.feishu.cn" in url or "feishu.cn" in url:
        return "feishu"
    if "oapi.dingtalk.com" in url:
        return "dingtalk"
    return "generic"


def _build_payload(kind: str, content: str) -> dict:
    """按平台消息契约构造请求体。"""
    if kind == "feishu":
        lines = [ln for ln in content.splitlines() if ln.strip()]
        elements = []
        for index, line in enumerate(lines):
            text = line.lstrip("> ").strip()
            if not text:
                continue
            if index == 0:
                # 首行通常是项目名，去掉原有 markdown 符号后整体加粗
                first = text.replace("*", "").strip()
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{first}**",
                    },
                })
            else:
                elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": text},
                })
            if index < len(lines) - 1:
                elements.append({"tag": "hr"})
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "协作分工智能体"},
                    "template": "blue",
                },
                "elements": elements,
            },
        }
    if kind == "dingtalk":
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": "协作分工智能体",
                "text": content,
            },
        }
    if kind == "generic":
        return {"text": content}
    # 企业微信群机器人
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }


def _post_webhook(content: str) -> dict:
    """按平台自动适配格式推送，支持多个 webhook 地址。"""
    urls = _webhook_urls()
    if not urls:
        return {"sent": False, "enabled": False}
    sent = 0
    errors: list[str] = []
    bodies: list[str] = []
    for url in urls:
        kind = _webhook_kind(url)
        payload = _build_payload(kind, content)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
                body = response.read().decode("utf-8", errors="replace")
            if status >= 400:
                errors.append(f"{kind}: HTTP {status} {body[:120]}")
            else:
                sent += 1
                bodies.append(body)
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
    result = {"sent": sent > 0, "enabled": True, "status": 200 if sent else 0}
    if sent:
        result["bodies"] = bodies
    if errors:
        result["errors"] = errors
    return result


def notify_reminders(plan: FullPlan) -> dict:
    items = reminders(plan)
    if not _webhook_urls():
        return {
            "sent": False,
            "enabled": False,
            "reminders": items,
        }
    if not items:
        return {
            "sent": True,
            "enabled": True,
            "status": 200,
            "reminders": items,
            "skipped": True,
        }
    lines = [f"**{plan.input.course.name}** 待处理提醒 {len(items)} 条："]
    for item in items:
        lines.append(f"> {item.get('title', '')}：{item.get('detail', '')}")
    lines.append(f"\n> {app_now().strftime('%H:%M')} 由协作分工智能体推送")
    result = _post_webhook("\n".join(lines))
    result["reminders"] = items
    return result


def notify_event(plan: FullPlan, event: str) -> dict:
    """推送一条事件文本（成员汇报、状态变更等）到外部 Webhook。"""
    content = (
        f"**{plan.input.course.name}**\n"
        f"> {event}\n\n"
        f"> {app_now().strftime('%H:%M')} 由协作分工智能体推送"
    )
    return _post_webhook(content)
