"""成员轻量汇报链接：令牌绑定 方案文件 + 成员名，支持有效期与汇报备注。

令牌写入本地 memory/report_tokens.json；汇报备注（语音整理、照片证据）
写入 memory/report_notes.json，按 方案文件+任务ID 保存，不修改方案结构。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from app.config import MEMORY_DIR

REPORT_FILE = MEMORY_DIR / "report_tokens.json"
NOTES_FILE = MEMORY_DIR / "report_notes.json"
_LOCK = threading.RLock()
DEFAULT_TTL_DAYS = 14


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _cleanup(data: dict, now: float) -> dict:
    return {
        key: value for key, value in data.items()
        if (value.get("expires") or 0) > now
    }


def create_report_token(
    filename: str, member: str, ttl_days: int = DEFAULT_TTL_DAYS,
) -> str:
    now = time.time()
    token = secrets.token_urlsafe(24)
    with _LOCK:
        data = _cleanup(_load(REPORT_FILE), now)
        data[token] = {
            "filename": filename,
            "member": member,
            "created": now,
            "expires": now + ttl_days * 86400,
        }
        _save(REPORT_FILE, data)
    return token


def get_report_token(token: str) -> dict | None:
    if not token:
        return None
    now = time.time()
    with _LOCK:
        data = _cleanup(_load(REPORT_FILE), now)
        entry = data.get(token)
        if not entry or (entry.get("expires") or 0) <= now:
            return None
        return entry


def add_report_note(filename: str, task_id: str, note: str) -> list[str]:
    key = f"{filename}::{task_id}"
    with _LOCK:
        data = _load(NOTES_FILE)
        notes = list(data.get(key) or [])
        if note:
            notes.append(note)
            data[key] = notes[-20:]
            _save(NOTES_FILE, data)
    return data.get(key) or notes


def get_report_notes(filename: str, task_id: str) -> list[str]:
    key = f"{filename}::{task_id}"
    with _LOCK:
        return list(_load(NOTES_FILE).get(key) or [])
