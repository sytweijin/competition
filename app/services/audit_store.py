"""方案版本树、差异对比与回滚存储。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from app.config import MEMORY_DIR

AUDIT_DIR = MEMORY_DIR / "audit"
VERSION_DIR = MEMORY_DIR / "versions"
SIMILARITY_THRESHOLD = 0.58


def _safe_filename(filename: str) -> str:
    name = Path(filename or "").name
    if not name or name in (".", ".."):
        raise ValueError("非法文件名")
    return name


def _safe_version_id(version_id: str) -> str:
    value = Path(version_id or "").name
    if not value or value in (".", ".."):
        raise ValueError("非法版本号")
    return value


def _normalize_text(value: object) -> str:
    text = str(value or "").lower().strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _plan_profile(plan_data: dict) -> dict:
    input_data = plan_data.get("input") or {}
    course = input_data.get("course") or {}
    tasks = (plan_data.get("plan") or {}).get("tasks") or []
    task_names = sorted({
        name for name in (_normalize_text(task.get("name")) for task in tasks)
        if name
    })
    project_name = str(course.get("name") or "未命名项目").strip()
    fingerprint_source = "|".join([_normalize_text(project_name), *task_names])
    return {
        "project_name": project_name,
        "normalized_name": _normalize_text(project_name),
        "task_names": task_names,
        "task_count": len(tasks),
        "task_fingerprint": hashlib.sha1(
            fingerprint_source.encode("utf-8")
        ).hexdigest()[:16],
    }


def _profile_similarity(left: dict, right: dict) -> float:
    left_name = left.get("normalized_name", "")
    right_name = right.get("normalized_name", "")
    name_score = SequenceMatcher(None, left_name, right_name).ratio()
    left_tasks = set(left.get("task_names") or [])
    right_tasks = set(right.get("task_names") or [])
    if left_tasks or right_tasks:
        task_score = len(left_tasks & right_tasks) / max(1, len(left_tasks | right_tasks))
    else:
        task_score = 0.0
    # 项目名决定版本族，任务集合用于识别改名或另存后的相似方案。
    return round(name_score * 0.62 + task_score * 0.38, 4)


def _read_raw_entries(filename: str) -> list[dict]:
    name = _safe_filename(filename)
    path = AUDIT_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("version_id"):
            entries.append(entry)
    return entries


def _enrich_entries(filename: str, entries: list[dict]) -> list[dict]:
    """为旧版平铺日志补齐父节点和版本族字段，不改写原文件。"""
    name = _safe_filename(filename)
    enriched = []
    previous = None
    for raw in entries:
        entry = dict(raw)
        version_id = entry["version_id"]
        parent_id = entry.get("parent_version_id")
        if parent_id is None and previous:
            parent_id = previous["version_id"]
        root_id = entry.get("root_version_id")
        if not root_id:
            root_id = previous.get("root_version_id") if previous else version_id
        family_id = entry.get("family_id") or f"tree_{root_id}"
        profile = {}
        try:
            profile = _plan_profile(load_version(name, version_id))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            pass
        entry.update({
            "filename": entry.get("filename") or name,
            "parent_version_id": parent_id,
            "parent_filename": entry.get("parent_filename") or (
                previous.get("filename") if parent_id and previous else None
            ),
            "root_version_id": root_id,
            "family_id": family_id,
            "project_name": entry.get("project_name") or profile.get("project_name", ""),
            "task_fingerprint": (
                entry.get("task_fingerprint") or profile.get("task_fingerprint", "")
            ),
            "task_count": entry.get("task_count", profile.get("task_count", 0)),
        })
        enriched.append(entry)
        previous = entry
    return enriched


def _all_filenames() -> list[str]:
    if not AUDIT_DIR.exists():
        return []
    suffix = ".jsonl"
    return sorted({
        path.name[:-len(suffix)]
        for path in AUDIT_DIR.glob(f"*{suffix}")
        if path.name.endswith(suffix)
    })


def _find_version_entry(version_id: str, filename: str = "") -> dict | None:
    safe_id = _safe_version_id(version_id)
    names = [_safe_filename(filename)] if filename else _all_filenames()
    for name in names:
        for entry in list_versions(name):
            if entry["version_id"] == safe_id:
                return entry
    return None


def _similar_parent(plan_data: dict, exclude_filename: str) -> tuple[dict | None, float]:
    profile = _plan_profile(plan_data)
    best_entry = None
    best_score = 0.0
    for name in _all_filenames():
        if name == exclude_filename:
            continue
        versions = list_versions(name)
        if not versions:
            continue
        latest = versions[0]
        try:
            candidate_profile = _plan_profile(load_version(name, latest["version_id"]))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            continue
        score = _profile_similarity(profile, candidate_profile)
        if score > best_score:
            best_entry, best_score = latest, score
    if best_score < SIMILARITY_THRESHOLD:
        return None, best_score
    return best_entry, best_score


def save_version(
    plan_data: dict,
    filename: str,
    action: str = "保存",
    summary: str = "",
    parent_version_id: str | None = None,
    parent_filename: str | None = None,
) -> str:
    """保存完整快照并写入带父节点信息的版本树记录。"""
    name = _safe_filename(filename)
    version_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        + "_" + uuid.uuid4().hex[:8]
    )
    current_versions = list_versions(name)
    parent = None
    similarity = 1.0
    if parent_version_id:
        parent = _find_version_entry(parent_version_id, parent_filename or name)
    elif current_versions:
        parent = current_versions[0]
    else:
        parent, similarity = _similar_parent(plan_data, name)

    profile = _plan_profile(plan_data)
    parent_id = parent.get("version_id") if parent else None
    parent_name = parent.get("filename") if parent else None
    root_id = parent.get("root_version_id") if parent else version_id
    family_id = parent.get("family_id") if parent else f"tree_{version_id}"

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
        "filename": name,
        "parent_version_id": parent_id,
        "parent_filename": parent_name,
        "root_version_id": root_id,
        "family_id": family_id,
        "similarity_to_parent": similarity if parent else None,
        "project_name": profile["project_name"],
        "task_fingerprint": profile["task_fingerprint"],
        "task_count": profile["task_count"],
    }
    with (AUDIT_DIR / f"{name}.jsonl").open("a", encoding="utf-8") as target:
        target.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return version_id


def list_versions(filename: str) -> list[dict]:
    entries = _enrich_entries(filename, _read_raw_entries(filename))
    entries.reverse()
    return entries


def load_version(filename: str, version_id: str) -> dict:
    name = _safe_filename(filename)
    safe_id = _safe_version_id(version_id)
    path = VERSION_DIR / name / f"{safe_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"版本不存在：{version_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_version_tree(
    filename: str,
    allowed_filenames: set[str] | None = None,
) -> dict:
    """返回当前方案及相似任务方案的版本树。"""
    name = _safe_filename(filename)
    target_versions = list_versions(name)
    if not target_versions:
        return {"nodes": [], "current_version_id": None, "similar_projects": []}
    target_profile = _plan_profile(
        load_version(name, target_versions[0]["version_id"])
    )
    target_families = {
        entry.get("family_id") for entry in target_versions if entry.get("family_id")
    }
    included: list[tuple[str, float]] = [(name, 1.0)]
    for candidate in _all_filenames():
        if candidate == name:
            continue
        if allowed_filenames is not None and candidate not in allowed_filenames:
            continue
        versions = list_versions(candidate)
        if not versions:
            continue
        try:
            profile = _plan_profile(load_version(candidate, versions[0]["version_id"]))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            continue
        score = _profile_similarity(target_profile, profile)
        shares_family = any(
            entry.get("family_id") in target_families for entry in versions
        )
        if shares_family or score >= SIMILARITY_THRESHOLD:
            included.append((candidate, score))

    nodes = []
    for candidate, score in included:
        for entry in reversed(list_versions(candidate)):
            node = dict(entry)
            node["similarity"] = score
            node["is_current_file"] = candidate == name
            nodes.append(node)
    nodes.sort(key=lambda item: (item.get("timestamp", ""), item["version_id"]))

    by_id = {node["version_id"]: node for node in nodes}
    depth_cache: dict[str, int] = {}

    def depth(node: dict, trail: set[str] | None = None) -> int:
        version_id = node["version_id"]
        if version_id in depth_cache:
            return depth_cache[version_id]
        trail = set(trail or ())
        if version_id in trail:
            return 0
        trail.add(version_id)
        parent = by_id.get(node.get("parent_version_id"))
        result = depth(parent, trail) + 1 if parent else 0
        depth_cache[version_id] = result
        return result

    child_counts: dict[str, int] = {}
    for node in nodes:
        parent_id = node.get("parent_version_id")
        if parent_id:
            child_counts[parent_id] = child_counts.get(parent_id, 0) + 1
    for node in nodes:
        node["depth"] = depth(node)
        node["child_count"] = child_counts.get(node["version_id"], 0)

    similar_projects = [
        {"filename": candidate, "similarity": score}
        for candidate, score in included if candidate != name
    ]
    return {
        "nodes": nodes,
        "current_version_id": target_versions[0]["version_id"],
        "current_filename": name,
        "similar_projects": similar_projects,
    }


def _task_map(plan_data: dict) -> dict[str, dict]:
    tasks = (plan_data.get("plan") or {}).get("tasks") or []
    mapped = {}
    for task in tasks:
        key = _normalize_text(task.get("name")) or str(task.get("id") or "")
        mapped[key] = task
    return mapped


def _member_map(plan_data: dict) -> dict[str, dict]:
    members = (plan_data.get("input") or {}).get("members") or []
    return {str(member.get("name") or ""): member for member in members}


def _changed_fields(left: dict, right: dict, fields: list[str]) -> list[dict]:
    changes = []
    for field in fields:
        old_value = left.get(field)
        new_value = right.get(field)
        if isinstance(old_value, list):
            old_value = sorted(old_value)
        if isinstance(new_value, list):
            new_value = sorted(new_value)
        if old_value != new_value:
            changes.append({"field": field, "before": old_value, "after": new_value})
    return changes


def compare_versions(
    left_filename: str,
    left_version_id: str,
    right_filename: str,
    right_version_id: str,
) -> dict:
    """比较两个版本的项目字段、成员和任务差异。"""
    left = load_version(left_filename, left_version_id)
    right = load_version(right_filename, right_version_id)
    left_tasks = _task_map(left)
    right_tasks = _task_map(right)
    added = [
        right_tasks[key]
        for key in sorted(right_tasks.keys() - left_tasks.keys())
    ]
    removed = [
        left_tasks[key]
        for key in sorted(left_tasks.keys() - right_tasks.keys())
    ]
    task_changes = []
    task_fields = [
        "name", "description", "estimated_hours", "actual_hours", "assignee_id",
        "collaborator_ids", "dependencies", "execution_stage", "status",
        "start_date", "end_date", "module_id", "extra_helpers_needed",
    ]
    for key in sorted(left_tasks.keys() & right_tasks.keys()):
        changes = _changed_fields(left_tasks[key], right_tasks[key], task_fields)
        if changes:
            task_changes.append({
                "task_id": right_tasks[key].get("id") or left_tasks[key].get("id"),
                "task_name": right_tasks[key].get("name") or left_tasks[key].get("name"),
                "changes": changes,
            })

    left_members = _member_map(left)
    right_members = _member_map(right)
    member_changes = []
    for name in sorted(left_members.keys() & right_members.keys()):
        changes = _changed_fields(
            left_members[name], right_members[name],
            ["role", "manager", "daily_available_hours", "skill_tags", "unavailable_dates"],
        )
        if changes:
            member_changes.append({"name": name, "changes": changes})

    left_input = left.get("input") or {}
    right_input = right.get("input") or {}
    project_changes = _changed_fields(
        {
            "project_name": (left_input.get("course") or {}).get("name"),
            "description": (left_input.get("course") or {}).get("description"),
            "deadline": left_input.get("deadline"),
            "requirements": left_input.get("additional_requirements"),
        },
        {
            "project_name": (right_input.get("course") or {}).get("name"),
            "description": (right_input.get("course") or {}).get("description"),
            "deadline": right_input.get("deadline"),
            "requirements": right_input.get("additional_requirements"),
        },
        ["project_name", "description", "deadline", "requirements"],
    )
    return {
        "left": {"filename": left_filename, "version_id": left_version_id},
        "right": {"filename": right_filename, "version_id": right_version_id},
        "summary": {
            "project_fields_changed": len(project_changes),
            "tasks_added": len(added),
            "tasks_removed": len(removed),
            "tasks_changed": len(task_changes),
            "members_added": len(right_members.keys() - left_members.keys()),
            "members_removed": len(left_members.keys() - right_members.keys()),
            "members_changed": len(member_changes),
        },
        "project_changes": project_changes,
        "tasks": {"added": added, "removed": removed, "changed": task_changes},
        "members": {
            "added": sorted(right_members.keys() - left_members.keys()),
            "removed": sorted(left_members.keys() - right_members.keys()),
            "changed": member_changes,
        },
    }


def rollback_plan(
    filename: str,
    version_id: str,
    source_filename: str = "",
) -> tuple[str, dict]:
    """把目标版本恢复为当前文件的新分支节点。"""
    name = _safe_filename(filename)
    source_name = _safe_filename(source_filename or filename)
    data = load_version(source_name, version_id)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    filepath = MEMORY_DIR / name
    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_version(
        data,
        name,
        action="回滚",
        summary=f"从 {source_name} 回滚到版本 {version_id}",
        parent_version_id=version_id,
        parent_filename=source_name,
    )
    return name, data
