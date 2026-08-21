"""可选 S3 兼容对象存储同步层。

默认保持本地 memory 目录读写；配置 ``STORAGE_BACKEND=s3`` 后，
方案、导出附件和分享令牌会同步到对象存储，重启不丢数据。
"""

from __future__ import annotations

import os

from app.config import (
    S3_BUCKET, S3_ENDPOINT_URL, S3_PREFIX, S3_REGION, STORAGE_BACKEND,
)


class ObjectStorageError(RuntimeError):
    pass


class S3ObjectStorage:
    def __init__(self) -> None:
        if not S3_BUCKET:
            raise ObjectStorageError("STORAGE_BACKEND=s3 时必须配置 S3_BUCKET")

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise ObjectStorageError("S3 同步需要安装 boto3") from exc
        kwargs = {
            "region_name": S3_REGION,
        }
        if S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = S3_ENDPOINT_URL
        return boto3.client("s3", **kwargs)

    def _key(self, key: str) -> str:
        return f"{S3_PREFIX}/{key.lstrip('/')}"

    def write_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        kwargs = {
            "Bucket": S3_BUCKET,
            "Key": self._key(key),
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            self._client().put_object(**kwargs)
        except Exception as exc:
            raise ObjectStorageError(f"S3 写入失败：{exc.__class__.__name__}") from exc

    def read_bytes(self, key: str) -> bytes:
        try:
            response = self._client().get_object(
                Bucket=S3_BUCKET, Key=self._key(key))
            return response["Body"].read()
        except Exception as exc:
            raise ObjectStorageError(f"S3 读取失败：{exc.__class__.__name__}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client().head_object(Bucket=S3_BUCKET, Key=self._key(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        try:
            self._client().delete_object(Bucket=S3_BUCKET, Key=self._key(key))
        except Exception as exc:
            raise ObjectStorageError(f"S3 删除失败：{exc.__class__.__name__}") from exc

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self._client().get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(
                Bucket=S3_BUCKET, Prefix=self._key(prefix)):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key.startswith(f"{S3_PREFIX}/"):
                        keys.append(key[len(S3_PREFIX) + 1:])
        except Exception as exc:
            raise ObjectStorageError(f"S3 列表失败：{exc.__class__.__name__}") from exc
        return keys

    def check(self) -> bool:
        """执行最小权限探测，供 readiness 检查确认对象存储可访问。"""
        try:
            self._client().list_objects_v2(
                Bucket=S3_BUCKET, Prefix=self._key(""), MaxKeys=1)
            return True
        except Exception:
            return False


def get_object_storage() -> S3ObjectStorage | None:
    if STORAGE_BACKEND != "s3":
        return None
    return S3ObjectStorage()


def s3_enabled() -> bool:
    return STORAGE_BACKEND == "s3" and bool(S3_BUCKET)
