"""远程文件安全拉取与临时产物清理（通用能力）。

SSRF / 重定向 / 大小上限保护，与具体平台解耦：
任何需要按 URL 拉取远程附件的场景都可通过 ``download_remote_file``
获得逐跳安全校验；``cleanup_artifacts`` 用于清理过期临时产物目录。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import MEMORY_DIR

ARTIFACT_DIR = MEMORY_DIR / "remote_artifacts"
_MAX_REDIRECTS = 3
_DOWNLOAD_TIMEOUT = 20.0
_ARTIFACT_TTL_SECONDS = 48 * 60 * 60


class RemoteFileError(ValueError):
    """远程文件不安全、不可下载或超过处理限制。"""


def allowed_host(hostname: str) -> bool:
    """未配置白名单时放行；配置后仅允许白名单主机及其子域。"""
    configured = [
        value.strip().lower()
        for value in os.getenv("ATTACHMENT_HOSTS", "").split(",")
        if value.strip()
    ]
    if not configured:
        return True
    host = hostname.lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in configured)


def _validate_remote_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RemoteFileError("远程地址必须是无账号信息的 HTTPS 公网 URL")
    if not allowed_host(parsed.hostname):
        raise RemoteFileError("远程地址不在允许的存储域名中")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise RemoteFileError("远程地址无法解析") from exc
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise RemoteFileError("远程地址不能指向内网或保留地址")


def download_remote_file(url: str, max_bytes: int) -> tuple[bytes, str]:
    """拉取远程文件，并对每次重定向逐跳执行 SSRF 校验。"""
    current = url
    with httpx.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _validate_remote_url(current)
            try:
                with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise RemoteFileError("下载重定向缺少目标地址")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length and int(length) > max_bytes:
                        raise RemoteFileError(
                            f"文件超过当前处理上限 {max_bytes // 1024 // 1024}MB"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise RemoteFileError(
                                f"文件超过当前处理上限 {max_bytes // 1024 // 1024}MB"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers.get("content-type", "")
            except httpx.HTTPError as exc:
                raise RemoteFileError("远程文件暂时无法下载，请稍后重试") from exc
    raise RemoteFileError("下载重定向次数过多")


def cleanup_artifacts() -> None:
    """删除超过保留期的临时产物目录。"""
    if not ARTIFACT_DIR.exists():
        return
    cutoff = time.time() - _ARTIFACT_TTL_SECONDS
    for path in ARTIFACT_DIR.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            for child in path.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    _rmtree(child)
            path.rmdir()
        except OSError:
            continue


def _rmtree(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            _rmtree(child)
        else:
            child.unlink(missing_ok=True)
    directory.rmdir()
