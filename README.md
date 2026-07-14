# 小组合作智能体 — 课程作业

## 一句话定位

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**——每个任务带角色化的 QA 归属，计划随现实变化而**实时重算**。

输入「课程信息 + 团队成员 + 截止日」，系统自动：**拆解任务 → CPM 排期 → 技能匹配分配答辩角色 → 生成报告**，并支持随时增删改任务、即时重算。

## 系统架构

```
AssignmentInput (课程/成员/截止日)
        │
        ▼
 ┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌────────────┐
 │  Planner    │───▶│  Matcher    │───▶│   Timeline   │───▶│  Reporter  │
 │ (LLM 拆任务) │    │ (LLM+B3评分) │    │ (CPM 关键路径)│    │ (LLM+兜底) │
 └─────────────┘    └─────────────┘    └──────────────┘    └────────────┘
        │                  │                   │                  │
        ▼                  ▼                   ▼                  ▼
   validate_plan     skill_score          倒排日期+浮动        FullPlan
   (去重/去环/清依赖) enhance/workload     回填负责人
                                                              │
                          ┌───────────────────────────────────┘
                          ▼
                     B4 动态编辑 (add/remove/update) → 重算 Timeline + Matcher
```

**设计原则**：LLM 负责创造性拆解，确定性算法负责正确性保证（关键路径、技能评分、负载均衡、依赖校验）。每个 Agent 失败都有兜底，主链路永不中断。

## 当前进度（截至 2026-07-14）

| 编号 | 模块 | 状态 | 负责人 | 说明 |
|------|------|------|--------|------|
| Step 0 | JSON 接口契约 (`schemas.py`) | ✅ done | B | 含 B3/B4 扩展模型 |
| A1 | LLM 调用封装 (`client.py`) | ✅ done | B | Structured Output + 重试 |
| A2 | Coordinator 主链路 (`coordinator.py`) | ✅ done | B | Planner→Matcher→Timeline→Report |
| A5 | FastAPI + Web (`main.py`/`routes.py`/`index.html`) | ✅ done | B | Gantt+QA表+负载条+Memory+编辑 |
| A3/A4 | Matcher（QA矩阵） | ✅ done | B+C | LLM + B3 评分增强 + 成员名校验 |
| — | Timeline Agent (`timeline.py`) | ✅ done | B | CPM 算法，环容错，可配置工时 |
| — | Reporter Agent (`reporter.py`) | ✅ done | B | LLM + 纯文本兜底 |
| B1 | 答辩模拟 Agent (`interview_sim.py`) | ✅ done | B | 5 维度，优先级标注 |
| B2 | Memory (`routes.py` save/load/list/delete) | ✅ done | B | 计划持久化 + Web 管理 |
| **B3** | **完整角色匹配 (`scoring.py`)** | ✅ **done** | B | 技能相似度评分 + 负载均衡 + workload |
| **B4** | **协作图动态编辑 (`editor.py`)** | ✅ **done** | B | add/remove/update + 重算 |
| — | Planner Agent | skeleton | A | 骨架 + 兜底校验已就位，Prompt 待 A 调优 |
| — | 测试 (24 个) | ✅ done | B | CPM/Scoring/Editor/Coordinator/API 全覆盖 |

## 近期变更（最新迭代）

### 代码质量与健壮性
- **Agent 返回类型统一**：`run()` 显式声明 `PlanOutput | AgentError`，消除类型契约不一致
- **计划校验兜底**：新增 `validation.py`——去重 task id、剔除悬空依赖、Kahn 环检测，Planner 输出与 B4 编辑共用
- **Matcher 成员名校验**：剔除 LLM 编造的不存在成员名，自动回退到真实成员
- **Matcher 降级**：LLM 不可用时自动启用 B3 确定性匹配，不再返回空矩阵

### Timeline 精细化（响应「时间倒排更灵活」）
- `hours_per_day` **可配置**（请求级参数），工时→天数折算不再硬编码
- **依赖环容错**：不再直接返回空，而是断环继续排期并标注风险
- 输出每个任务的 **`float_days`（浮动天数）**，前端展示非关键任务的弹性
- QA 矩阵负责人**回填**到时间线，Gantt 条上显示谁负责

