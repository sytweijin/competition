"""
Timeline Agent
负责人：B
负责：倒排时间线 + 关键路径标红
"""

from datetime import date, timedelta
from collections import deque

from app.agents.base import BaseAgent
from app.models.schemas import PlanOutput, TimelineOutput, TimelineTask


class TimelineAgent(BaseAgent[TimelineOutput]):
    system_prompt = ""
    response_model = None  # 不用 LLM，纯算法

    def __init__(self, llm=None):
        # 不初始化 LLMClient，Timeline 是纯算法
        self.llm = None

    def run(self, plan: PlanOutput, deadline: str) -> TimelineOutput:
        """根据任务依赖和工时，用 CPM 算法生成倒排时间线"""
        deadline_date = date.fromisoformat(deadline)
        tasks = plan.tasks

        # 构建邻接表
        task_map = {t.id: t for t in tasks}
        successors: dict[str, list[str]] = {t.id: [] for t in tasks}
        predecessors: dict[str, list[str]] = {t.id: [] for t in tasks}
        for t in tasks:
            for dep in t.dependencies:
                if dep in successors:
                    successors[dep].append(t.id)
                    predecessors[t.id].append(dep)

        # 拓扑排序（Kahn 算法）
        in_degree = {t.id: len(predecessors[t.id]) for t in tasks}
        queue = deque(tid for tid, d in in_degree.items() if d == 0)
        topo_order = []
        while queue:
            tid = queue.popleft()
            topo_order.append(tid)
            for s in successors[tid]:
                in_degree[s] -= 1
                if in_degree[s] == 0:
                    queue.append(s)

        if len(topo_order) != len(tasks):
            return TimelineOutput(
                tasks=[], critical_path=[], total_days=0,
                note="检测到依赖环，无法生成时间线",
            )

        # 工时转天数（按每人每天 4 小时估算）
        hours_per_day = 4.0
        durations = {t.id: max(1, round(t.estimated_hours / hours_per_day))
                     for t in tasks}

        # Forward pass: 最早开始/结束
        es = {}
        ef = {}
        for tid in topo_order:
            if not predecessors[tid]:
                es[tid] = 0
            else:
                es[tid] = max(ef[p] for p in predecessors[tid])
            ef[tid] = es[tid] + durations[tid]

        project_days = max(ef.values()) if ef else 0

        # Backward pass: 最晚开始/结束
        lf = {}
        ls = {}
        for tid in reversed(topo_order):
            if not successors[tid]:
                lf[tid] = project_days
            else:
                lf[tid] = min(ls[s] for s in successors[tid])
            ls[tid] = lf[tid] - durations[tid]

        # 关键路径: float == 0 的任务
        float_time = {tid: ls[tid] - es[tid] for tid in task_map}
        critical = [tid for tid in topo_order if float_time[tid] == 0]

        # 计算实际日期（从 deadline 倒推起始日）
        start_base = deadline_date - timedelta(days=project_days)
        timeline_tasks = []
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
                assigned_to=[],
            ))

        reasoning = (
            f"使用关键路径法(CPM)计算。"
            f"共 {len(tasks)} 个任务，总工期 {project_days} 天"
            f"（按每人每天 {hours_per_day}h 估算工时转天数）。"
            f"关键路径：{' -> '.join(critical) or '无'}。"
            f"非关键任务有浮动时间，可灵活调整。"
        )

        return TimelineOutput(
            tasks=timeline_tasks,
            critical_path=critical,
            total_days=project_days,
            note=f"总工期 {project_days} 天，起始日 {start_base.isoformat()}，截止日 {deadline}",
            reasoning=reasoning,
        )