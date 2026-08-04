"""方案变更审计与版本回滚存储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import MEMORY_DIR

AUDIT_DIR = MEMORY_DIR / "audit"
VERSION_DIR = MEMORY_DIR / "versions"


def _safe_filename(filename: str) -> str:
    name = Path(filename or "").name
    if not name or name in (".", ".."):
        raise ValueError("非法文件名")
    return name


def save_version(
    plan_data: dict,
    filename: str,
    action: str = "保存",
    summary: str = "",
) -> str:
    """保存一份完整快照，并写一条审计记录。"""
    name = _safe_filename(filename)
    version_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        + "_" + uuid.uuid4().hex[:8]
    )
    version_dir = VERSION_DIR / name
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / f"{version_id}.json").write_text(
        json.dumps(plan_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "version_id": version_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "summary": summary,
    }
    with (AUDIT_DIR / f"{name}.jsonl").open("a", encoding="utf-8") as target:
        target.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return version_id


def list_versions(filename: str) -> list[dict]:
    name = _safe_filename(filename)
    path = AUDIT_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries


def load_version(filename: str, version_id: str) -> dict:
    name = _safe_filename(filename)
    safe_id = Path(version_id or "").name
    path = VERSION_DIR / name / f"{safe_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"版本不存在：{version_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def rollback_plan(filename: str, version_id: str) -> tuple[str, dict]:
    data = load_version(filename, version_id)
    name = _safe_filename(filename)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(name).stem
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    new_filename = f"{ts}_{stem}_rollback.json"
    filepath = MEMORY_DIR / new_filename
    n = 1
    while filepath.exists():
        new_filename = f"{ts}_{stem}_rollback_{n}.json"
        filepath = MEMORY_DIR / new_filename
        n += 1
    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_version(
        data,
        new_filename,
        action="回滚",
        summary=f"从 {name} 回滚到版本 {version_id}",
    )
    return new_filename, data
