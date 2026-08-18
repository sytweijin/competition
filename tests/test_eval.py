"""E1-E3：评测集 schema 与评测脚本可执行性。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_eval_cases_have_required_schema():
    cases = json.loads(
        (ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
    assert 10 <= len(cases) <= 20
    for case in cases:
        assert case["id"].startswith("E")
        assert case["name"]
        assert case["input"]["course"]["name"]
        assert case["input"]["members"]
        assert case["input"]["deadline"]
        assert case["hard_requirements"]
        assert case["human_reference"]["task_count"] > 0
        assert case["human_reference"]["expected_task_names"]


def test_evaluate_case_returns_all_metrics():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from run_eval import evaluate_case, load_cases

    result = evaluate_case(load_cases()[0])
    for key in (
        "id", "task_count", "total_hours", "recognition_rate",
        "generation_ms", "load_cv", "baseline_load_cv",
        "load_improvement", "reschedule_ms", "critical_path_count",
    ):
        assert key in result
    assert result["recognition_rate"] > 0
