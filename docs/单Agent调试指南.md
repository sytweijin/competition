# 单 Agent 调试指南

> 本文档面向三名团队成员（A / B / C），说明如何在不启动整个 Web 服务的情况下，
> 单独运行某一个 Agent，方便调试 Prompt 和观察输出质量。

## 前置准备

### 1. 确认 Python 环境

```powershell
D:\Python311\python.exe --version
# 应输出 Python 3.11.x
```

### 2. 安装依赖（首次或更新后）

```powershell
cd C:\Users\ty\Desktop\competition
D:\Python311\python.exe -m pip install -r requirements.txt
```

### 3. 确认 .env 配置

项目根目录下应有 `.env` 文件，内容类似：

```env
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-max
```

**注意**：Planner / Matcher / Reporter / Interview 需要调 LLM，`.env` 必须配好。
Timeline 是纯算法，不需要联网。

---

## 命令格式

所有命令的统一格式：

```powershell
D:\Python311\python.exe -m app.cli <子命令> [参数]
```

查看帮助：

```powershell
D:\Python311\python.exe -m app.cli --help
D:\Python311\python.exe -m app.cli planner --help
```

---

## 各 Agent 详细用法

### 1. Planner（任务拆解）

**负责人：A**

Planner 是整条链路的起点。输入课程信息和团队成员，输出 5-8 个子任务。

```powershell
D:\Python311\python.exe -m app.cli planner `
  --course "软件工程" `
  --desc "开发一个小组协作管理系统，要求前后端分离，包含用户管理、任务分配、进度追踪功能" `
  --members "张三:前端;React;TypeScript,李四:后端;Python;FastAPI,王五:数据库;MySQL;PPT" `
  --deadline 2026-08-01 `
  --extra "希望每个成员的任务量尽量均衡，总工时不超过 60 小时"
```

**参数说明：**

| 参数 | 必填 | 格式 | 说明 |
|------|------|------|------|
| `--course` | 是 | 纯文本 | 课程名称 |
| `--desc` | 否 | 纯文本 | 课程描述/作业要求，越详细 LLM 拆解越准 |
| `--members` | 是 | `名字:技能1;技能2,名字2:技能3` | 成员列表，逗号分隔，冒号后是技能（分号分隔） |
| `--deadline` | 是 | `YYYY-MM-DD` | 截止日期 |
| `--extra` | 否 | 纯文本 | 额外要求（如均衡分工、技术偏好等） |

**输出**：直接打印 JSON，包含 tasks 数组（每个任务有 id/name/estimated_hours/dependencies/required_skills）。

**调 Prompt 的方法**：修改 `app/llm/prompts.py` 中的 `PLANNER_SYSTEM` 和 `PLANNER_USER_TEMPLATE`，
然后用上面的命令反复跑，观察输出质量变化。

---

### 2. Matcher（答辩角色分配）

**负责人：C**

为每个任务分配主讲、主答、辅答。需要先有 Planner 的输出（plan.json）。

#### 第一步：把 Planner 输出存为文件

跑完 Planner 后，把输出 JSON 复制保存为 `plan.json`：

```powershell
# 方法一：直接重定向
D:\Python311\python.exe -m app.cli planner --course "软件工程" --desc "小组项目" --members "张三:前端,李四:后端" --deadline 2026-08-01 > plan.json

# 方法二：手动复制输出到 plan.json
```

#### 第二步：跑 Matcher

```powershell
D:\Python311\python.exe -m app.cli matcher `
  --plan-file plan.json `
  --members "张三:前端;React,李四:后端;Python,王五:数据库;PPT"
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `--plan-file` | 是 | Planner 输出的 JSON 文件路径 |
| `--members` | 是 | 成员列表，格式同 Planner |

**输出**：JSON，包含 assignments 数组（每个任务分配 presenter/qa_primary/qa_support）+ workload 负载摘要。

**调 Prompt**：修改 `app/llm/prompts.py` 中的 `MATCHER_SYSTEM` 和 `MATCHER_USER_TEMPLATE`。

---

### 3. Timeline（时间线排期）

**纯算法，不需要 LLM，随时可跑。**

根据任务依赖和工时，用 CPM 关键路径法生成倒排时间线。

