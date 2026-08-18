#!/usr/bin/env python
"""真实调用视觉 OCR 与语音转写模型，验证 C1 配置是否可用。

安全约束：本脚本只读取模型名称和 API Key 是否配置，不输出密钥。
未配置对应模型时跳过该项；配置后调用失败会返回非零退出码。
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.config import (  # noqa: E402
    APP_ASR_API_KEY, APP_ASR_MODEL, APP_VISION_API_KEY, APP_VISION_MODEL,
)
from app.services.media_analysis import (  # noqa: E402
    audio_transcribe_text, image_ocr_text,
)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _sample_audio_bytes() -> bytes:
    url = "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def main() -> int:
    failed = False
    if APP_VISION_MODEL:
        if not APP_VISION_API_KEY:
            print("APP_VISION_MODEL 已配置，但 APP_VISION_API_KEY 未配置。",
                  file=sys.stderr)
            return 2
        try:
            text = image_ocr_text("verify.png", _png_bytes())
            print(f"视觉 OCR [{APP_VISION_MODEL}] 调用成功：{text[:80]}")
        except ValueError as exc:
            print(f"视觉 OCR [{APP_VISION_MODEL}] 调用失败：{exc}", file=sys.stderr)
            failed = True
    else:
        print("未配置 APP_VISION_MODEL，跳过视觉 OCR 验证。")
    if APP_ASR_MODEL:
        if not APP_ASR_API_KEY:
            print("APP_ASR_MODEL 已配置，但 APP_ASR_API_KEY 未配置。",
                  file=sys.stderr)
            return 2
        try:
            text = audio_transcribe_text("verify.mp3", _sample_audio_bytes())
            print(f"语音转写 [{APP_ASR_MODEL}] 调用成功：{text[:80]}")
        except ValueError as exc:
            print(f"语音转写 [{APP_ASR_MODEL}] 调用失败：{exc}", file=sys.stderr)
            failed = True
    else:
        print("未配置 APP_ASR_MODEL，跳过语音转写验证。")
    if failed:
        return 1
    if not (APP_VISION_MODEL or APP_ASR_MODEL):
        print("请至少配置一个媒体模型后再运行。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
