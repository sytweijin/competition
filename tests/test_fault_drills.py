"""故障演练对应的自动化测试（基础版通用路径：app.services.remote_io）。"""

import os
import socket
import time

import pytest


def test_artifact_expiry_cleanup_removes_stale_directories(monkeypatch, tmp_path):
    from app.services import remote_io

    old = tmp_path / "artifacts" / "old"
    fresh = tmp_path / "artifacts" / "fresh"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (old / "file.txt").write_text("stale", encoding="utf-8")
    (fresh / "file.txt").write_text("new", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(fresh, (now, now))

    monkeypatch.setattr(remote_io, "ARTIFACT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(remote_io, "_ARTIFACT_TTL_SECONDS", 10)
    remote_io.cleanup_artifacts()

    assert not old.exists()
    assert fresh.exists()


def test_download_rejects_private_network(monkeypatch):
    from app.services.remote_io import RemoteFileError, download_remote_file

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ],
    )
    with pytest.raises(RemoteFileError, match="内网"):
        download_remote_file("https://example.com/file.pdf", 1000)


def test_download_rejects_host_outside_allowlist(monkeypatch):
    from app.services.remote_io import RemoteFileError, download_remote_file

    monkeypatch.setenv("ATTACHMENT_HOSTS", "allowed.example.com")
    with pytest.raises(RemoteFileError, match="允许"):
        download_remote_file("https://other.example.com/file.pdf", 1000)
