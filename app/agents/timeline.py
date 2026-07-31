"""
Timeline Agent
负责人：B
负责：倒排时间线 + 关键路径(CPM)计算。

纯算法，不依赖 LLM。用拓扑排序 + Forward/Backward pass 计算关键路径，
从截止日倒推起始日。

v0.4 改进：
- CPM 以「半天」为最小粒度（内部用 half-day 整数运算），小任务不再被强制占满 1 整天
- 相邻任务日期不再重叠（结束日 = 开始日 + 工期 - 1 天，符合自然日语义）
- 起始日不会排到过去（若倒推后早于今天，则从今天起正排并提示延期）
- 支持按任务负责人各自的每日可用工时折算
"""

from __future__ import annotations

import math
from collections import deque
from datetime import date, datetime, timedelta
from app import config

from app.agents.base import BaseAgent
from app.models.schemas import (
    PlanOutput, TimelineOutput, TimelineTask, TeamMember,
)

DEFAULT_HOURS_PER_DAY = 4.0


def _is_weekend(d: date) -> bool:
    """周六(5)或周日(6)。"""
    return d.weekday() >= 5


def _next_workday(d: date) -> date:
    """如果 d 是周末，前进到下一个周一。"""
    while _is_weekend(d):
        d += timedelta(days=1)
    return d


def _add_work_days(start: date, days: int, skip_dates: set[date] | None = None) -> date:
    """从 start 前进 days 个工作日（跳过周末和 skip_dates 中的日期）。days >= 0。"""
    skip_dates = skip_dates or set()
    d = start
    for _ in range(max(0, days)):
        d += timedelta(days=1)
        while _is_weekend(d) or d in skip_dates:
            d += timedelta(days=1)
    return d


def _sub_work_days(end: date, days: int, skip_dates: set[date] | None = None) -> date:
    """从 end 后退 days 个工作日（跳过周末和 skip_dates 中的日期）。days >= 0。"""
    skip_dates = skip_dates or set()
    d = end
    for _ in range(max(0, days)):
        d -= timedelta(days=1)
        while _is_weekend(d) or d in skip_dates:
            d -= timedelta(days=1)
    return d


def _count_work_days(start: date, end: date, skip_dates: set[date] | None = None) -> int:
    """计算 [start, end] 闭区间内的工作日数（跳过周末和 skip_dates）。"""
    skip_dates = skip_dates or set()
    if start > end:
        return 0
    count = 0
    d = start
    while d <= end:
        if not _is_weekend(d) and d not in skip_dates:
            count += 1
        d += timedelta(days=1)
    return count