```powershell
D:\Python311\python.exe -m app.cli timeline `
  --plan-file plan.json `
  --deadline 2026-08-01 `
  --members "张三:4,李四:6,王五:3"
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `--plan-file` | 是 | Planner 输出的 JSON 文件路径 |
| `--deadline` | 是 | 截止日期 YYYY-MM-DD |
| `--members` | 否 | 成员及每日工时，格式 `名字:小时数`。不传则默认每人每天 4 小时 |

**输出**：JSON，包含每个任务的 start_date/end_date/is_critical/float_days，以及关键路径和总工期。

---

### 4. Reporter（报告生成）

将 Plan + Timeline + QA 矩阵合并为面向答辩评委的专业报告。

```powershell
D:\Python311\python.exe -m app.cli reporter `
  --plan-file plan.json `
  --timeline-file timeline.json `
  --qa-file qa.json
```

需要三个输入文件：Planner 输出、Timeline 输出、Matcher 输出。
分别存为 `plan.json`、`timeline.json`、`qa.json`。

**调 Prompt**：修改 `app/llm/prompts.py` 中的 `REPORTER_SYSTEM`。

---

### 5. Interview（答辩模拟）

模拟答辩评委提问，生成 10-15 道可能的答辩问题。

```powershell
D:\Python311\python.exe -m app.cli interview `
  --plan-file plan.json `
  --qa-file qa.json `
  --requirements "重点关注技术选型理由和分工合理性"
```

**参数说明：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `--plan-file` | 是 | Planner 输出 |
| `--qa-file` | 是 | Matcher 输出 |
| `--requirements` | 否 | 自定义要求（如评委关注点、重点模块等） |

**调 Prompt**：修改 `app/llm/prompts.py` 中的 `INTERVIEW_SYSTEM`。

---

### 6. Full（完整链路）

一次性跑完 Planner 到 Matcher 到 Timeline 到 Reporter 全链路。

```powershell
D:\Python311\python.exe -m app.cli full `
  --course "软件工程" `
  --desc "小组协作管理系统" `
  --members "张三:前端,李四:后端,王五:数据库" `
  --deadline 2026-08-01 `
  --extra "任务量尽量均衡"
```

---

## 典型调试流程

### 场景一：A 调 Planner Prompt

1. 修改 `app/llm/prompts.py` 中的 `PLANNER_SYSTEM`
2. 跑 Planner 看效果：

```powershell
D:\Python311\python.exe -m app.cli planner --course "软件工程" --desc "管理系统" --members "张三:前端,李四:后端" --deadline 2026-08-01
```

3. 不满意就回到步骤 1 继续改
4. 满意后把输出存为 `plan.json`，交给 C 测 Matcher

### 场景二：C 调 Matcher Prompt

前提：已有 `plan.json`（A 的 Planner 输出）

1. 修改 `app/llm/prompts.py` 中的 `MATCHER_SYSTEM`
2. 跑 Matcher：

```powershell
D:\Python311\python.exe -m app.cli matcher --plan-file plan.json --members "张三:前端,李四:后端"
```

3. 观察分配是否合理（负载均衡、技能匹配）

### 场景三：B 调 Timeline 算法

Timeline 不依赖 LLM，改完 `app/agents/timeline.py` 直接跑：

```powershell
D:\Python311\python.exe -m app.cli timeline --plan-file plan.json --deadline 2026-08-01 --members "张三:4,李四:6"
```

观察关键路径、工期、浮动天数是否合理。

---

## 常见问题

**Q: 报错 ModuleNotFoundError: No module named 'app'**
A: 确保在项目根目录 `C:\Users\ty\Desktop\competition` 下执行命令。

**Q: 报错 AuthenticationError 或 401**
A: 检查 `.env` 中的 `LLM_API_KEY` 是否正确。

**Q: LLM 返回的内容不对 / 格式错误**
A: 这是 Prompt 调优的正常过程。多跑几次，调整 `prompts.py` 中的指令措辞。

**Q: Timeline 不需要 API Key？**
A: 对，Timeline 是纯 CPM 算法（`app/agents/timeline.py`），不调 LLM，断网也能跑。

**Q: 怎么把输出存成文件给下一个 agent 用？**
A: 用重定向 `> 文件名.json`，或者手动复制终端输出的 JSON 到文件。
