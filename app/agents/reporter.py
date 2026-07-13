"""
Report Agent
负责人：B
负责：把 Plan + Timeline + QA 矩阵格式化为报告文本
"""

from app.agents.base import BaseAgent
from app.llm.prompts import REPORTER_SYSTEM
from app.models.schemas import PlanOutput, TimelineOutput, QAOutput, ReportOutput


class ReporterAgent(BaseAgent[ReportOutput]):
    system_prompt = REPORTER_SYSTEM
    response_model = ReportOutput

    def run(self, plan: PlanOutput,
            timeline: TimelineOutput,
            qa_matrix: QAOutput) -> ReportOutput:
        """将规划、时间线、QA矩阵合并为最终报告"""
        user = (
            f"请根据以下三个部分生成最终报告：\n\n"
            f"## 任务计划\n{plan.model_dump_json(indent=2)}\n\n"
            f"## 时间线\n{timeline.model_dump_json(indent=2)}\n\n"
            f"## QA矩阵\n{qa_matrix.model_dump_json(indent=2)}"
        )
        return self._call_llm(user)
