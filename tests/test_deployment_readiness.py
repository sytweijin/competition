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
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "APP_VISION_MODEL", "APP_ASR_MODEL",
        "APP_VISION_API_KEY", "APP_VISION_BASE_URL",
        "APP_ASR_API_KEY", "APP_ASR_BASE_URL",
        "APP_ASR_TRANSCRIPTION_MODE",
        "MAP_REALTIME_API_KEY", "MAP_REALTIME_MODEL",
        "MAP_REALTIME_BASE_URL",
        "STORAGE_BACKEND", "S3_BUCKET", "S3_PREFIX",
    ):
        assert key in env


def test_render_config_has_health_and_media_models():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "healthCheckPath: /api/health" in render
    assert "APP_VISION_MODEL" in render
    assert "APP_ASR_MODEL" in render
    assert "APP_VISION_API_KEY" in render
    assert "APP_ASR_API_KEY" in render
    assert "APP_ASR_TRANSCRIPTION_MODE" in render
    assert "S3_BUCKET" in render


def test_rollback_document_exists():
    doc = ROOT / "docs" / "部署与回退清单.md"
    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    assert "回退流程" in content
    assert "Rollback" in content
