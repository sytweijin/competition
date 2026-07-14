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
    # 前端任务主讲应是前端哥
    t1 = next(a for a in out.assignments if a.task_id == "T1")
    assert t1.presenter == "前端哥"
    t2 = next(a for a in out.assignments if a.task_id == "T2")
    assert t2.presenter == "后端姐"
    # workload 非空
    assert set(out.workload.keys()) == {"前端哥", "后端姐"}


def test_assign_empty_returns_empty():
    out = assign_with_balance(PlanOutput(tasks=[], summary="t"), [])
    assert out.assignments == []


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