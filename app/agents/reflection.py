"""
Reflection Agent
负责：对完整计划进行批判性自我审查，发现潜在问题并给出改进建议。
      支持 LLM + 确定性兜底双保险。
"""

from __future__ import annotations

import logging
from collections import Counter

from app.agents.base import BaseAgent
from app.llm.prompts import REFLECTION_SYSTEM, REFLECTION_USER_TEMPLATE
from app.models.schemas import (
    AgentError,
    PlanOutput,
    QAOutput,
    ReflectionIssue,
    ReflectionOutput,
    TimelineOutput,
)

logger = logging.getLogger(__name__)


class ReflectionAgent(BaseAgent[ReflectionOutput]):
    system_prompt = REFLECTION_SYSTEM
    response_model = ReflectionOutput

    def run(
        self,
        plan: PlanOutput,
        timeline: TimelineOutput,
        qa_matrix: QAOutput,
        total_capacity: float = 0.0,
    ) -> ReflectionOutput:
        """对完整计划进行审查，返回 ReflectionOutput。

        Args:
            plan: Planner 生成的任务拆解
            timeline: Timeline 生成的排期
            qa_matrix: Matcher 生成的分工矩阵
            total_capacity: 团队总产能（小时），用于负载评估
        """
        user_prompt = self._build_prompt(plan, timeline, qa_matrix, total_capacity)
        result = self._call_llm(user_prompt, temperature=0.3)

        if isinstance(result, AgentError):
            logger.warning("ReflectionAgent LLM failed, using deterministic fallback: %s",
                           result.message)
            return self._deterministic_reflect(plan, timeline, qa_matrix, total_capacity)

        return result

    # ──────────── prompt 构建 ────────────

    def _build_prompt(
        self,
        plan: PlanOutput,
        timeline: TimelineOutput,
        qa_matrix: QAOutput,
        total_capacity: float,
    ) -> str:
        # 任务摘要
        task_lines = "\n".join(
            f"- {t.id} {t.name}（{t.estimated_hours}h"
            f"，依赖: {', '.join(t.dependencies) or '无'}）"
            for t in plan.tasks
        )
        total_hours = sum(t.estimated_hours for t in plan.tasks)

        # 关键路径信息
        critical_path = " → ".join(timeline.critical_path) if timeline.critical_path else "无"
        critical_tasks = ", ".join(
            f"{t.task_id}({t.name})"
            for t in timeline.tasks
            if t.is_critical
        ) or "无"

        # 负载统计
        workload: Counter[str] = Counter()
        for a in qa_matrix.assignments:
            if a.presenter:
                workload[a.presenter] += 1
            if a.qa_primary:
                workload[a.qa_primary] += 0.5
        workload_lines = "\n".join(
            f"- {name}: 主负责 {cnt} 个任务当量" for name, cnt in workload.most_common()
        ) or "无分配数据"

        # 产能信息
        cap_info = f"团队总产能 {total_capacity:.1f}h，任务总工时 {total_hours:.1f}h"
        if total_capacity > 0:
            ratio = total_hours / total_capacity * 100
            cap_info += f"，负载率 {ratio:.0f}%"

        return REFLECTION_USER_TEMPLATE.format(
            task_count=len(plan.tasks),
            total_hours=total_hours,
            task_summary=task_lines,
            total_days=timeline.total_days,
            critical_path=critical_path,
            critical_tasks=critical_tasks,
            workload_summary=workload_lines,
            capacity_summary=cap_info,
        )

    # ──────────── 确定性兜底 ────────────

    def _deterministic_reflect(
        self,
        plan: PlanOutput,
        timeline: TimelineOutput,
        qa_matrix: QAOutput,
        total_capacity: float,
    ) -> ReflectionOutput:
        """LLM 不可用时，用规则引擎做确定性审查，保证链路不中断。"""
        issues: list[ReflectionIssue] = []
        total_hours = sum(t.estimated_hours for t in plan.tasks)

        # ① 负载检查
        workload: Counter[str] = Counter()
        for a in qa_matrix.assignments:
            if a.presenter:
                workload[a.presenter] += 1
        if workload:
            max_load = max(workload.values())
            min_load = min(workload.values())
            if max_load > 0 and max_load / max(min_load, 0.01) >= 2.5:
                overloaded = [name for name, cnt in workload.items() if cnt == max_load]
                issues.append(ReflectionIssue(
                    level="warning",
                    dimension="负载均衡",
                    description=f"成员负载差距过大：{', '.join(overloaded)} 承担了 {max_load} 个任务，"
                                f"而最少的只有 {min_load} 个，比例达 {max_load/max(min_load,1):.1f}x。",
                    suggestion="建议重新分配部分任务，确保每人负担相近。",
                    affected_tasks=[],
                ))

        # ② 工时 vs 产能
        if total_capacity > 0:
            ratio = total_hours / total_capacity
            if ratio > 1.3:
                issues.append(ReflectionIssue(
                    level="warning",
                    dimension="工时估算",
                    description=f"任务总工时（{total_hours:.1f}h）超出团队总产能（{total_capacity:.1f}h）"
                                f"的 {(ratio - 1) * 100:.0f}%，存在超载风险。",
                    suggestion="建议削减低优先级任务的工时，或延长截止日期。",
                    affected_tasks=[],
                ))
            elif ratio < 0.5:
                issues.append(ReflectionIssue(
                    level="suggestion",
                    dimension="工时估算",
                    description=f"任务总工时（{total_hours:.1f}h）仅占团队产能（{total_capacity:.1f}h）"
                                f"的 {ratio * 100:.0f}%，可能存在工时低估或任务遗漏。",
                    suggestion="检查是否有遗漏的关键任务，或考虑增加任务范围。",
                    affected_tasks=[],
                ))

        # ③ 关键路径过长
        if timeline.total_days > 0 and len(timeline.critical_path) > 5:
            issues.append(ReflectionIssue(
                level="warning",
                dimension="时间线",
                description=f"关键路径包含 {len(timeline.critical_path)} 个任务，链条较长，"
                            f"任意一环延误都会影响整体工期。",
                suggestion="考虑将关键路径上的长任务拆分，或增加并行处理空间。",
                affected_tasks=timeline.critical_path,
            ))

        # ④ 单任务工时过大
        heavy_tasks = [t for t in plan.tasks if t.estimated_hours > 12]
        for t in heavy_tasks:
            issues.append(ReflectionIssue(
                level="suggestion",
                dimension="任务拆解",
                description=f"任务 {t.id}（{t.name}）工时达 {t.estimated_hours}h，粒度偏大，"
                            f"难以管控进度。",
                suggestion=f"建议将 {t.name} 拆分为 2-3 个子任务，每项不超过 8h。",
                affected_tasks=[t.id],
            ))

        # ⑤ 无依赖链的孤立任务（非前驱、非后继）
        all_ids = {t.id for t in plan.tasks}
        has_dep_or_depended = set()
        for t in plan.tasks:
            if t.dependencies:
                has_dep_or_depended.add(t.id)
                has_dep_or_depended.update(t.dependencies)
        isolated = [t for t in plan.tasks
                    if t.id not in has_dep_or_depended and len(plan.tasks) > 3]
        if len(isolated) > len(plan.tasks) * 0.6:
            issues.append(ReflectionIssue(
                level="suggestion",
                dimension="任务拆解",
                description=f"{len(isolated)} 个任务没有依赖关系，任务间逻辑关联不清晰。",
                suggestion="建议梳理任务的先后依赖，完善依赖链，有助于 CPM 排期更准确。",
                affected_tasks=[t.id for t in isolated[:5]],
            ))

        # 综合评分
        error_count = sum(1 for i in issues if i.level == "error")
        warning_count = sum(1 for i in issues if i.level == "warning")
        score = max(0.0, 10.0 - error_count * 2.5 - warning_count * 1.0 - len(issues) * 0.2)
        score = round(min(score, 10.0), 1)
        passed = error_count == 0

        if score >= 8:
            comment = (f"计划整体质量良好，共 {len(plan.tasks)} 个任务，"
                       f"总工时 {total_hours:.1f}h，工期 {timeline.total_days} 天。"
                       f"任务拆解较为合理，时间线可行，分工基本均衡。")
        else:
            comment = (f"计划发现 {len(issues)} 个问题（{error_count} 个错误，"
                       f"{warning_count} 个警告），需要重点关注负载均衡与工时估算。"
                       f"建议优先修复 error 级别问题后再进入执行阶段。")

        # 改进优先级
        priority: list[str] = []
        for issue in sorted(issues, key=lambda x: {"error": 0, "warning": 1, "suggestion": 2}.get(x.level, 3)):
            tip = f"【{issue.dimension}】{issue.suggestion or issue.description[:30]}"
            if tip not in priority:
                priority.append(tip)
            if len(priority) >= 5:
                break

        return ReflectionOutput(
            issues=issues,
            overall_score=score,
            overall_comment=comment,
            improvement_priority=priority,
            passed=passed,
        )