class TimelineAgent(BaseAgent[TimelineOutput]):
    """CPM 关键路径 Agent（纯算法，不实例化 LLM）。"""

    system_prompt = ""
    response_model = None  # 不用 LLM

    def __init__(self, llm=None):
        self.llm = llm

    def run(self, plan: PlanOutput, deadline: str,
            assignments: dict[str, list[str]] | None = None,
            members: list[TeamMember] | None = None) -> TimelineOutput:
        """根据任务依赖和工时，用 CPM 算法生成倒排时间线。

        Args:
            plan: Planner 输出的计划。
            deadline: 截止日期 ISO 字符串。
            assignments: {task_id: [成员名]}，可选，用于回填负责人。
            members: 团队成员列表，用于获取每人每天可用工时。
        """
        deadline_date = date.fromisoformat(deadline)
        tasks = plan.tasks
        assignments = assignments or {}

        # 构建成员名→每日可用工时的映射
        member_daily: dict[str, float] = {}
        # 构建成员名→不可用日期集合的映射
        member_unavailable: dict[str, set[date]] = {}
        if members:
            for m in members:
                member_daily[m.name] = max(0.5, m.daily_available_hours)
                member_unavailable[m.name] = set(m.unavailable_dates or [])

        if member_daily:
            global_daily = sum(member_daily.values()) / len(member_daily)
        else:
            global_daily = DEFAULT_HOURS_PER_DAY

        # 边界：空计划
        if not tasks:
            return TimelineOutput(
                tasks=[], critical_path=[], total_days=0,
                note="没有任务，无法生成时间线",
                reasoning="输入计划为空。",
            )

        # 构建邻接表
        task_map = {t.id: t for t in tasks}
        valid_ids = set(task_map)
        successors: dict[str, list[str]] = {t.id: [] for t in tasks}
        predecessors: dict[str, list[str]] = {t.id: [] for t in tasks}
        for t in tasks:
            for dep in t.dependencies:
                if dep in valid_ids and dep != t.id:
                    successors[dep].append(t.id)
                    predecessors[t.id].append(dep)

        # 拓扑排序（Kahn）
        in_degree = {t.id: len(predecessors[t.id]) for t in tasks}
        queue = deque(sorted(tid for tid, d in in_degree.items() if d == 0))
        topo_order: list[str] = []
        broken_cycle = False
        local_in = dict(in_degree)
        while queue:
            tid = queue.popleft()
            topo_order.append(tid)
            for s in sorted(successors[tid]):
                local_in[s] -= 1
                if local_in[s] == 0:
                    queue.append(s)

        # 环容错：把不在 topo_order 里的任务追加到末尾（断开其入环依赖）
        if len(topo_order) != len(tasks):
            broken_cycle = True
            remaining = [tid for tid in task_map if tid not in set(topo_order)]
            for tid in remaining:
                predecessors[tid] = []
                successors[tid] = []
            topo_order.extend(remaining)

        # 工时→工期折算：以「半天」为最小粒度（half-day 单位，1 个单位 = 0.5 天）
        # 内部 CPM 全部用 half-day 整数运算，避免浮点误差。
        def _task_daily_capacity(task_id: str) -> float:
            """根据任务负责人计算每天可用工时。
            主讲(first assigned)按全产能，其他参与者按0.5折算(部分并行假设)。"""
            assigned = assignments.get(task_id, [])
            if not assigned or not member_daily:
                return global_daily
            if len(assigned) == 1:
                return max(0.5, member_daily.get(assigned[0], global_daily))
            capacity = member_daily.get(assigned[0], global_daily)
            for name in assigned[1:]:
                capacity += 0.5 * member_daily.get(name, global_daily)
            return max(0.5, capacity)

        durations: dict[str, int] = {}  # 单位：half-day（0.5 天）
        completed_ids: set[str] = set()
        for t in tasks:
            # 已完成的任务不占排期（工期为 0），其后续任务可立即接上
            if t.status == "completed":
                durations[t.id] = 0
                completed_ids.add(t.id)
                continue
            daily_cap = _task_daily_capacity(t.id)
            days_needed = t.estimated_hours / daily_cap
            # 转成 half-day 单位并向上取整，最少 1 个 half-day（即 0.5 天）
            durations[t.id] = max(1, math.ceil(days_needed * 2))

        # Forward pass: 最早开始/结束（单位：half-day）
        es: dict[str, int] = {}
        ef: dict[str, int] = {}
        for tid in topo_order:
            if not predecessors[tid]:
                es[tid] = 0
            else:
                es[tid] = max(ef[p] for p in predecessors[tid])
            ef[tid] = es[tid] + durations[tid]

        project_half_days = max(ef.values()) if ef else 0

        # Backward pass: 最晚开始/结束（单位：half-day）
        lf: dict[str, int] = {}
        ls: dict[str, int] = {}
        for tid in reversed(topo_order):
            if not successors[tid]:
                lf[tid] = project_half_days
            else:
                lf[tid] = min(ls[s] for s in successors[tid])
            ls[tid] = lf[tid] - durations[tid]

        # 关键路径: float == 0
        float_time = {tid: ls[tid] - es[tid] for tid in task_map}
        critical = [tid for tid in topo_order if float_time[tid] == 0]

        # half-day → 自然天数（向上取整，0.5 天算 1 天工期）
        project_days = math.ceil(project_half_days / 2)

        # 从截止日倒推起始日；若早于今天则改为从今天正排（避免排到过去）
        # P3-1: 使用工作日计算，跳过周末
        today = config.today()
        ideal_start = _sub_work_days(deadline_date, project_days - 1)
        forced_forward = ideal_start < today
        if forced_forward:
            start_base = _next_workday(today)
        else:
            start_base = _next_workday(ideal_start)

        timeline_tasks: list[TimelineTask] = []
        for tid in topo_order:
            t = task_map[tid]
            # 获取该任务负责人的不可用日期，排期时跳过
            assigned_people = assignments.get(tid, [])
            task_skip_dates: set[date] = set()
            for person in assigned_people:
                task_skip_dates |= member_unavailable.get(person, set())
            # P3-1: half-day 偏移转工作日偏移，跳过周末和负责人不可用日
            work_offset = round(es[tid] / 2)
            s_date = datetime.combine(_add_work_days(start_base, work_offset, task_skip_dates), datetime.min.time())
            # 结束日 = 开始日 + 工期 - 1 个工作日（同样跳过负责人不可用日）
            dur_days = math.ceil(durations[tid] / 2)
            e_date = datetime.combine(_add_work_days(s_date.date(), max(0, dur_days - 1), task_skip_dates), datetime.min.time())
            timeline_tasks.append(TimelineTask(
                task_id=tid,
                name=t.name,
                start_date=s_date,
                end_date=e_date,
                is_critical=(tid in critical),
                float_days=math.ceil(max(0, float_time[tid]) / 2),
                assigned_to=assigned_people,
                status=t.status,
            ))

        risk = ""
        if broken_cycle:
            risk = "（检测到依赖环，已断环继续排期，结果仅供参考）"
        if project_days <= 0:
            risk += "（总工期为 0，请检查任务工时）"

        # Deadline overrun check (P3-1: 使用工作日计算)
        available_days = max(1, _count_work_days(today, deadline_date))
        overrun_days = project_days - available_days
        if forced_forward:
            risk += f"（警告：倒推起始日早于今天，已改为从今天正排；总工期 {project_days} 工作日，"
            risk += f"预计 {_add_work_days(start_base, project_days - 1)} 完成，"
            risk += f"将晚于截止日 {deadline_date}，建议缩减任务或延长截止日期）"
        elif overrun_days > 0:
            risk += f"（警告：总工期 {project_days} 工作日超过可用 {available_days} 工作日，超出 {overrun_days} 工作日！建议缩减任务或延长截止日期）"
        elif available_days > 0 and overrun_days > -3:
            risk += f"（注意：仅剩 {-overrun_days} 工作日缓冲，建议关注关键路径进度）"

        # 构造可读的 reasoning
        cap_desc = ""
        if member_daily:
            cap_parts = [f"{n}: {h:g}h/天" for n, h in member_daily.items()]
            cap_desc = f"各成员每日可用工时：{', '.join(cap_parts)}。"
        else:
            cap_desc = f"按全局默认每人每天 {global_daily:g}h 折算。"

        # P1-6: 检查每人每日并行任务是否超出当日可用工时
        if member_daily:
            from collections import defaultdict
            warn = '（警告：%s 在 %s 并行任务折算 %.1fh 超过当日可用 %.1fh，建议拆分人手或拉开日期）'
            per_day = defaultdict(float)
            for tt in timeline_tasks:
                if tt.status == "completed":
                    continue
                s = tt.start_date.date() if hasattr(tt.start_date, "date") else tt.start_date
                e = tt.end_date.date() if hasattr(tt.end_date, "date") else tt.end_date
                dur_days = max(1, (e - s).days + 1)
                assigned = tt.assigned_to or []
                t_ref = task_map.get(tt.task_id)
                th = t_ref.estimated_hours if t_ref else 0.0
                for idx, nm in enumerate(assigned):
                    cap = member_daily.get(nm)
                    if cap is None:
                        continue
                    contrib = (th / dur_days) if idx == 0 else (0.5 * cap)
                    d = s
                    while d <= e:
                        if not _is_weekend(d):
                            per_day[(nm, d)] += contrib
                        d += timedelta(days=1)
            worst = {}
            for (nm, d), hrs in per_day.items():
                cap = member_daily.get(nm, global_daily)
                if hrs > cap + 1e-6:
                    prev = worst.get(nm)
                    if prev is None or hrs > prev[1]:
                        worst[nm] = (d, hrs, cap)
            for nm, (d, hrs, cap) in sorted(worst.items()):
                risk += warn % (
                    nm, d.isoformat(), hrs, cap,
                )

        reasoning = (
            f"使用关键路径法(CPM)计算，以半天为最小排期粒度，跳过周末。"
            f"共 {len(tasks)} 个任务，总工期 {project_days} 工作日。"
            f"{cap_desc}"
            f"关键路径：{' -> '.join(critical) or '无'}。"
            f"非关键任务有浮动天数，可灵活调整。{risk}"
        )

        return TimelineOutput(
            tasks=timeline_tasks,
            critical_path=critical,
            total_days=project_days,
            note=(f"总工期 {project_days} 工作日，起始日 {start_base.isoformat()}，"
                  f"截止日 {deadline}{risk and ';' + risk}"),
            reasoning=reasoning,
        )
