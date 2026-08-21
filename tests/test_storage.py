"""对象存储同步层与分享令牌持久化的单元测试（基础版通用路径）。"""

import pytest


class FakeObjectStorage:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def write_bytes(self, key, data, content_type=""):
        self.data[key] = data

    def read_bytes(self, key):
        return self.data[key]

    def exists(self, key):
        return key in self.data

    def delete(self, key):
        self.data.pop(key, None)

    def list_keys(self, prefix=""):
        return [key for key in self.data if key.startswith(prefix)]

    def check(self):
        return True


def test_share_tokens_sync_and_restore_from_object_storage(monkeypatch, tmp_path):
    from app.services import share_store

    storage = FakeObjectStorage()
    share_file = tmp_path / "shares.json"
    monkeypatch.setattr(share_store, "SHARE_FILE", share_file)
    monkeypatch.setattr(share_store, "get_object_storage", lambda: storage)

    token = share_store.create_share("demo.json", ttl_seconds=3600)
    assert token
    # 分享令牌元数据同步到对象存储
    assert storage.exists("metadata/shares.json")

    # 本地丢失后从对象存储恢复
    share_file.unlink()
    assert share_store.get_share_filename(token) == "demo.json"
    assert share_file.exists()
    assert share_store.share_status(token) == "active"


def test_share_token_expiry(monkeypatch, tmp_path):
    from app.services import share_store

    monkeypatch.setattr(share_store, "SHARE_FILE", tmp_path / "shares.json")
    monkeypatch.setattr(share_store, "get_object_storage", lambda: None)

    token = share_store.create_share("demo.json", ttl_seconds=60)
    assert share_store.get_share_entry(token, now=10**12) is None  # 已过期
    assert share_store.share_status(token, now=10**12) == "expired"

    token2 = share_store.create_share("demo2.json")
    assert share_store.share_status(token2) == "active"


def test_storage_backend_local_returns_none(monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(storage, "S3_BUCKET", "")
    assert storage.get_object_storage() is None
    assert storage.s3_enabled() is False


def test_storage_backend_s3_requires_bucket(monkeypatch):
    from app.services import storage

    monkeypatch.setattr(storage, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(storage, "S3_BUCKET", "")
    with pytest.raises(storage.ObjectStorageError):
        storage.get_object_storage()


def _full_plan():
    from datetime import date

    from app.models.schemas import (
        AssignmentInput, CourseInfo, FullPlan, PlanOutput, QAOutput, ReportOutput,
        SubTask, TeamMember, TimelineOutput,
    )
    return FullPlan(
        input=AssignmentInput(
            course=CourseInfo(name="测试项目", description=""),
            members=[TeamMember(name="小文")],
            deadline=date(2026, 8, 20),
        ),
        plan=PlanOutput(tasks=[SubTask(id="T1", name="调研", estimated_hours=2)], summary="测试方案"),
        timeline=TimelineOutput(tasks=[], critical_path=[], total_days=0),
        qa_matrix=QAOutput(assignments=[]),
        report=ReportOutput(summary=""),
    )


def test_audit_versions_sync_and_restore_from_object_storage(monkeypatch, tmp_path):
    from app.services import audit_store

    storage = FakeObjectStorage()
    monkeypatch.setattr(audit_store, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(audit_store, "VERSION_DIR", tmp_path / "versions")
    monkeypatch.setattr(audit_store, "get_object_storage", lambda: storage)

    plan_data = _full_plan().model_dump(mode="json")
    version_id = audit_store.save_version(plan_data, "plan.json")
    assert "audit/plan.json.jsonl" in storage.data
    assert f"versions/plan.json/{version_id}.json" in storage.data

    (tmp_path / "audit" / "plan.json.jsonl").unlink()
    (tmp_path / "versions" / "plan.json" / f"{version_id}.json").unlink()
    assert audit_store.list_versions("plan.json")[0]["version_id"] == version_id
    assert audit_store.load_version("plan.json", version_id)["input"]["course"]["name"] == "测试项目"
