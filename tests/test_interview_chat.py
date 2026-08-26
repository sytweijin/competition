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


def test_interview_chat_merges_consecutive_user_messages():
    """评委生成失败残留的连续 user 消息应合并，防止模型误判为新会话重问第一题。"""
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
        user_answer="第二轮回答",
        history=[
            {"role": "assistant", "content": "第一个问题：项目目标是什么？"},
            {"role": "user", "content": "第一轮回答（无评委回复残留）"},
            {"role": "user", "content": "第二轮回答（再次残留）"},
        ],
    )
    assert "预算" in reply
    messages = calls[0]
    roles = [m["role"] for m in messages]
    # 不允许出现连续 user，Q/A 必须交替
    assert all(
        roles[i] != "user" or roles[i + 1] != "user"
        for i in range(len(roles) - 1)
    )
    # 合并后的最后一个 user 同时包含残留回答与本轮回答
    assert "第一轮回答" in messages[-1]["content"]
    assert "第二轮回答" in messages[-1]["content"]


def test_interview_chat_adjust_question():
    agent = InterviewSimAgent()
    calls = []

    class FakeLLM:
        def chat_messages(self, system_prompt, messages, temperature):
            calls.append((system_prompt, messages))
            return "调整后的问题：你们的视觉物料具体怎么分工？"

    agent.llm = FakeLLM()
    plan, qa = _plan_and_qa()
    reply = agent.chat_turn(
        plan=plan, qa_matrix=qa,
        user_answer="这个问题不够具体，请结合视觉设计问得更细。",
        history=[{"role": "assistant", "content": "第一个问题：项目目标是什么？"}],
        mode="adjust",
    )
    assert "调整后的问题" in reply
    assert "重新调整" in calls[0][1][-1]["content"]
    assert "视觉设计问得更细" in calls[0][1][-1]["content"]
    assert "调整" in calls[0][0]
