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

from app.agents.base import BaseAgent
from app.models.schemas import (
    PlanOutput, TimelineOutput, TimelineTask, TeamMember,
)

DEFAULT_HOURS_PER_DAY = 4.0


class TimelineAgent(BaseAgent[TimelineOutput]):
    """CPM 关键路径 Agent（纯算法，不实例化 LLM）。"""

    system_prompt = ""
    response_model = None  # 不用 LLM

    def __init__(self, llm=None):
        self.llm = None

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
        if members:
            for m in members:
                member_daily[m.name] = max(0.5, m.daily_available_hours)

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
        today = date.today()
        ideal_start = deadline_date - timedelta(days=project_days - 1)
        forced_forward = ideal_start < today
        if forced_forward:
            start_base = today
        else:
            start_base = ideal_start

        timeline_tasks: list[TimelineTask] = []
        for tid in topo_order:
            t = task_map[tid]
            # half-day 偏移转成自然日：开始日 = start_base + es/2 天
            s_date = datetime.combine(start_base, datetime.min.time()) + timedelta(days=es[tid] / 2)
            # 结束日 = 开始日 + 工期 - 1 天（含头不含尾→含头含尾的自然日语义，避免相邻重叠）
            dur_days = math.ceil(durations[tid] / 2)
            e_date = s_date + timedelta(days=max(0, dur_days - 1))
            timeline_tasks.append(TimelineTask(
                task_id=tid,
                name=t.name,
                start_date=s_date,
                end_date=e_date,
                is_critical=(tid in critical),
                float_days=math.ceil(max(0, float_time[tid]) / 2),
                assigned_to=assignments.get(tid, []),
                status=t.status,
            ))

        risk = ""
        if broken_cycle:
            risk = "（检测到依赖环，已断环继续排期，结果仅供参考）"
        if project_days <= 0:
            risk += "（总工期为 0，请检查任务工时）"

        # Deadline overrun check
        available_days = max(1, (deadline_date - today).days + 1)  # +1: include both today and deadline
        overrun_days = project_days - available_days
        if forced_forward:
            risk += f"（警告：倒推起始日早于今天，已改为从今天正排；总工期 {project_days} 天，"
            risk += f"预计 {start_base + timedelta(days=project_days - 1)} 完成，"
            risk += f"将晚于截止日 {deadline_date}，建议缩减任务或延长截止日期）"
        elif overrun_days > 0:
            risk += f"（警告：总工期 {project_days} 天超过可用天数 {available_days} 天，超出 {overrun_days} 天！建议缩减任务或延长截止日期）"
        elif available_days > 0 and overrun_days > -3:
            risk += f"（注意：仅剩 {-overrun_days} 天缓冲，建议关注关键路径进度）"

        # 构造可读的 reasoning
        cap_desc = ""
        if member_daily:
            cap_parts = [f"{n}: {h:g}h/天" for n, h in member_daily.items()]
            cap_desc = f"各成员每日可用工时：{', '.join(cap_parts)}。"
        else:
            cap_desc = f"按全局默认每人每天 {global_daily:g}h 折算。"

        reasoning = (
            f"使用关键路径法(CPM)计算，以半天为最小排期粒度。"
            f"共 {len(tasks)} 个任务，总工期 {project_days} 天。"
            f"{cap_desc}"
            f"关键路径：{' -> '.join(critical) or '无'}。"
            f"非关键任务有浮动天数，可灵活调整。{risk}"
        )

        return TimelineOutput(
            tasks=timeline_tasks,
            critical_path=critical,
            total_days=project_days,
            note=(f"总工期 {project_days} 天，起始日 {start_base.isoformat()}，"
                  f"截止日 {deadline}{risk and ';' + risk}"),
            reasoning=reasoning,
        )