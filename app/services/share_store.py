"""只读分享链接存储。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.config import MEMORY_DIR

SHARE_FILE = MEMORY_DIR / "shares.json"


def _load_shares() -> dict:
    if not SHARE_FILE.exists():
        return {}
    try:
        return json.loads(SHARE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_shares(shares: dict) -> None:
    SHARE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHARE_FILE.write_text(
        json.dumps(shares, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_share(filename: str) -> str:
    shares = _load_shares()
    token = uuid.uuid4().hex
    shares[token] = {"filename": filename}
    _save_shares(shares)
    return token


def get_share_filename(token: str) -> str | None:
    shares = _load_shares()
    entry = shares.get(token)
    return entry.get("filename") if entry else None
