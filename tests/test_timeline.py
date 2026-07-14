"""Timeline Agent (CPM 关键路径) 单元测试。"""
from datetime import date, timedelta

from app.agents.timeline import TimelineAgent
from app.models.schemas import PlanOutput, SubTask


def _plan(tasks):
    return PlanOutput(tasks=tasks, summary="t")


def test_empty_plan():
    out = TimelineAgent().run(_plan([]), "2026-07-20")
    assert out.total_days == 0
    assert out.tasks == []


def test_single_task():
    out = TimelineAgent().run(
        _plan([SubTask(id="T1", name="A", estimated_hours=8)]),
        "2026-07-20",
    )
    assert out.total_days >= 1
    assert out.critical_path == ["T1"]
    assert out.tasks[0].end_date == date(2026, 7, 20)  # 截止日倒推


def test_linear_chain_critical_path():
    """T1 -> T2 -> T3 链式依赖，三者都在关键路径上。"""
    plan = _plan([
        SubTask(id="T1", name="A", estimated_hours=4),
        SubTask(id="T2", name="B", estimated_hours=4, dependencies=["T1"]),
        SubTask(id="T3", name="C", estimated_hours=4, dependencies=["T2"]),
    ])
    out = TimelineAgent().run(plan, "2026-07-20")
    assert out.critical_path == ["T1", "T2", "T3"]
    # T3 结束于截止日
    assert out.tasks[-1].end_date == date(2026, 7, 20)
    # 起始日 = 截止日 - 总天数（T1 从第 0 天开始）
    assert out.tasks[0].start_date == date(2026, 7, 20) - timedelta(days=out.total_days)


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


def test_hours_per_day_config():
    """hours_per_day 影响 工时→天数 折算。"""
    plan = _plan([SubTask(id="T1", name="A", estimated_hours=8)])
    fast = TimelineAgent().run(plan, "2026-07-20", hours_per_day=8.0)
    slow = TimelineAgent().run(plan, "2026-07-20", hours_per_day=2.0)
    assert fast.total_days <= slow.total_days


def test_assignments_backfill():
    plan = _plan([SubTask(id="T1", name="A", estimated_hours=4)])
    out = TimelineAgent().run(
        plan, "2026-07-20", assignments={"T1": ["张三", "李四"]}
    )
    assert out.tasks[0].assigned_to == ["张三", "李四"]