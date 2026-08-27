"""Multi-user session and ACL tests."""

import hashlib
import json


def test_users_sessions_and_acl(tmp_path, monkeypatch):
    import app.services.auth_store as auth

    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(auth, "ACL_FILE", tmp_path / "acl.json")
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(auth, "APP_USERS_JSON", "")

    auth.seed_users()
    assert auth.verify_login("admin", "secret") is True
    assert auth.verify_login("admin", "wrong") is False

    token = auth.create_session("admin")
    assert auth.username_by_token(token) == "admin"

    assert auth.can_read("carol", "other.json") is False
    assert auth.can_read("admin", "other.json") is True

    auth.set_acl("plan.json", owner="alice", editors=["bob"])
    assert auth.can_read("bob", "plan.json") is True
    assert auth.can_write("bob", "plan.json") is True
    assert auth.can_read("carol", "plan.json") is False
    assert auth.can_write("carol", "plan.json") is False
    assert auth.can_read("admin", "plan.json") is True
    assert "plan.json" in auth.accessible_filenames("bob")


def test_salted_password_hash_and_legacy_compat(tmp_path, monkeypatch):
    """新写入的密码用加盐 PBKDF2；旧版无盐 sha256 哈希仍可登录。"""
    import app.services.auth_store as auth

    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(auth, "ACL_FILE", tmp_path / "acl.json")
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(auth, "APP_USERS_JSON", "")

    auth.seed_users()
    users = json.loads(auth.USERS_FILE.read_text(encoding="utf-8"))
    assert users["admin"]["password_hash"].startswith("sha256$")
    assert auth.verify_login("admin", "secret") is True
    assert auth.verify_login("admin", "wrong") is False

    # 旧版无盐 sha256 哈希（存量 users.json）仍应验证通过
    users["admin"]["password_hash"] = (
        hashlib.sha256(b"secret").hexdigest())
    auth.USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False), encoding="utf-8")
    assert auth.verify_login("admin", "secret") is True


def test_session_expiry_and_legacy_format(tmp_path, monkeypatch):
    """新会话 30 天有效；旧版裸用户名会话长期有效；过期会话拒绝。"""
    import app.services.auth_store as auth

    monkeypatch.setattr(auth, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(auth, "ACL_FILE", tmp_path / "acl.json")
    monkeypatch.setattr(auth, "APP_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(auth, "APP_USERS_JSON", "")

    token = auth.create_session("admin")
    assert auth.username_by_token(token) == "admin"

    auth.SESSIONS_FILE.write_text(
        json.dumps({"legacy-token": "admin"}), encoding="utf-8")
    assert auth.username_by_token("legacy-token") == "admin"

    auth.SESSIONS_FILE.write_text(
        json.dumps({
            "expired-token": {"username": "admin", "expires": 0},
        }),
        encoding="utf-8",
    )
    assert auth.username_by_token("expired-token") is None
