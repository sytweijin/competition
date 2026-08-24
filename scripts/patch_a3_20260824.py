"""一次性补丁：修复 llama-omni-server 的两个 A3 服务端缺陷（2026-08-24）。

1. ws_handler.cpp：会话复用时 llama_memory_clear(data=false) 只清元数据，
   长音频会话后 LLM KV 残留陈旧数据导致后续输出乱码（"?"）；
   改为 data=true 真正清零 KV。
2. audition.cpp：音频超过 whisper 位置编码容量（约 30 秒）时 build_graph
   直接 throw，导致整个服务崩溃；改为捕获并返回 false（上层按"无音频"处理），
   服务不再被长音频打挂。

用法（在 A3 上，仓库根目录）：
    python3 scripts/patch_a3_20260824.py
然后增量重编：
    cd build && cmake --build . -j 64
"""

import io
import sys


def patch_file(path: str, old: str, new: str, expected: int) -> None:
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count != expected:
        raise SystemExit(
            f"[FAIL] {path}: expected {expected} occurrence(s), got {count}")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))
    print(f"[OK] {path} patched ({count})")


def main() -> None:
    root = "/workspace/llama.cpp-omni"

    # 1) 会话重置时真正清零 LLM KV 缓存数据（修复长音频后的状态泄漏）
    patch_file(
        f"{root}/tools/server/ws_handler.cpp",
        "llama_memory_clear(mem, /*data=*/false);",
        "llama_memory_clear(mem, /*data=*/true);",
        expected=2,
    )

    # 2) whisper 位置编码越界改为优雅失败，不再 throw 崩掉整个服务
    old = (
        "    ggml_backend_sched_reset(ctx->sched.get());\n"
        "    ggml_cgraph * gf = audition_audio_build_graph(ctx, audios);\n"
        "    ggml_backend_sched_alloc_graph(ctx->sched.get(), gf);\n"
    )
    new = (
        "    ggml_backend_sched_reset(ctx->sched.get());\n"
        "    ggml_cgraph * gf = nullptr;\n"
        "    try {\n"
        "        gf = audition_audio_build_graph(ctx, audios);\n"
        "    } catch (const std::runtime_error & e) {\n"
        "        // [保险] 音频超过 whisper 位置编码容量（约 30 秒）时\n"
        "        // build_graph 会抛 \"Position encoding buffer overflow\"，\n"
        "        // 直接放行会导致整个服务崩溃；捕获后清缓存并返回 false。\n"
        '        LOG_ERR("%s: build graph failed: %s\\n", __func__, e.what());\n'
        "        audition_whisper_clear_kv_cache(ctx);\n"
        "        ctx->whisper_kv_cache.iter = 0;\n"
        "        return false;\n"
        "    }\n"
        "    ggml_backend_sched_alloc_graph(ctx->sched.get(), gf);\n"
    )
    patch_file(f"{root}/tools/omni/audition.cpp", old, new, expected=1)

    print("patch complete; next: cd build && cmake --build . -j 64")


if __name__ == "__main__":
    main()
