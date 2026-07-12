# 小组合作智能体 - 课程作业

## 一句话定位

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**，每个任务都带**角色化的 QA 归属**——计划随现实变化而重算。

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
