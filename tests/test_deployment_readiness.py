"""D4：依赖锁定、环境变量与回退文档的静态契约检查。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_requirements_are_exact_pins():
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(
            encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines
    assert all("==" in line for line in lines)
    assert not any(">=" in line for line in lines)


def test_env_example_covers_deployment_variables():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "MAP_REALTIME_API_KEY", "MAP_REALTIME_MODEL",
        "MAP_REALTIME_BASE_URL",
        "ASCEND_OMNI_WS_URL", "ASCEND_OMNI_TIMEOUT",
        "APP_LOCAL_AUDIO_CHUNK_SECONDS",
        "APP_LOCAL_AUDIO_MAX_SECONDS",
        "APP_LOCAL_TTS_ENABLED",
        "APP_MODEL_MODE", "APP_ALLOW_EXTERNAL_MODELS",
        "APP_ADMIN_TOKEN", "APP_NOTIFY_WEBHOOK",
        "STORAGE_BACKEND", "S3_BUCKET", "S3_PREFIX",
    ):
        assert key in env
    # 合规模式不得再出现外部模型配置
    for key in (
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "APP_VISION_MODEL", "APP_ASR_MODEL",
        "APP_VISION_API_KEY", "APP_ASR_API_KEY",
        "APP_ASR_TRANSCRIPTION_MODE",
        "LLM_PREFER_PLAIN", "LLM_DISABLE_THINKING",
    ):
        assert key not in env


def test_render_config_is_compliant_minicpm_template():
    """render.yaml 必须与参赛合规模板一致：仅 MiniCPM-o，无外部模型配置。"""
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "healthCheckPath: /api/health" in render
    assert "APP_MODEL_MODE" in render
    assert "minicpm" in render
    assert "APP_ALLOW_EXTERNAL_MODELS" in render
    assert "MAP_REALTIME_API_KEY" in render
    assert "MAP_REALTIME_MODEL" in render
    assert "ASCEND_OMNI_WS_URL" in render
    assert "S3_BUCKET" in render
    # 合规模式不得再出现 DeepSeek/DashScope 等外部模型配置
    assert "LLM_MODEL" not in render
    assert "APP_VISION_MODEL" not in render
    assert "APP_ASR_MODEL" not in render


def test_rollback_document_exists():
    doc = ROOT / "docs" / "部署与回退清单.md"
    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    assert "回退流程" in content
    assert "Rollback" in content