### B3 完整角色匹配（新）
- `scoring.py`：基于技能标签相似度（SequenceMatcher）的确定性评分引擎
- 贪心 + 负载均衡分配主讲/主答/辅答，避免某人任务过重
- 生成 **workload 负载摘要**（主讲=全工时，主答=0.5，辅答=0.25），前端可视化条形图

### B4 协作图动态编辑（新）
- `editor.py`：对 FullPlan 应用 `add/remove/update` 编辑序列
- 编辑后**自动重算** Timeline(CPM) 与 Matcher(B3)，实现「计划随现实重算」
- 新增 `/api/edit` 路由 + DELETE `/api/plans/{filename}`

### Web 精致化
- Gantt 加入**日期刻度网格 + 关键路径图例 + 浮动天数 + 负责人**
- 新增**成员负载条形图**（B3 workload 可视化）
- 新增**历史计划**弹窗（载入/删除）、**保存当前**按钮（B2 完整暴露）
- 渐变标题、空状态、响应式表格、hover 微交互

### 测试
- 从 2 个测试扩展到 **24 个**：Timeline CPM（7）、Scoring（7）、Editor（8）、Coordinator、API

## 项目结构

```
competition/
├── app/
│   ├── main.py               # FastAPI 入口
│   ├── config.py             # 全局配置
│   ├── coordinator.py        # 总调度：主链路编排
│   ├── editor.py             # B4：动态编辑 + 重算
│   ├── models/schemas.py     # JSON 接口契约（含 B3/B4 模型）
│   ├── agents/
│   │   ├── base.py           # Agent 基类
│   │   ├── planner.py        # Planner（A 负责 Prompt）
│   │   ├── matcher.py        # Matcher：QA 矩阵 + 校验
│   │   ├── scoring.py        # B3：技能评分 + 负载均衡
│   │   ├── timeline.py       # Timeline：CPM 关键路径
│   │   ├── reporter.py       # 报告格式化
│   │   ├── interview_sim.py  # B1：答辩模拟
│   │   └── validation.py     # 计划校验（去重/去环/清依赖）
│   ├── llm/
│   │   ├── client.py         # LLM 调用封装
│   │   └── prompts.py        # Prompt 模板
│   └── web/
│       ├── routes.py         # FastAPI 路由（run/edit/save/load）
│       ├── templates/index.html
│       └── static/style.css
├── tests/                    # 24 个单元/集成测试
├── memory/                   # B2 计划持久化
├── docs/                     # 项目文档
└── requirements.txt
```

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/run` | 生成完整计划（可选 `hours_per_day`） |
| POST | `/api/edit` | B4：应用编辑并重算 |
| POST | `/api/save` | B2：保存计划到 memory |
| GET | `/api/plans` | B2：列出已保存计划 |
| GET | `/api/load/{filename}` | B2：载入计划 |
| DELETE | `/api/plans/{filename}` | B2：删除计划 |
| GET | `/api/health` | 健康检查 |

## 三人分工

| 人 | 角色 | 端到端负责 | 占比 |
|---|---|---|---|
| **B** | 软件工程 | 骨架 + LLM封装 + Coordinator + Timeline + Reporter + Matcher增强(B3) + 动态编辑(B4) + Web + Memory(B2) + 答辩模拟(B1) + 测试 | ~55% |
| **A** | Agent 设计 | **Planner**：Prompt 调优 + 输出质量（校验兜底已由 B 提供） | ~22% |
| **C** | 知识增强 | **Matcher QA 矩阵**：Prompt + 可解释性（评分增强已由 B 提供） | ~23% |

## 快速启动

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑填入 LLM_API_KEY
python -m app.main     # 默认 http://127.0.0.1:8000
```

## 运行测试

```bash
python -m pytest -q    # 24 passed
```

## 分支策略

详见 [BRANCHES.md](BRANCHES.md)。每人一个独享分支，只有 B 合并 `main`，每天至少集成一次。

## 提交范围

- **A 类（必做）**：A1~A5 ✅
- **B 类（加做）**：B1 答辩模拟 ✅、B2 Memory ✅、B3 完整角色匹配 ✅、B4 协作图动态编辑 ✅
- **C 类**：比赛阶段再扩展