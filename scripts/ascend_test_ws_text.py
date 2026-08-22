#!/usr/bin/env python3
"""Smoke test: send a text prompt to llama-omni-server over WebSocket."""

import json
import time

import websocket


HOST = "127.0.0.1"
PORT = 28099


def main():
    ws = websocket.WebSocket()
    ws.connect(f"ws://{HOST}:{PORT}/backend", timeout=60)
    ws.settimeout(300)

    start = time.time()

    ws.send(json.dumps({
        "type": "session.init",
        "payload": {"mode": "turn_based", "use_tts": False},
    }))
    msg = json.loads(ws.recv())
    print(f"[{time.time()-start:.2f}s] {msg['type']}")

    ws.send(json.dumps({
        "type": "input.append",
        "input": {
            "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
            "streaming": True,
            "generation": {"max_new_tokens": 128},
        },
    }))

    text = ""
    while True:
        msg = json.loads(ws.recv())
        t = time.time() - start
        if msg["type"] == "response.output.delta" and msg.get("kind") == "text":
            text += msg.get("text", "")
        elif msg["type"] == "response.done":
            print(f"[{t:.2f}s] response.done")
            break
        elif msg["type"] == "error":
            print(f"[{t:.2f}s] error:", msg)
            break

    print(f"\n总耗时: {time.time()-start:.2f}s")
    print(f"回复: {text}")
    ws.close()


if __name__ == "__main__":
    main()
