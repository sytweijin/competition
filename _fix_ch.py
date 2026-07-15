path = "CHANGELOG.md"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()
entry = (
    "\n"
    "### 第四轮审查修补（workbuddy 全量重读确认）\n\n"
    "34. **修正 planner.py docstring**：`输出 5-8 子任务` → `输出弹性子任务（1-8 个）`，与提示词对齐。\n"
    "35. **README 测试数同步**：从 43 → 45。\n"
    "36. **README API 表补导出端点**：新增 `/api/export/markdown`、`/api/export/docx`、`/api/export/pdf`。\n"
)
src = src.rstrip() + "\n" + entry + "\n"
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("DONE")
