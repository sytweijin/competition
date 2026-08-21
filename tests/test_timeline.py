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


@patch('app.config.today', return_value=_dt.date(2026, 7, 13))
def test_weekend_deadline_finishes_on_previous_workday(mock_today):
    """周末截止日不能把任务错误排到下周一。"""
    out = TimelineAgent().run(
        _plan([SubTask(id="T1", name="交付", estimated_hours=4)]),
        "2026-07-19",  # Sunday
    )
    assert out.tasks[0].end_date == datetime(2026, 7, 17)


@patch('app.config.today', return_value=_dt.date(2026, 7, 13))
def test_half_day_offsets_are_monotonic_and_exported(mock_today):
    """半天链路应保持 0、0.5、1.0 的稳定偏移，供甘特图精确绘制。"""
    out = TimelineAgent().run(
        _plan([
            SubTask(id="T1", name="A", estimated_hours=2),
            SubTask(id="T2", name="B", estimated_hours=2,
                    dependencies=["T1"]),
            SubTask(id="T3", name="C", estimated_hours=2,
                    dependencies=["T2"]),
        ]),
        "2026-07-17",
    )
    assert [task.start_offset_days for task in out.tasks] == [0, .5, 1]
    assert [task.duration_days for task in out.tasks] == [.5, .5, .5]
    assert out.tasks[0].start_date == out.tasks[1].start_date
    assert out.tasks[2].start_date > out.tasks[1].start_date


@patch('app.config.today', return_value=_dt.date(2026, 8, 10))
def test_start_date_never_lands_on_unavailable_day(mock_today):
    """起始日本身不可用时，任务必须后移到下一个可用工作日，而不是压在不可用日上。"""
    plan = _plan([SubTask(id="T1", name="交付", estimated_hours=8)])
    out = TimelineAgent().run(
        plan,
        "2026-08-14",
        assignments={"T1": ["小文"]},
        members=[TeamMember(
            name="小文", daily_available_hours=4,
            unavailable_dates=[
                _dt.date(2026, 8, 11), _dt.date(2026, 8, 12),
                _dt.date(2026, 8, 13), _dt.date(2026, 8, 14),
            ],
        )],
    )
    s, e = out.tasks[0].start_date.date(), out.tasks[0].end_date.date()
    # 8-11~8-14 全部不可用，任务应整体后移（8-17~8-18，跳过周末）
    assert s == _dt.date(2026, 8, 17)
    assert e == _dt.date(2026, 8, 18)
    assert all(
        d not in {_dt.date(2026, 8, 11), _dt.date(2026, 8, 12),
                  _dt.date(2026, 8, 13), _dt.date(2026, 8, 14)}
        for d in [s, e]
    )


@patch('app.config.today', return_value=_dt.date(2026, 8, 10))
def test_multi_day_task_skips_mid_span_unavailable_day(mock_today):
    """多日任务窗口中间的不可用日应被跳过并拉长窗口，而不是包进排期。"""
    plan = _plan([SubTask(id="T1", name="长任务", estimated_hours=32)])
    out = TimelineAgent().run(
        plan,
        "2026-08-25",
        assignments={"T1": ["小文"]},
        members=[TeamMember(
            name="小文", daily_available_hours=4,
            unavailable_dates=[_dt.date(2026, 8, 13)],
        )],
    )
    s, e = out.tasks[0].start_date.date(), out.tasks[0].end_date.date()
    # 32h / 4h/天 = 8 个工作日；从 8-14 起排（8-16/8-17 周末不计），
    # 8 个工作日依次为 14、15、18、19、20、21、22、25
    assert s == _dt.date(2026, 8, 14)
    assert e == _dt.date(2026, 8, 25)


def test_sync_task_dates_backfills_timeline_dates():
    """时间线日期必须回填到任务自身，否则资源日历会读到草案默认窗口。"""
    from app.agents.timeline import sync_task_dates
    from app.models.schemas import TimelineOutput, TimelineTask
    plan = _plan([SubTask(
        id="T1", name="A", estimated_hours=8,
        start_date=_dt.date(2026, 8, 1), end_date=_dt.date(2026, 8, 10),
    )])
    timeline = TimelineOutput(
        tasks=[TimelineTask(
            task_id="T1", name="A",
            start_date=datetime(2026, 8, 17, 0, 0),
            end_date=datetime(2026, 8, 18, 0, 0),
            start_offset_days=0.0, duration_days=1.0,
            is_critical=True, float_days=0.0,
            assigned_to=["小文"], status="pending",
        )],
        critical_path=["T1"], total_days=1,
    )
    synced = sync_task_dates(plan, timeline)
    assert synced.tasks[0].start_date == _dt.date(2026, 8, 17)
    assert synced.tasks[0].end_date == _dt.date(2026, 8, 18)
