# planner.py docstring: 5-8 改为弹性
path = "app/agents/planner.py"
with open(path, "r", encoding="utf-8") as f:
    src = f.read()
src = src.replace(
    "负责：输入课程/要求/组员 → 输出 5-8 子任务（含工时、依赖）",
    "负责：输入课程/要求/组员 → 输出弹性子任务（1-8 个，含工时、依赖）",
)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("planner DONE")
