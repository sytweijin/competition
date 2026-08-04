"""Audit log and rollback store tests."""

import app.services.audit_store as store


def test_save_list_load_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")

    data = {"input": {"course": {"name": "测试项目"}}, "plan": {"tasks": []}}
    version_id = store.save_version(data, "plan.json", action="保存", summary="第一版")
    entries = store.list_versions("plan.json")
    assert entries and entries[0]["version_id"] == version_id
    assert entries[0]["action"] == "保存"

    loaded = store.load_version("plan.json", version_id)
    assert loaded["input"]["course"]["name"] == "测试项目"

    new_filename, rolled = store.rollback_plan("plan.json", version_id)
    assert (tmp_path / "memory" / new_filename).exists()
    assert rolled["input"]["course"]["name"] == "测试项目"
    versions = store.list_versions(new_filename)
    assert versions and versions[0]["action"] == "回滚"
