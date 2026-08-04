"""Multi-user session and ACL tests."""


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
