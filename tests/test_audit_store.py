"""Audit log and rollback store tests."""

import json

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
    assert new_filename == "plan.json"
    assert versions[0]["parent_version_id"] == version_id


def test_version_tree_tracks_linear_history_and_rollback_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")

    first = {
        "input": {"course": {"name": "校园活动"}, "members": []},
        "plan": {"tasks": [{"id": "T1", "name": "活动策划", "estimated_hours": 4}]},
    }
    second = {
        "input": {"course": {"name": "校园活动"}, "members": []},
        "plan": {"tasks": [{"id": "T1", "name": "活动策划", "estimated_hours": 6}]},
    }
    v1 = store.save_version(first, "plan.json", summary="初版")
    v2 = store.save_version(second, "plan.json", summary="增加工时")
    assert store.list_versions("plan.json")[0]["parent_version_id"] == v1

    store.rollback_plan("plan.json", v1)
    versions = store.list_versions("plan.json")
    rollback = versions[0]
    assert rollback["parent_version_id"] == v1
    assert rollback["version_id"] != v2

    tree = store.list_version_tree("plan.json")
    by_id = {node["version_id"]: node for node in tree["nodes"]}
    assert by_id[v1]["child_count"] == 2
    assert by_id[v2]["depth"] == 1
    assert by_id[rollback["version_id"]]["depth"] == 1


def test_similar_projects_join_version_family_and_can_compare(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")

    first = {
        "input": {
            "course": {"name": "校园低碳项目"},
            "members": [{"name": "小林", "role": "执行成员", "skill_tags": ["调研"]}],
            "deadline": "2026-08-20",
        },
        "plan": {"tasks": [
            {"id": "T1", "name": "需求调研", "estimated_hours": 4, "assignee_id": "小林"},
            {"id": "T2", "name": "成果发布", "estimated_hours": 3},
        ]},
    }
    second = {
        "input": {
            "course": {"name": "校园低碳项目优化"},
            "members": [{"name": "小林", "role": "项目负责人", "skill_tags": ["调研"]}],
            "deadline": "2026-08-22",
        },
        "plan": {"tasks": [
            {"id": "T1", "name": "需求调研", "estimated_hours": 6, "assignee_id": "小林"},
            {"id": "T3", "name": "现场活动", "estimated_hours": 5},
        ]},
    }
    v1 = store.save_version(first, "carbon.json")
    v2 = store.save_version(second, "carbon-copy.json")

    latest = store.list_versions("carbon-copy.json")[0]
    assert latest["version_id"] == v2
    assert latest["parent_version_id"] == v1
    assert latest["parent_filename"] == "carbon.json"

    tree = store.list_version_tree("carbon-copy.json")
    assert {node["filename"] for node in tree["nodes"]} == {
        "carbon.json", "carbon-copy.json",
    }
    diff = store.compare_versions("carbon.json", v1, "carbon-copy.json", v2)
    assert diff["summary"]["tasks_added"] == 1
    assert diff["summary"]["tasks_removed"] == 1
    assert diff["summary"]["tasks_changed"] == 1
    assert diff["summary"]["members_changed"] == 1


def test_legacy_flat_snapshots_are_read_as_linear_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(store, "MEMORY_DIR", tmp_path / "memory")
    (store.AUDIT_DIR).mkdir(parents=True)
    version_dir = store.VERSION_DIR / "legacy.json"
    version_dir.mkdir(parents=True)
    plan = {
        "input": {"course": {"name": "旧项目"}},
        "plan": {"tasks": [{"id": "T1", "name": "旧任务"}]},
    }
    for version_id in ("legacy_v1", "legacy_v2"):
        (version_dir / f"{version_id}.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8"
        )
    entries = [
        {"version_id": "legacy_v1", "timestamp": "2026-08-01T00:00:00Z", "action": "保存"},
        {"version_id": "legacy_v2", "timestamp": "2026-08-02T00:00:00Z", "action": "保存"},
    ]
    (store.AUDIT_DIR / "legacy.json.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )

    versions = store.list_versions("legacy.json")
    assert versions[0]["version_id"] == "legacy_v2"
    assert versions[0]["parent_version_id"] == "legacy_v1"
    assert versions[0]["root_version_id"] == "legacy_v1"
