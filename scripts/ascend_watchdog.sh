#!/usr/bin/env bash
# A3 llama-omni-server 崩溃守护：每 30 秒探测一次 28099，未健康则自动重启。
#
# 背景：本地 A3 的 whisper 编码器遇到长音频会抛
# "Position encoding buffer overflow" 直接崩溃（应用层已做音频分片规避），
# 但任何历史遗留输入仍可能把进程打死；守护脚本保证服务掉线后自动拉起，
# 避免"模型已关闭"卡死到有人手动重启。
#
# 用法（在 A3 上）：
#   setsid nohup bash /workspace/ascend_watchdog.sh > /workspace/watchdog.log 2>&1 &
set -u

PORT="28099"
LOG="/workspace/llama-omni.log"
START_SCRIPT="/workspace/llama.cpp-omni/ascend_start_server.sh"

while true; do
  if ! curl -fsS -m 5 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "[$(date '+%F %T')] llama-omni 未健康，尝试重启..." >> "${LOG}"
    # 用 [b] 技巧避免 pkill 匹配到自身命令行（shell 命令行里含同样字符串）
    pkill -f '[b]uild/bin/llama-omni-server' 2>/dev/null || true
    sleep 2
    if [ -x "${START_SCRIPT}" ]; then
      (cd /workspace/llama.cpp-omni \
        && setsid nohup ./ascend_start_server.sh >> "${LOG}" 2>&1 &)
    fi
    sleep 25
  fi
  sleep 30
done
