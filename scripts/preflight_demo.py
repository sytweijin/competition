"""演示前健康检查：一键确认应用、A3、配置与 MiniCPM-o 链路可用。

用法（在仓库根目录）：
    python scripts/preflight_demo.py

检查项：
1. 本地应用服务 /api/health
2. 后端配置（本地昇腾 / ModelBest 云端 / 通用模型兜底）
3. A3 llama-omni-server /health
4. /api/realtime/status
5. MiniCPM-o 真实对话暖机（A3 健康或云端已配置时执行）

任意关键项失败时退出码为 1；全部通过时退出码为 0。
"""

import json
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

APP_URL = "http://127.0.0.1:8000"
A3_HEALTH_URL = "http://127.0.0.1:28099/health"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(text: str) -> None:
    print(f"{GREEN}✅ {text}{RESET}")


def fail(text: str) -> None:
    print(f"{RED}❌ {text}{RESET}")


def warn(text: str) -> None:
    print(f"{YELLOW}⚠️  {text}{RESET}")


def http_get(url: str, timeout: float = 8):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def http_post_json(url: str, payload: dict, timeout: float = 30):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    failed = False

    print("== 演示前健康检查 ==")

    # 1. 本地应用服务
    try:
        _, body = http_get(f"{APP_URL}/api/health", timeout=5)
        data = json.loads(body)
        version = data.get("version", "?")
        if data.get("status") == "ok":
            ok(f"应用服务运行中（/api/health，版本 {version}）")
        else:
            failed = True
            fail(f"/api/health 返回异常：{body[:120]}")
    except Exception as exc:
        failed = True
        fail(
            f"应用服务不可用（{type(exc).__name__}）→ "
            "请在仓库根目录运行 python -m app.main"
        )

    # 2. 后端配置
    from app import config

    local_url = config.ASCEND_OMNI_WS_URL
    cloud_key = config.MAP_REALTIME_API_KEY
    llm_key = config.LLM_API_KEY
    if local_url:
        warn(f"当前后端：本地昇腾（ASCEND_OMNI_WS_URL={local_url}）")
    elif cloud_key:
        warn("当前后端：ModelBest 云端（未配置 ASCEND_OMNI_WS_URL）")
    else:
        failed = True
        fail("MiniCPM-o 未配置：请配置 ASCEND_OMNI_WS_URL 或 MAP_REALTIME_API_KEY")
    if not cloud_key:
        warn("MAP_REALTIME_API_KEY 未配置 → TTS 语音回复不可用（仅云端演示）")
    if not llm_key:
        warn("LLM_API_KEY 未配置 → 兜底对话不可用")

    # 3. A3 llama-omni-server
    a3_ok = False
    try:
        _, body = http_get(A3_HEALTH_URL, timeout=8)
        data = json.loads(body)
        if data.get("status") == "ok":
            ok("A3 llama-omni-server 健康（/health）")
            a3_ok = True
        else:
            fail(f"A3 health 异常：{body[:120]}")
    except Exception as exc:
        fail(
            f"A3 不可达（{type(exc).__name__}）→ "
            "检查 VS Code 端口转发 28099；必要时在 A3 上重启："
            "pkill -f llama-omni-server && nohup ./ascend_start_server.sh &"
        )

    # 4. Realtime 状态
    try:
        _, body = http_get(f"{APP_URL}/api/realtime/status", timeout=5)
        data = json.loads(body)
        ok(
            "Realtime 状态：enabled="
            f"{data.get('enabled')} backend={data.get('backend')} "
            f"model={data.get('model')}"
        )
    except Exception as exc:
        failed = True
        fail(f"/api/realtime/status 不可用（{type(exc).__name__}）")

    # 5. MiniCPM-o 对话暖机（也是暖机动作）
    if a3_ok or cloud_key:
        try:
            _, data = http_post_json(
                f"{APP_URL}/api/realtime/chat",
                {"message": "你好，请回复：预检通过。", "max_new_tokens": 64},
                timeout=30,
            )
            reply = (data.get("reply") or "").strip()
            if reply:
                ok(f"MiniCPM-o 对话暖机成功（回复 {len(reply)} 字）")
            else:
                fail("MiniCPM-o 对话返回空回复")
        except Exception as exc:
            failed = True
            fail(
                f"MiniCPM-o 对话失败（{type(exc).__name__}）→ "
                "演示时将回退通用模型"
            )
    else:
        warn("MiniCPM-o 未配置，跳过对话暖机")

    print()
    if failed:
        print("结论：存在 ❌ 项，请先处理后（或明确接受回退）再演示。")
        return 1
    print("结论：全部通过，可以开始演示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
