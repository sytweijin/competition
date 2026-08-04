"""多用户账号、会话与项目 ACL 存储。"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from app.config import APP_ADMIN_TOKEN, APP_USERS_JSON, MEMORY_DIR

USERS_FILE = MEMORY_DIR / "users.json"
SESSIONS_FILE = MEMORY_DIR / "sessions.json"
ACL_FILE = MEMORY_DIR / "acl.json"


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def seed_users() -> None:
    if USERS_FILE.exists():
        return
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    users: dict[str, dict] = {}
    if APP_USERS_JSON:
        try:
            raw = json.loads(APP_USERS_JSON)
        except json.JSONDecodeError:
            raw = []
        for item in raw or []:
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            if username and password:
                users[username] = {
                    "password_hash": _hash(password),
                    "role": item.get("role", "editor"),
                }
    if not users and APP_ADMIN_TOKEN:
        users["admin"] = {
            "password_hash": _hash(APP_ADMIN_TOKEN),
            "role": "admin",
        }
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def auth_enabled() -> bool:
    return bool(APP_ADMIN_TOKEN or APP_USERS_JSON)


def verify_login(username: str, password: str) -> bool:
    seed_users()
    if not USERS_FILE.exists():
        return False
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    user = users.get(username)
    return bool(user and user.get("password_hash") == _hash(password))


def create_session(username: str) -> str:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    sessions = {}
    if SESSIONS_FILE.exists():
        try:
            sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            sessions = {}
    token = uuid.uuid4().hex
    sessions[token] = username
    SESSIONS_FILE.write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return token


def username_by_token(token: str) -> str | None:
    if not token:
        return None
    if APP_ADMIN_TOKEN and token == APP_ADMIN_TOKEN:
        return "admin"
    if not SESSIONS_FILE.exists():
        return None
    try:
        sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return sessions.get(token)


def _load_acl() -> dict:
    if not ACL_FILE.exists():
        return {}
    try:
        return json.loads(ACL_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_acl(acl: dict) -> None:
    ACL_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACL_FILE.write_text(
        json.dumps(acl, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def set_acl(
    filename: str,
    owner: str,
    editors: list[str] | None = None,
    viewers: list[str] | None = None,
) -> None:
    acl = _load_acl()
    entry = acl.get(filename, {})
    if not entry.get("owner"):
        entry["owner"] = owner
    if editors is not None:
        entry["editors"] = list(dict.fromkeys(editors))
    else:
        entry.setdefault("editors", [])
    if viewers is not None:
        entry["viewers"] = list(dict.fromkeys(viewers))
    if owner and owner not in entry["editors"]:
        entry["editors"].append(owner)
    acl[filename] = entry
    _save_acl(acl)


def get_acl(filename: str) -> dict:
    return _load_acl().get(filename, {})


def add_editor(filename: str, username: str) -> None:
    acl = _load_acl()
    entry = acl.get(filename, {})
    entry.setdefault("editors", [])
    if username not in entry["editors"]:
        entry["editors"].append(username)
    acl[filename] = entry
    _save_acl(acl)


def can_read(username: str | None, filename: str) -> bool:
    acl = _load_acl()
    entry = acl.get(filename)
    if not auth_enabled():
        return True
    if not entry:
        return username == "admin"
    if username == "admin":
        return True
    return username in {entry.get("owner"), *(entry.get("editors") or []), *(entry.get("viewers") or [])}


def can_write(username: str | None, filename: str) -> bool:
    acl = _load_acl()
    entry = acl.get(filename)
    if not auth_enabled():
        return True
    if not entry:
        return username == "admin"
    if username == "admin":
        return True
    return username in {entry.get("owner"), *(entry.get("editors") or [])}


def accessible_filenames(username: str | None) -> set[str]:
    acl = _load_acl()
    if username == "admin":
        return set(acl.keys())
    result = set()
    for filename, entry in acl.items():
        if username in {
            entry.get("owner"),
            *(entry.get("editors") or []),
            *(entry.get("viewers") or []),
        }:
            result.add(filename)
    return result
