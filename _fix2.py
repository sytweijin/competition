path = "README.md"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()

# 1. 测试数 43 → 45
src = src.replace("43 个单元/集成测试", "45 个单元/集成测试")
src = src.replace("43 passed", "45 passed")

# 2. API 表补 export 端点（在 save 后面插一行）
old_api = (
    "| POST | `/api/save` | B2：保存计划到 memory |\n"
    "| GET | `/api/plans` | B2：列出已保存计划 |"
)
new_api = (
    "| POST | `/api/save` | B2：保存计划到 memory |\n"
    "| POST | `/api/export/markdown` | 导出当前计划为 Markdown |\n"
    "| POST | `/api/export/docx` | 导出当前计划为 Word 文档 |\n"
    "| POST | `/api/export/pdf` | 导出当前计划为 PDF 文档 |\n"
    "| GET | `/api/plans` | B2：列出已保存计划 |"
)
src = src.replace(old_api, new_api)

# 3. 测试数 43 → 45（文档目录那行）
src = src.replace("├── tests/                    # 43 个单元/集成测试", "├── tests/                    # 45 个单元/集成测试")

with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("DONE")
