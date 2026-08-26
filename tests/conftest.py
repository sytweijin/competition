"""pytest 共享夹具。

为编辑/重算/成员变动等会间接触发 ReporterAgent / ReflectionAgent 的测试提供 mock，
避免它们因真实 LLM 网络调用而挂死或产生不稳定结果。
直接在 test_agents.py / test_reflection.py 里构造 Agent 本体的用例不受影响，
因为它们绕过这里 patch 的入口。
"""
import pytest
from pathlib import Path

from app.models.schemas import ReflectionOutput, ReportOutput


@pytest.fixture(autouse=True)
def _disable_real_media_calls(monkeypatch):
    """所有测试默认禁用真实视觉/语音 API，避免 .env 密钥导致外呼。"""
    import app.services.media_analysis as media

    monkeypatch.setattr(media, "APP_VISION_API_KEY", "")
    monkeypatch.setattr(media, "APP_VISION_MODEL", "")
    monkeypatch.setattr(media, "APP_ASR_API_KEY", "")
    monkeypatch.setattr(media, "APP_ASR_MODEL", "")
    monkeypatch.setattr(media, "MAP_REALTIME_API_KEY", "")
    monkeypatch.setattr(media, "ASCEND_OMNI_WS_URL", "")


@pytest.fixture(autouse=True)
def _force_legacy_llm_mode(monkeypatch):
    """测试默认走 legacy（DeepSeek 模拟）路径，避免合规模式发起真实调用。"""
    import app.config as config
    import app.llm.client as llm_client
    import app.services.media_analysis as media

    monkeypatch.setattr(config, "APP_MODEL_MODE", "legacy")
    monkeypatch.setattr(llm_client, "APP_MODEL_MODE", "legacy")
    # 打桩一个非空 key，让 legacy 模式的 LLMClient mock 用例可复现：
    # 不再依赖 .env / 环境变量（参赛交付 .env 中 LLM_API_KEY 为空），
    # AGENTS.md 的全量测试基线在任何干净环境都能直接跑通。
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "APP_ALLOW_EXTERNAL_MODELS", True)
    monkeypatch.setattr(media, "APP_ALLOW_EXTERNAL_MODELS", True)


@pytest.fixture(autouse=True)
def _stub_reporter_for_recompute(request):
    """让编辑/重算链路里的 ReporterAgent.run 返回固定报告，不打网络。

    仅当测试不是 test_agents（那里直接测 ReporterAgent 本体）时生效。
    """
    if Path(str(request.node.fspath)).name in {
        "test_agents.py", "test_agent_benchmark.py",
    }:
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
    if Path(str(request.node.fspath)).name == "test_reflection.py":
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
