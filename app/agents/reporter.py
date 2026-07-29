"""
Report Agent
负责人：B
负责：把 Plan + Timeline + QA 矩阵格式化为报告文本
"""

from app.agents.base import BaseAgent
from app.llm.prompts import REPORTER_SYSTEM
from app.models.schemas import AgentError, PlanOutput, TimelineOutput, QAOutput, ReportOutput


class ReporterAgent(BaseAgent[ReportOutput]):
    system_prompt = REPORTER_SYSTEM
    response_model = ReportOutput

    def run(self, plan: PlanOutput,
            timeline: TimelineOutput,
            qa_matrix: QAOutput) -> ReportOutput:
        """将规划、时间线、QA矩阵合并为最终报告"""
        # 精简摘要传给 LLM，避免全量 JSON 的 token 浪费
        task_lines = '\n'.join(
            f"- {t.id} {t.name}（{t.estimated_hours}h，依赖: {', '.join(t.dependencies) or '无'}）"
            for t in plan.tasks)
        tl_lines = '\n'.join(
            f"- {t.task_id} {t.name}: {t.start_date}~{t.end_date}"
            f"{' [关键]' if t.is_critical else ''}"
            for t in timeline.tasks) or '无时间线数据'
        qa_lines = '\n'.join(
            f"- {a.task_name}: 负责人 {a.presenter}，主要协助 {a.qa_primary}，"
            f"辅助协助 {', '.join(a.qa_support) or '无'}"
            for a in qa_matrix.assignments) or '无分配数据'
        user = (
            f"请根据以下信息生成最终报告：\n\n"
            f"## 任务计划（共 {len(plan.tasks)} 个任务）\n{task_lines}\n\n"
            f"## 时间线（总工期 {timeline.total_days} 天）\n{tl_lines}\n\n"
            f"## 责任分工\n{qa_lines}"
        )
        result = self._call_llm(user, temperature=0.5)

        # LLM 失败时用纯文本兜底
        if isinstance(result, AgentError):
            return self._fallback_report(plan, timeline, qa_matrix, result.message)
        return result

    def _fallback_report(self, plan: PlanOutput, timeline: TimelineOutput,
                         qa_matrix: QAOutput, error_msg: str = "") -> ReportOutput:
        """LLM 不可用时的纯文本兜底报告"""
        task_lines = []
        for t in plan.tasks:
            dep = f"（依赖: {', '.join(t.dependencies)}）" if t.dependencies else ""
            task_lines.append(f"- {t.id} {t.name}：{t.estimated_hours}h{dep}")
        plan_text = f"共 {len(plan.tasks)} 个任务：\n" + "\n".join(task_lines)

        tl_lines = []
        for t in timeline.tasks:
            mark = " [关键路径]" if t.is_critical else ""
            tl_lines.append(f"- {t.task_id} {t.name}：{t.start_date} ~ {t.end_date}{mark}")
        tl_text = (f"总工期 {timeline.total_days} 天，"
                   f"关键路径：{', '.join(timeline.critical_path) or '无'}\n"
                   + "\n".join(tl_lines))

        qa_lines = []
        for a in qa_matrix.assignments:
            support = ", ".join(a.qa_support) if a.qa_support else "无"
            qa_lines.append(
                f"- {a.task_name}：负责人 {a.presenter}，主要协助 {a.qa_primary}，辅助协助 {support}"
            )
        qa_text = "\n".join(qa_lines) if qa_lines else "无 QA 分配数据"

        summary = f"{plan.summary}\n\n任务概览：\n{plan_text}"
        risk = ""
        if error_msg:
            risk = f"报告生成异常，以下为兜底文本：{error_msg}"
        if timeline.total_days == 0 and timeline.tasks:
            risk += "\n注意：时间线计算可能存在异常。"

        return ReportOutput(
            summary=summary,
            timeline_section=tl_text,
            qa_matrix_section=qa_text,
            risk_note=risk,
        )
