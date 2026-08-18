from datetime import datetime
"""Timeline Agent (CPM 关键路径) 单元测试。"""
from datetime import date, timedelta

from app.agents.timeline import TimelineAgent
from unittest.mock import patch
import datetime as _dt

FIXED_TODAY = _dt.date(2026, 7, 16)

class _FakeDate:
    @classmethod
    def today(cls):
        return FIXED_TODAY
    @staticmethod
    def fromisoformat(s):
        return _dt.date.fromisoformat(s)
from app.models.schemas import PlanOutput, SubTask, TeamMember


def _plan(tasks):
    return PlanOutput(tasks=tasks, summary="t")


@patch('app.agents.timeline.date', new=_FakeDate)
def test_empty_plan():
    out = TimelineAgent().run(_plan([]), "2026-07-20")
    assert out.total_days == 0
    assert out.tasks == []


@patch('app.config.today', return_value=_FakeDate.today())
@patch('app.agents.timeline.date', new=_FakeDate)
def test_single_task(mock_today):
    out = TimelineAgent().run(
        _plan([SubTask(id="T1", name="A", estimated_hours=8)]),
        "2026-07-20",
    )
    assert out.total_days >= 1
    assert out.critical_path == ["T1"]
    assert out.tasks[0].end_date == datetime(2026, 7, 20)  # 截止日倒推


@patch('app.config.today', return_value=_FakeDate.today())
@patch('app.agents.timeline.date', new=_FakeDate)
def test_linear_chain_critical_path(mock_today):
    """T1 -> T2 -> T3 链式依赖，三者都在关键路径上。"""
    plan = _plan([
        SubTask(id="T1", name="A", estimated_hours=4),
        SubTask(id="T2", name="B", estimated_hours=4, dependencies=["T1"]),
        SubTask(id="T3", name="C", estimated_hours=4, dependencies=["T2"]),
    ])
    out = TimelineAgent().run(plan, "2026-07-20")
    assert out.critical_path == ["T1", "T2", "T3"]
    # T3 结束于截止日
    assert out.tasks[-1].end_date == datetime(2026, 7, 20)
    # 起始日 = 截止日往前推 (total_days - 1) 个工作日（跳过周末）
    from app.agents.timeline import _sub_work_days
    expected_start = datetime.combine(
        _sub_work_days(_dt.date(2026, 7, 20), out.total_days - 1),
        _dt.datetime.min.time())
    assert out.tasks[0].start_date == expected_start


def test_parallel_task_has_float():
    """并行分支任务应有浮动天数，不在关键路径。"""
    plan = _plan([
        SubTask(id="T1", name="长任务", estimated_hours=16),  # 4 天
        SubTask(id="T2", name="短任务", estimated_hours=4),   # 1 天
    ])
    out = TimelineAgent().run(plan, "2026-07-20")
    # 关键路径应包含工时更长的 T1
    assert "T1" in out.critical_path
    short = next(t for t in out.tasks if t.task_id == "T2")
    long = next(t for t in out.tasks if t.task_id == "T1")
    assert long.is_critical
    assert not short.is_critical
    assert short.float_days > 0


def test_parallel_paths_have_one_explicit_critical_path():
    """两条并行路径汇合时，较长分支及汇合点构成关键路径。"""
    plan = _plan([
        SubTask(id="T1", name="长分支一", estimated_hours=8),
        SubTask(id="T2", name="长分支二", estimated_hours=8,
                dependencies=["T1"]),
        SubTask(id="T3", name="短分支", estimated_hours=4),
        SubTask(id="T4", name="汇合", estimated_hours=4,
                dependencies=["T2", "T3"]),
    ])
    out = TimelineAgent().run(plan, "2026-08-20")
    assert out.critical_path == ["T1", "T2", "T4"]
    assert next(t for t in out.tasks if t.task_id == "T3").float_days > 0


def test_independent_equal_tasks_are_all_critical():
    """无依赖且等长的任务都决定项目最短工期。"""
    plan = _plan([
        SubTask(id="T1", name="A", estimated_hours=4),
        SubTask(id="T2", name="B", estimated_hours=4),
        SubTask(id="T3", name="C", estimated_hours=4),
    ])
    out = TimelineAgent().run(plan, "2026-08-20")
    assert out.critical_path == ["T1", "T2", "T3"]
    assert all(task.float_days == 0 for task in out.tasks)


def test_cycle_is_tolerated():
    """依赖环不应崩溃，应断环继续排期。"""
    plan = _plan([
        SubTask(id="T1", name="A", estimated_hours=4, dependencies=["T2"]),
        SubTask(id="T2", name="B", estimated_hours=4, dependencies=["T1"]),
    ])
    out = TimelineAgent().run(plan, "2026-07-20")
    # 两个任务都被排出（断环）
    assert len(out.tasks) == 2
    assert "环" in out.note or "环" in out.reasoning
    assert len({task.task_id for task in out.tasks}) == 2


def test_daily_capacity_affects_duration():
    """成员每日可用工时影响 工时→天数 折算。"""
    from app.models.schemas import TeamMember
    plan = _plan([SubTask(id="T1", name="A", estimated_hours=8)])
    fast = TimelineAgent().run(
        plan, "2026-07-20", assignments={"T1": ["A"]},
        members=[TeamMember(name="A", daily_available_hours=8.0)],
    )
    slow = TimelineAgent().run(
        plan, "2026-07-20", assignments={"T1": ["A"]},
        members=[TeamMember(name="A", daily_available_hours=2.0)],
    )
    assert fast.total_days <= slow.total_days


def test_assignments_backfill():
    plan = _plan([SubTask(id="T1", name="A", estimated_hours=4)])
    out = TimelineAgent().run(
        plan, "2026-07-20", assignments={"T1": ["张三", "李四"]}
    )
    assert out.tasks[0].assigned_to == ["张三", "李四"]


def test_same_owner_tasks_are_serialized_but_different_owners_can_parallel():
    """甘特图必须体现分工资源：同一负责人串行，不同负责人仍可并行。"""
    plan = _plan([
        SubTask(id="T1", name="资料整理", estimated_hours=4),
        SubTask(id="T2", name="文案撰写", estimated_hours=4),
        SubTask(id="T3", name="视觉设计", estimated_hours=4),
    ])
    out = TimelineAgent().run(
        plan,
        "2026-08-20",
        assignments={"T1": ["小林"], "T2": ["小林"], "T3": ["小陈"]},
        members=[
            TeamMember(name="小林", daily_available_hours=4),
            TeamMember(name="小陈", daily_available_hours=4),
        ],
    )
    by_id = {task.task_id: task for task in out.tasks}

    assert by_id["T2"].start_date > by_id["T1"].end_date
    assert by_id["T3"].start_date == by_id["T1"].start_date
    assert "资源顺序约束" in out.reasoning
