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
