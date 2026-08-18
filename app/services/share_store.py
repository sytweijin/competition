"""只读分享令牌存储（支持可选有效期）。

默认写入本地 memory；配置 ``STORAGE_BACKEND=s3`` 后，分享令牌元数据
同步到对象存储，重启不丢。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from app.config import MEMORY_DIR
from app.services.storage import get_object_storage

SHARE_FILE = MEMORY_DIR / "shares.json"


def _load_shares() -> dict:
    if not SHARE_FILE.exists():
        if not _restore_shares():
            return {}
    try:
        return json.loads(SHARE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_shares(shares: dict) -> None:
    SHARE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SHARE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(shares, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(SHARE_FILE)
    _sync_shares()


def _sync_shares() -> None:
    storage = get_object_storage()
    if not storage or not SHARE_FILE.exists():
        return
    storage.write_bytes(
        "metadata/shares.json", SHARE_FILE.read_bytes(), "application/json")


def _restore_shares() -> bool:
    storage = get_object_storage()
    if not storage or not storage.exists("metadata/shares.json"):
        return False
    data = storage.read_bytes("metadata/shares.json")
    SHARE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHARE_FILE.write_bytes(data)
    return True


def create_share(filename: str, *, ttl_seconds: int | None = None) -> str:
    """创建只读分享令牌；可传 ttl_seconds 设置有效期。"""
    if ttl_seconds is not None and ttl_seconds <= 0:
        raise ValueError("分享有效期必须大于 0 秒")
    shares = _load_shares()
    token = uuid.uuid4().hex
    now = time.time()
    shares[token] = {
        "filename": filename,
        "created_at": now,
        "expires_at": now + ttl_seconds if ttl_seconds is not None else None,
    }
    _save_shares(shares)
    return token


def get_share_entry(token: str, *, now: float | None = None) -> dict | None:
    """返回有效分享令牌元数据；兼容旧版仅含 filename 的永久只读记录。"""
    shares = _load_shares()
    entry = shares.get(token)
    if not isinstance(entry, dict) or not entry.get("filename"):
        return None
    normalized = {
        "filename": entry["filename"],
        "created_at": entry.get("created_at"),
        "expires_at": entry.get("expires_at"),
    }
    current = time.time() if now is None else now
    if normalized["expires_at"] is not None \
            and current >= float(normalized["expires_at"]):
        return None
    return normalized


def share_status(token: str, *, now: float | None = None) -> str:
    entry = _load_shares().get(token)
    if not isinstance(entry, dict) or not entry.get("filename"):
        return "invalid"
    expires_at = entry.get("expires_at")
    current = time.time() if now is None else now
    if expires_at is not None and current >= float(expires_at):
        return "expired"
    return "active"


def get_share_filename(token: str) -> str | None:
    entry = get_share_entry(token)
    return entry["filename"] if entry else None
