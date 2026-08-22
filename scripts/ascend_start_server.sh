#!/bin/bash
set -e

cd /workspace/llama.cpp-omni

MODEL="/workspace/MiniCPM-o-4_5-gguf/MiniCPM-o-4_5-F16.gguf"
HOST="0.0.0.0"
PORT="28099"
CTX="4096"
NGL="99"

export LD_LIBRARY_PATH="$(pwd)/build/bin:/usr/local/Ascend/cann-9.1.0-beta.1/lib64:${LD_LIBRARY_PATH}"

exec ./build/bin/llama-omni-server \
    -m "$MODEL" \
    --host "$HOST" \
    --port "$PORT" \
    -c "$CTX" \
    -ngl "$NGL"
