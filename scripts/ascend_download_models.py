#!/usr/bin/env python3
"""Download the GGUF files needed for llama.cpp-omni on Ascend."""

import os

from modelscope.hub.file_download import model_file_download


MODEL_ID = "OpenBMB/MiniCPM-o-4_5-gguf"
LOCAL_DIR = "/workspace/MiniCPM-o-4_5-gguf"

FILES = [
    "MiniCPM-o-4_5-F16.gguf",
    "audio/MiniCPM-o-4_5-audio-F16.gguf",
    "vision/MiniCPM-o-4_5-vision-F16.gguf",
    "tts/MiniCPM-o-4_5-tts-F16.gguf",
    "tts/MiniCPM-o-4_5-projector-F16.gguf",
    "token2wav-gguf/encoder.gguf",
    "token2wav-gguf/flow_matching.gguf",
    "token2wav-gguf/flow_extra.gguf",
    "token2wav-gguf/hifigan2.gguf",
    "token2wav-gguf/prompt_cache.gguf",
]

EXPECTED_SIZES = {
    "MiniCPM-o-4_5-F16.gguf": 16384959136,
    "audio/MiniCPM-o-4_5-audio-F16.gguf": 660167904,
    "vision/MiniCPM-o-4_5-vision-F16.gguf": 1095113184,
    "tts/MiniCPM-o-4_5-tts-F16.gguf": 1157244416,
    "tts/MiniCPM-o-4_5-projector-F16.gguf": 14948640,
    "token2wav-gguf/encoder.gguf": 151339008,
    "token2wav-gguf/flow_matching.gguf": 458250240,
    "token2wav-gguf/flow_extra.gguf": 13663328,
    "token2wav-gguf/hifigan2.gguf": 83242816,
    "token2wav-gguf/prompt_cache.gguf": 211613152,
}


def main():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    failed = False
    for rel in FILES:
        print(f"[DOWNLOAD] {rel}")
        try:
            path = model_file_download(MODEL_ID, rel, local_dir=LOCAL_DIR)
            size = os.path.getsize(path)
            expected = EXPECTED_SIZES.get(rel)
            if expected is not None and size != expected:
                print(f"[WARN]     {path} size={size} expected={expected}")
                failed = True
            else:
                print(f"[DONE]     {path} ({size} bytes)")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR]    {rel}: {exc}")
            failed = True

    if failed:
        print("SOME FILES ARE INCOMPLETE, please rerun this script.")
        raise SystemExit(1)
    print("ALL FILES OK")


if __name__ == "__main__":
    main()
