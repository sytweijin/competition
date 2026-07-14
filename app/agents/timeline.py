"""
Timeline Agent
负责人：B
负责：倒排时间线 + 关键路径(CPM)计算。

纯算法，不依赖 LLM。用拓扑排序 + Forward/Backward pass 计算关键路径，
从截止日倒推起始日。

v0.3 改进：
- 支持按任务负责人各自的每日可用工时折算天数（不再硬编码每人每天4小时）
- 无负责人时使用团队平均每日可用工时
- 对依赖环做容错
"""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta

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
            hours_per_day: float | None = None,
            assignments: dict[str, list[str]] | None = None,
            members: list[TeamMember] | None = None) -> TimelineOutput:
        """根据任务依赖和工时，用 CPM 算法生成倒排时间线。

        Args:
            plan: Planner 输出的计划。
            deadline: 截止日期 ISO 字符串。
            hours_per_day: 全局每人每天有效工时（向后兼容）。
                           如果传入了 members，则优先使用成员的 daily_available_hours。
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

        # 全局默认值：优先用传入的 hours_per_day，否则用成员平均值，最后兜底 DEFAULT
        if hours_per_day is not None:
            global_daily = max(0.5, float(hours_per_day))
        elif member_daily:
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

        # 工时→天数折算：根据每个任务的负责人计算有效每日产能
        def _task_daily_capacity(task_id: str) -> float:
            """根据任务负责人计算每天可用工时。"""
            assigned = assignments.get(task_id, [])
            if not assigned or not member_daily:
                return global_daily
            # 取负责人的每日可用工时，求和（多人并行时产能为各人之和）
            capacity = 0.0
            for name in assigned:
                capacity += member_daily.get(name, global_daily)
            return max(0.5, capacity)

        durations = {}
        for t in tasks:
            daily_cap = _task_daily_capacity(t.id)
            durations[t.id] = max(1, round(t.estimated_hours / daily_cap))

        # Forward pass: 最早开始/结束
        es: dict[str, int] = {}
        ef: dict[str, int] = {}
        for tid in topo_order:
            if not predecessors[tid]:
                es[tid] = 0
            else:
                es[tid] = max(ef[p] for p in predecessors[tid])
            ef[tid] = es[tid] + durations[tid]

        project_days = max(ef.values()) if ef else 0

        # Backward pass: 最晚开始/结束
        lf: dict[str, int] = {}
        ls: dict[str, int] = {}
        for tid in reversed(topo_order):
            if not successors[tid]:
                lf[tid] = project_days
            else:
                lf[tid] = min(ls[s] for s in successors[tid])
            ls[tid] = lf[tid] - durations[tid]

        # 关键路径: float == 0
        float_time = {tid: ls[tid] - es[tid] for tid in task_map}
        critical = [tid for tid in topo_order if float_time[tid] == 0]

        # 从截止日倒推起始日
        start_base = deadline_date - timedelta(days=project_days)
        timeline_tasks: list[TimelineTask] = []
        for tid in topo_order:
            t = task_map[tid]
            s_date = start_base + timedelta(days=es[tid])
            e_date = start_base + timedelta(days=ef[tid])
            timeline_tasks.append(TimelineTask(
                task_id=tid,
                name=t.name,
                start_date=s_date,
                end_date=e_date,
                is_critical=(tid in critical),
                float_days=max(0, float_time[tid]),
                assigned_to=assignments.get(tid, []),
            ))

        risk = ""
        if broken_cycle:
            risk = "（检测到依赖环，已断环继续排期，结果仅供参考）"
        if project_days <= 0:
            risk += "（总工期为 0，请检查任务工时）"

        # Deadline overrun check
        from datetime import date as _date
        today = _date.today()
        available_days = (deadline_date - today).days
        overrun_days = project_days - available_days
        if overrun_days > 0:
            risk += f"（警告：总工期 {project_days} 天超过可用天数 {available_days} 天，超出 {overrun_days} 天！建议缩减任务或延长截止日期）"
        elif available_days > 0 and overrun_days <= -3:
            pass  # comfortable buffer, no warning needed
        elif available_days > 0:
            risk += f"（注意：仅剩 {-overrun_days} 天缓冲，建议关注关键路径进度）"

        # 构造可读的 reasoning
        cap_desc = ""
        if member_daily:
            cap_parts = [f"{n}: {h:g}h/天" for n, h in member_daily.items()]
            cap_desc = f"各成员每日可用工时：{', '.join(cap_parts)}。"
        else:
            cap_desc = f"按全局默认每人每天 {global_daily:g}h 折算。"

        reasoning = (
            f"使用关键路径法(CPM)计算。"
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
