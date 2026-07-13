# 小组合作智能体 - 课程作业

## 一句话定位

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**，每个任务都带**角色化的 QA 归属**-- 计划随现实变化而重算。


## 当前进度（截至 2026-07-13）

| 编号 | 模块 | 状态 | 负责人 | 备注 |
|------|------|------|--------|------|
| Step 0 | JSON 接口契约 (`schemas.py`) | done | B | 所有 Agent 输入输出已定义 |
| A1 | LLM 调用封装 (`client.py`) | done | B | Structured Output + 重试 |
| A2 | Coordinator 主链路 (`coordinator.py`) | done | B | Planner->Matcher->Timeline->Report |
| A5 | FastAPI + 只读 Web (`main.py` / `routes.py` / `index.html`) | done | B | 前后端骨架已通 |
| - | Agent 基类 + Reporter | done | B | `base.py` / `reporter.py` |
| B1 | 答辩模拟 Agent | done | B | 骨架已写，Prompt 待迭代 |
| - | 测试 (test_coordinator / test_api) | done | B | mock 测试已写 |
| - | Planner Agent | **skeleton** | **A** | 骨架已搭，**Prompt 和校验逻辑待完成** |
| - | Matcher Agent | **skeleton** | **C** | 骨架已搭，**QA 矩阵 + 可解释性待完成** |
| - | Timeline Agent | **skeleton** | **C** | 骨架已搭，**关键路径计算待完成** |
| B2 | Memory (保存/加载计划 JSON) | not started | B | |
| B3 | 完整角色匹配 | not started | C | |
| B4 | 协作图动态编辑 | not started | B | 落后即砍 |

## 近期变更

- `BRANCHES.md`: 将 `git add -A` 改为 `git add .`，提交前加了 `git status` 检查，避免误提交不相关文件

## 队友待办

### 队友 A -- Planner Agent

1. **Prompt 迭代**：当前 `prompts.py` 里的 `PLANNER_SYSTEM` 是基础版，需要根据实际输出质量反复调优，让 Planner 拆出合理的 5-8 个子任务（工时、依赖都要靠谱）
2. **输出校验**：在 `planner.py` 的 `run()` 里添加校验逻辑 -- task id 唯一性、依赖不能指向不存在的任务、依赖环检测
3. **自测**：用真实课程信息跑几次，确保输出符合 `PlanOutput` schema

### 队友 C -- Timeline + Matcher Agent

1. **Matcher (A3)**：当前 `matcher.py` 只调了 LLM，需要扩展 QA 责任矩阵生成逻辑 -- 答辩细分 + 谁主讲/谁主答/谁辅答
2. **Timeline (A4)**：当前 `timeline.py` 只调了 LLM，需要补充关键路径计算逻辑（关键路径上的任务标红）
3. **可解释性**：每个匹配结果附一句"为什么张三主答第3章"；关键路径说明怎么算出来的
4. **Prompt 迭代**：`MATCHER_SYSTEM` 和 `TIMELINE_SYSTEM` 都需要根据实际输出反复调
5. **Report 格式化**：检查 `reporter.py` 的输出是否符合预期，必要时调整 `REPORTER_SYSTEM` prompt

### 共同注意事项

- **先读 `schemas.py`**：所有 Agent 的输入输出 JSON 格式都在 `app/models/schemas.py`，这是接口契约，不要随意改字段
- **开发流程**：按 `BRANCHES.md` 操作 -- 每天先 merge main，下班前 push 自己的分支
- **有问题找 B**：接口冲突或跑不通时先停下来对齐，不要硬合


## 项目结构

```
competition/
├── .env.example              # 环境变量模板（复制为 .env 后编辑）
├── .gitignore
├── BRANCHES.md               # 分支策略说明
├── README.md
├── requirements.txt
├── app/                      # 主应用
│   ├── __init__.py
│   ├── main.py               # FastAPI 入口（B 负责）
│   ├── config.py             # 全局配置
│   ├── coordinator.py        # 总调度：编排 A2 主链路（B 负责）
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py        # JSON 接口契约 ← 第0步，必须先定
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py           # Agent 基类
│   │   ├── planner.py        # Planner    ← 队友A 端到端负责
│   │   ├── matcher.py        # Matcher    ← 队友C 端到端负责
│   │   ├── timeline.py       # Timeline   ← 队友C 端到端负责
│   │   ├── reporter.py       # Report 格式化
│   │   └── interview_sim.py  # 答辩模拟   ← B1，B 负责
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py         # A1: LLM 调用封装
│   │   └── prompts.py        # 所有 Prompt 模板
│   └── web/
│       ├── __init__.py
│       ├── routes.py         # FastAPI 路由
│       ├── templates/
│       │   └── index.html    # 演示用前端
│       └── static/
│           └── style.css
├── scripts/
│   └── setup_git.sh          # Git 初始化脚本
├── tests/                    # 单元测试
│   ├── __init__.py
│   ├── test_coordinator.py
│   └── test_api.py
├── memory/                   # B2: 计划状态的保存/加载
├── docs/                     # 项目文档（docx + MVP 拆解方案）
└── notebooks/                # 实验/探索用
```

## 三人分工

| 人 | 角色 | 端到端负责 | 占提交比重 |
|---|---|---|---|
| **B（你）** | 软件工程 | 骨架 + LLM封装 + FastAPI/Web + Coordinator + 集成 + 答辩模拟(B1) + Memory(B2) | ~50%+ |
| **A** | Agent 设计 | **Planner Agent**：课程→子任务JSON | ~25% |
| **C** | 知识增强 | **Timeline + QA矩阵**：倒排/关键路径 + 责任矩阵 + 可解释性 | ~25% |

## 分支策略

团队协作分支说明见 [BRANCHES.md](BRANCHES.md)。简要原则：
- 每人一个独享开发分支，互不干扰
- **只有你（B）** 能合并到 `main`
- 每天至少集成一次，避免最后一天爆炸

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制并编辑）
cp .env.example .env

# 启动服务
python -m app.main
```

## MVP 节奏

- **D1**：定数据模型 + LLM封装 + FastAPI骨架 + Hello World + 最小垂直切片
- **D2**：Planner + Matcher Prompt 迭代，主链路跑通
- **D3**：Timeline + QA 矩阵 + 可解释性
- **D4**：只读Web + 答辩模拟 + 边界用例测试
- **D5**：联调 + 演示彩排 + 收尾

## 提交范围（课程作业版）

A 类（必做）：A1~A5
B 类（加做）：B1 优先，B4 落后即砍
C 类：比赛阶段再扩展
