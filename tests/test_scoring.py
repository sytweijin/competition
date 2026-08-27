"""B3 技能评分 + 确定性匹配 单元测试。"""
from app.agents.scoring import assign_with_balance, enhance, skill_score
from app.models.schemas import (
    PlanOutput, QAAssignment, QAOutput, SubTask, TeamMember,
)


def test_skill_score_exact_match():
    m = TeamMember(name="A", skill_tags=["前端", "python"])
    assert skill_score(m, ["前端"]) == 1.0


def test_skill_score_no_skills():
    m = TeamMember(name="A", skill_tags=[])
    # 没有技能标签时返回中性分数 0.0
    assert skill_score(m, ["前端"]) == 0.0


def test_skill_score_partial():
    m = TeamMember(name="A", skill_tags=["Python"])
    s = skill_score(m, ["python3"])  # 相似但不完全
    assert 0 < s < 1


def test_skill_score_no_requirements_is_neutral():
    m = TeamMember(name="A", skill_tags=["前端"])
    assert skill_score(m, []) == 0.5


def test_assign_with_balance_distributes():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="前端", estimated_hours=4, required_skills=["前端"]),
        SubTask(id="T2", name="后端", estimated_hours=4, required_skills=["后端"]),
    ], summary="t")
    members = [
        TeamMember(name="前端哥", skill_tags=["前端"]),
        TeamMember(name="后端姐", skill_tags=["后端"]),
    ]
    out = assign_with_balance(plan, members)
    assert len(out.assignments) == 2
    # 前端任务负责人应是前端哥
    t1 = next(a for a in out.assignments if a.task_id == "T1")
    assert t1.presenter == "前端哥"
    t2 = next(a for a in out.assignments if a.task_id == "T2")
    assert t2.presenter == "后端姐"
    # workload 非空
    assert set(out.workload.keys()) == {"前端哥", "后端姐"}


def test_assign_empty_returns_empty():
    out = assign_with_balance(PlanOutput(tasks=[], summary="t"), [])
    assert out.assignments == []


def test_default_assignment_keeps_gap_within_two_hours_when_possible():
    plan = PlanOutput(tasks=[
        SubTask(id=f"T{i}", name=f"通用任务{i}", estimated_hours=4)
        for i in range(1, 7)
    ], summary="t")
    members = [TeamMember(name=name) for name in ("A", "B", "C")]
    out = assign_with_balance(plan, members)
    gap = max(out.workload.values()) - min(out.workload.values())
    assert gap <= 2.0


def test_impossible_two_hour_balance_returns_split_suggestion():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="大任务", estimated_hours=12,
                required_skills=["专项技能"]),
        SubTask(id="T2", name="小任务", estimated_hours=2),
    ], summary="t")
    members = [
        TeamMember(name="A", skill_tags=["专项技能"]),
        TeamMember(name="B"),
        TeamMember(name="C"),
    ]
    out = assign_with_balance(plan, members)
    gap = max(out.workload.values()) - min(out.workload.values())
    assert gap > 2.0
    assert "超过 1h" in out.note
    assert "建议拆分" in out.note


def test_balance_does_not_assign_avoided_skill_to_presenter():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="制作PPT", estimated_hours=8,
                required_skills=["PPT"]),
        SubTask(id="T2", name="整理资料", estimated_hours=2),
    ], summary="t")
    members = [
        TeamMember(name="A", skill_tags=["PPT"]),
        TeamMember(name="B", skill_tags=["不想做PPT"]),
    ]
    out = assign_with_balance(plan, members)
    ppt = next(item for item in out.assignments if item.task_id == "T1")
    assert ppt.presenter == "A"
    assert "B" not in (ppt.qa_primary, *(ppt.qa_support or []))


def test_enhance_keeps_llm_picks_adds_scores():
    plan = PlanOutput(tasks=[
        SubTask(id="T1", name="前端", estimated_hours=8, required_skills=["前端"]),
    ], summary="t")
    members = [TeamMember(name="张三", skill_tags=["前端"])]
    qa = QAOutput(assignments=[QAAssignment(
        task_id="T1", task_name="前端", presenter="张三", qa_primary="张三",
    )])
    out = enhance(qa, plan, members)
    a = out.assignments[0]
    assert a.score == 1.0
    assert out.workload["张三"] >= 8.0
