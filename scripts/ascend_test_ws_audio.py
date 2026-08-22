#!/usr/bin/env python3
"""Smoke test: send an audio prompt to llama-omni-server over WebSocket."""

import base64
import json
import sys
import time
import wave

import numpy as np
import websocket


HOST = "127.0.0.1"
PORT = 28099
DEFAULT_AUDIO = "tools/omni/assets/test_case/audio_test_case/audio_test_case_0001.wav"


def load_audio_pcm_b64(path):
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return base64.b64encode(samples.astype(np.float32).tobytes()).decode("ascii")


def main():
    audio_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AUDIO
    audio_b64 = load_audio_pcm_b64(audio_path)

    ws = websocket.WebSocket()
    ws.connect(f"ws://{HOST}:{PORT}/backend", timeout=60)
    ws.settimeout(300)

    start = time.time()

    ws.send(json.dumps({
        "type": "session.init",
        "payload": {"mode": "turn_based", "use_tts": False},
    }))
    print(f"[{time.time()-start:.2f}s] {json.loads(ws.recv())['type']}")

    ws.send(json.dumps({
        "type": "input.append",
        "input": {
            "messages": [{
                "role": "user",
                "content": [{"type": "audio", "data": audio_b64}],
            }],
            "streaming": True,
            "generation": {"max_new_tokens": 128},
        },
    }))

    text = ""
    while True:
        msg = json.loads(ws.recv())
        if msg["type"] == "response.output.delta" and msg.get("kind") == "text":
            text += msg.get("text", "")
        elif msg["type"] == "response.done":
            break
        elif msg["type"] == "error":
            print("error:", msg)
            break

    print(f"总耗时: {time.time()-start:.2f}s")
    print(f"回复: {text}")
    ws.close()


if __name__ == "__main__":
    main()
