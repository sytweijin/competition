"""
Timeline Agent
负责人：B
负责：倒排时间线 + 关键路径(CPM)计算。

纯算法，不依赖 LLM。用拓扑排序 + Forward/Backward pass 计算关键路径，
从截止日倒推起始日。支持可配置的「每人每天工时」，并对依赖环做容错。
"""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta

from app.agents.base import BaseAgent
from app.models.schemas import (
    PlanOutput, TimelineOutput, TimelineTask,
)

# 默认：每人每天按 4 有效工时折算为天数
DEFAULT_HOURS_PER_DAY = 4.0


class TimelineAgent(BaseAgent[TimelineOutput]):
    """CPM 关键路径 Agent（纯算法，不实例化 LLM）。"""

    system_prompt = ""
    response_model = None  # 不用 LLM

    def __init__(self, llm=None):
        # 不初始化 LLMClient
        self.llm = None

    def run(self, plan: PlanOutput, deadline: str,
            hours_per_day: float = DEFAULT_HOURS_PER_DAY,
            assignments: dict[str, list[str]] | None = None) -> TimelineOutput:
        """根据任务依赖和工时，用 CPM 算法生成倒排时间线。

        Args:
            plan: Planner 输出的计划。
            deadline: 截止日期 ISO 字符串。
            hours_per_day: 每人每天有效工时，用于工时→天数折算。
            assignments: {task_id: [成员名]}，可选，用于回填负责人。
        """
        deadline_date = date.fromisoformat(deadline)
        tasks = plan.tasks
        assignments = assignments or {}
        hours_per_day = max(0.5, float(hours_per_day))  # 防御性下限

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
            # 断开这些任务的所有入边后重新尝试
            for tid in remaining:
                predecessors[tid] = []
                successors[tid] = []
            topo_order.extend(remaining)

        # 工时→天数折算（至少 1 天）
        durations = {
            t.id: max(1, round(t.estimated_hours / hours_per_day))
            for t in tasks
        }

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

        reasoning = (
            f"使用关键路径法(CPM)计算。"
            f"共 {len(tasks)} 个任务，总工期 {project_days} 天"
            f"（按每人每天 {hours_per_day:g}h 折算工时→天数）。"
            f"关键路径：{' -> '.join(critical) or '无'}。"
            f"非关键任务有浮动天数，可灵活调整。{risk}"
        )

        return TimelineOutput(
            tasks=timeline_tasks,
            critical_path=critical,
            total_days=project_days,
            note=(f"总工期 {project_days} 天，起始日 {start_base.isoformat()}，"
                  f"截止日 {deadline}{risk and '；' + risk}"),
            reasoning=reasoning,
        )