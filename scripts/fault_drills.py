#!/usr/bin/env python
"""自动执行 D3 故障演练：服务重启、附件过期、上游超时与网络失败。

运行后会生成 JSON 演练记录到 ``docs/drill-records/``。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1,
            ) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _list_plans(port: int) -> list[str]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/plans", timeout=5,
    ) as response:
        return [item["filename"] for item in json.loads(response.read())["plans"]]


def _start_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app", "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _restart_drill() -> dict:
    port = _free_port()
    drill_name = f"drill_restart_{uuid.uuid4().hex}.json"
    drill_path = ROOT / "memory" / drill_name
    process: subprocess.Popen | None = None
    try:
        process = _start_server(port)
        if not _wait_health(port):
            return {"name": "service_restart", "ok": False, "error": "首次启动不健康"}
        drill_path.write_text(
            json.dumps({"drill": True, "phase": "before_restart"}),
            encoding="utf-8",
        )
        before = drill_name in _list_plans(port)
        process.terminate()
        process.wait(timeout=15)
        process = None

        process = _start_server(port)
        if not _wait_health(port):
            return {"name": "service_restart", "ok": False, "error": "重启后不健康"}
        after = drill_name in _list_plans(port)
        return {
            "name": "service_restart",
            "ok": before and after,
            "before_restart_persisted": before,
            "after_restart_persisted": after,
        }
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if drill_path.exists():
            drill_path.unlink()


def _pytest_drill() -> dict:
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q",
            "tests/test_fault_drills.py",
            "tests/test_agent_benchmark.py",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return {
        "name": "timeout_expiry_network_tests",
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "summary": result.stdout.strip().splitlines()[-1] if result.stdout else "",
    }


def main() -> int:
    results = [_restart_drill(), _pytest_drill()]
    report = {
        "date": date.today().isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "all_ok": all(item["ok"] for item in results),
    }
    out_dir = ROOT / "docs" / "drill-records"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report['date']}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
