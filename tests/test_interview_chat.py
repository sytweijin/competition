"""Multi-round interview simulation agent tests."""

from app.agents.interview_sim import InterviewSimAgent
from app.models.schemas import (
    PlanOutput, QAAssignment, QAOutput, SubTask,
)


def _plan_and_qa():
    plan = PlanOutput(
        tasks=[SubTask(id="T1", name="Research", description="",
                       estimated_hours=4)],
        summary="test plan",
    )
    qa = QAOutput(
        assignments=[
            QAAssignment(task_id="T1", task_name="Research", chapter="",
                         presenter="Alice", qa_primary="", qa_support=[])
        ],
    )
    return plan, qa


def test_interview_chat_asks_first_question():
    agent = InterviewSimAgent()

    class FakeLLM:
        def chat_messages(self, **kwargs):
            return "第一个问题：请介绍项目的核心目标。"

    agent.llm = FakeLLM()
    plan, qa = _plan_and_qa()
    reply = agent.chat_turn(plan=plan, qa_matrix=qa,
                            user_answer="", history=[])
    assert "第一个问题" in reply
    assert "QA" not in reply


def test_interview_chat_sends_user_answer_and_history():
    agent = InterviewSimAgent()
    calls = []

    class FakeLLM:
        def chat_messages(self, system_prompt, messages, temperature):
            calls.append(messages)
            return "点评合理，下一个问题：预算如何控制？"

    agent.llm = FakeLLM()
    plan, qa = _plan_and_qa()
    reply = agent.chat_turn(
        plan=plan, qa_matrix=qa,
        user_answer="我们的目标是在两周内完成调研。",
        history=[{"role": "assistant", "content": "第一个问题：项目目标是什么？"}],
    )
    assert "预算" in reply
    assert calls
    assert calls[0][-1]["role"] == "user"
    assert "两周内完成调研" in calls[0][-1]["content"]
    assert any(
        msg["content"] == "第一个问题：项目目标是什么？"
        for msg in calls[0]
    )
