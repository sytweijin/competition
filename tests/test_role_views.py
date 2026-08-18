"""工作台角色化视图的静态契约测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workbench_exposes_three_audience_roles_and_member_picker():
    html = (ROOT / "app/web/templates/index.html").read_text(encoding="utf-8")

    assert 'data-audience-role="manager"' in html
    assert 'data-audience-role="member"' in html
    assert 'data-audience-role="reviewer"' in html
    assert 'id="audienceMemberPicker"' in html
    assert 'id="audienceSummary"' in html


def test_role_tabs_and_views_remain_bound_to_the_same_plan_state():
    script = (ROOT / "app/web/static/app.js").read_text(encoding="utf-8")

    assert "function audienceTabs(role,defenseVisible)" in script
    assert "function renderMemberTasks()" in script
    assert "function renderEvaluationView()" in script
    assert "state.plan.plan.tasks" in script
    assert "state.audienceRole==='member'" in script


def test_reviewer_view_uses_evidence_without_subjective_score():
    script = (ROOT / "app/web/static/app.js").read_text(encoding="utf-8")

    assert "分工完整度" in script
    assert "平均匹配度" in script
    assert "关键任务" in script
    assert "不生成主观评分" in script

