"""进程内请求级监控：错误率、响应时间分位数与按路径统计。"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

_BUCKETS_MS = (100, 250, 500, 1000, 2000, 5000, 10000)


def _bucket_index(duration_ms: float) -> int:
    for index, upper in enumerate(_BUCKETS_MS):
        if duration_ms <= upper:
            return index
    return len(_BUCKETS_MS)


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0
        self.errors = 0
        self.by_status: dict[int, int] = defaultdict(int)
        self.by_path: dict[str, dict] = defaultdict(lambda: {
            "count": 0,
            "errors": 0,
            "latency_ms_sum": 0.0,
        })
        self.buckets = [0] * (len(_BUCKETS_MS) + 1)

    def record(self, status_code: int, path: str, duration_ms: float) -> None:
        with self._lock:
            self.total += 1
            self.by_status[status_code] += 1
            if status_code >= 500:
                self.errors += 1
            item = self.by_path[path]
            item["count"] += 1
            item["latency_ms_sum"] += duration_ms
            if status_code >= 500:
                item["errors"] += 1
            self.buckets[_bucket_index(duration_ms)] += 1

    def snapshot(self) -> dict:
        with self._lock:
            error_rate = round(self.errors / self.total, 6) if self.total else 0.0
            paths = {}
            for path, item in sorted(self.by_path.items()):
                count = item["count"]
                paths[path] = {
                    "count": count,
                    "errors": item["errors"],
                    "error_rate": round(
                        item["errors"] / count, 6) if count else 0.0,
                    "avg_ms": round(
                        item["latency_ms_sum"] / count, 3) if count else 0.0,
                }
            return {
                "total": self.total,
                "errors": self.errors,
                "error_rate": error_rate,
                "by_status": {
                    str(status): count
                    for status, count in sorted(self.by_status.items())
                },
                "by_path": paths,
                "latency_buckets_ms": {
                    str(upper): self.buckets[index]
                    for index, upper in enumerate(_BUCKETS_MS)
                },
                "latency_over_10s": self.buckets[-1],
                "started_at": getattr(self, "_started_at", ""),
            }

    def mark_started(self, started_at: str) -> None:
        self._started_at = started_at


request_metrics = RequestMetrics()
