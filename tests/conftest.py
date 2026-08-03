"""pytest 共享夹具。

为编辑/重算/成员变动等会间接触发 ReporterAgent / ReflectionAgent 的测试提供 mock，
避免它们因真实 LLM 网络调用而挂死或产生不稳定结果。
直接在 test_agents.py / test_reflection.py 里构造 Agent 本体的用例不受影响，
因为它们绕过这里 patch 的入口。
"""
import pytest

from app.models.schemas import ReflectionOutput, ReportOutput


@pytest.fixture(autouse=True)
def _stub_reporter_for_recompute(request):
    """让编辑/重算链路里的 ReporterAgent.run 返回固定报告，不打网络。

    仅当测试不是 test_agents（那里直接测 ReporterAgent 本体）时生效。
    """
    if "test_agents" in request.node.fspath.basename:
        yield
        return
    from app.agents.reporter import ReporterAgent

    def _fake_run(self, plan=None, timeline=None, qa_matrix=None, **kw):
        return ReportOutput(
            summary="(测试用兜底报告) 计划已更新",
            timeline_section="", qa_matrix_section="", risk_note="",
        )

    orig = ReporterAgent.run
    ReporterAgent.run = _fake_run
    try:
        yield
    finally:
        ReporterAgent.run = orig


@pytest.fixture(autouse=True)
def _stub_reflection_for_recompute(request):
    """让链路里的 ReflectionAgent.run 返回固定输出，不打网络。

    仅当测试不是 test_reflection（那里直接测 ReflectionAgent 本体）时生效。
    """
    if "test_reflection" in request.node.fspath.basename:
        yield
        return
    from app.agents.reflection import ReflectionAgent

    def _fake_run(self, plan=None, timeline=None, qa_matrix=None, **kw):
        return ReflectionOutput(
            issues=[],
            overall_score=8.0,
            overall_comment="(测试用兜底审查)",
            improvement_priority=[],
            passed=True,
        )

    orig = ReflectionAgent.run
    ReflectionAgent.run = _fake_run
    try:
        yield
    finally:
        ReflectionAgent.run = orig
