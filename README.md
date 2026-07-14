# 小组合作智能体 — 课程作业

**版本：v1.0** | 最后更新：2026-07-14

## 一句话定位

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**——每个任务带角色化的 QA 归属，计划随现实变化而**实时重算**。

输入「课程信息 + 团队成员 + 截止日」，系统自动：**拆解任务 → CPM 排期 → 技能匹配分配答辩角色 → 生成报告**，并支持随时增删改任务、即时重算。

## 系统架构

```
AssignmentInput (课程/成员/截止日/每日工时)
        │
        ▼
 ┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌────────────┐
 │  Planner    │───▶│  Matcher    │───▶│   Timeline   │───▶│  Reporter  │
 │ (LLM 拆任务) │    │ (LLM+B3评分) │    │ (CPM+成员产能)│    │ (LLM+兜底) │
 └─────────────┘    └─────────────┘    └──────────────┘    └────────────┘
        │                  │                   │                  │
        ▼                  ▼                   ▼                  ▼
   validate_plan     skill_score      按负责人日工时折算       FullPlan
   (去重/去环/清依赖) enhance/workload  倒排日期+浮动+回填
                                                              │
                          ┌───────────────────────────────────┘
                          ▼
                     B4 动态编辑 (add/remove/update) → 重算 Timeline + Matcher
```

**设计原则**：LLM 负责创造性拆解，确定性算法负责正确性保证（关键路径、技能评分、负载均衡、依赖校验）。每个 Agent 失败都有兜底，主链路永不中断。

## 当前进度（截至 2026-07-14）

| 编号 | 模块 | 状态 | 负责人 | 说明 |
|------|------|------|--------|------|
| Step 0 | JSON 接口契约 (`schemas.py`) | ✅ done | B | 含 B3/B4 扩展模型，v0.3 新增 `daily_available_hours` |
| A1 | LLM 调用封装 (`client.py`) | ✅ done | B | Structured Output + 重试 |
| A2 | Coordinator 主链路 (`coordinator.py`) | ✅ done | B | v0.3 成员信息透传 Timeline + Planner |
| A5 | FastAPI + Web (`main.py`/`routes.py`/`index.html`) | ✅ done | B | v0.3 全新 UI + 答辩模拟接口 |
| A3/A4 | Matcher（QA矩阵） | ✅ done | B+C | LLM + B3 评分增强 + 成员名校验 + sanitize 修复 |
| — | Timeline Agent (`timeline.py`) | ✅ done | B | v0.3 CPM + **成员级日工时折算**，环容错 |
| — | Reporter Agent (`reporter.py`) | ✅ done | B | LLM + 纯文本兜底 |
| B1 | 答辩模拟 Agent (`interview_sim.py`) | ✅ done | B | 5 维度，优先级标注，v0.3 接入 Web API |
| B2 | Memory (`routes.py` save/load/list/delete) | ✅ done | B | 计划持久化 + Web 管理 |
| **B3** | **完整角色匹配 (`scoring.py`)** | ✅ **done** | B | 技能相似度评分 + 负载均衡 + workload |
| **B4** | **协作图动态编辑 (`editor.py`)** | ✅ **done** | B | add/remove/update + 重算 |
| — | Prompt 模板 (`prompts.py`) | ✅ done | B | v0.3 全量重构：结构化 Prompt Engineering |
| — | Planner Agent | skeleton | A | 骨架 + 兜底校验已就位，Prompt 待 A 调优 |
| — | 测试 (24 个) | ✅ done | B | CPM/Scoring/Editor/Coordinator/API 全覆盖 |

## 近期变更

### v1.0.0（2026-07-14）— 全面审查修复 + B4 前端落地

#### Bug 修复
- routes.py 未传 user_requirements 给 InterviewSimAgent（已修复）
- editor.py 重算 timeline 时未传 members（已修复）
- routes.py 导出函数 f-string 语法错误（已修复）
- schemas.py 删除未使用的 SkillLevel/MemberRole 枚举
- routes.py 清理冗余 hours_per_day 字段

#### 新功能
- **B4 编辑计划 Tab**：前端可直接增删改任务并一键重算 Timeline + Matcher
- **工期预警系统**：自动检测工期超截止日期、缓冲不足、成员负载超标/偏高
- **进度追踪预留**：SubTask/TimelineTask 新增 status 字段（待开始/进行中/已完成）
- **报告 Markdown 渲染**：simpleMarkdown 解析标题/粗体/斜体/列表
- **Markdown 导出**：一键下载完整计划报告为 .md 文件
- **历史计划搜索**：Modal 内支持按文件名实时筛选
- **答辩模拟用户要求**：传入评委关注点/重点模块等自定义要求
- **Planner 多方案建议**：Prompt 增加 alternatives 字段，鼓励 LLM 提供备选拆解思路

### v1.0（2026-07-14）— 代码质量 + UI 全面升级

#### 核心功能增强
- **成员级日工时**：`TeamMember` 新增 `daily_available_hours` 字段，不同成员可以设置不同的每日可用时间（如 6h、3h、4h），Timeline 按任务负责人的实际产能折算天数，彻底告别硬编码的"每人每天4小时"
- **Coordinator 透传成员信息**：将完整的成员列表传入 TimelineAgent，支持按成员实际可用工时计算排期
- **Planner 输入升级**：从简单姓名列表升级为含技能标签 + 可用工时的富文本，LLM 能更好地理解团队产能
- **答辩模拟 API**：新增 `/api/interview` 接口，前端可直接调用 AI 生成答辩问题
- **`additional_requirements` 修复**：之前额外要求字段未传入 `AssignmentInput`，现已修复

#### Prompt 全量重构
- Planner：增加拆解原则（粒度适中、依赖清晰、技能匹配、覆盖完整、可验证），输出格式说明更严格
- Matcher：增加分配原则（技能匹配优先、负载均衡、主讲主答可不同、辅答互补、全员参与）
- Reporter：增加风险多维度分析（工期/人员/技术风险 + 建议），要求用数据说话
- 所有 Prompt 从简短指令升级为结构化 Prompt Engineering 格式

#### Bug 修复
- **Matcher sanitize**：修复了 `_sanitize` 中指向不存在任务的分配被错误保留的 bug（现在正确跳过）
- **版本号统一**：`FullPlan.version` 和 `editor.py` 统一升至 `0.3.0`

#### Web UI 全面重构
- **左右分栏布局**：输入面板 sticky 在左侧，结果区域在右侧
- **TailwindCSS + Lucide 图标**：现代化的 UI 组件和图标系统
- **Tab 切换输出**：任务计划 / 时间线 / QA 矩阵 / 报告 / 答辩模拟，五个 Tab 清晰组织
- **新增输入字段**：每人每天工时（全局）、成员每日工时（个人）、额外要求
- **成员行可删除**：每个成员行有移除按钮
- **甘特图美化**：红色=关键路径，绿色=可浮动，右侧显示关键/浮动/负责人标签
- **负载条形图**：渐变蓝色条形图展示成员负载分布
- **QA 表格**：蓝色=主讲，琥珀色=主答，匹配度百分比徽章
- **答辩模拟**：集成在输出 Tab 中，一键生成 AI 答辩问题
- **历史计划弹窗**：现代化 Modal 设计，支持载入/删除
- **空状态优化**：引导性图标 + 文字说明

### v0.2.0 — B3 角色匹配 + B4 动态编辑
- B3 完整角色匹配：技能评分 + 负载均衡 + workload 可视化
- B4 协作图动态编辑：add/remove/update + 自动重算 Timeline + Matcher
- Timeline CPM 算法：环容错 + 可配置工时 + 浮动天数 + 负责人回填
- Web：Gantt 图表 + 负载条形图 + Memory 管理 + 编辑接口

### v0.1.0 — 初始骨架
- 接口契约（schemas.py）+ Agent 基类 + LLM 封装
- Coordinator 主链路 + FastAPI + 简易 Web
- 计划校验（去重/去环/清依赖）+ 24 个测试

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
│   │   ├── timeline.py       # Timeline：CPM 关键路径 + 成员产能
│   │   ├── reporter.py       # 报告格式化
│   │   ├── interview_sim.py  # B1：答辩模拟
│   │   └── validation.py     # 计划校验（去重/去环/清依赖）
│   ├── llm/
│   │   ├── client.py         # LLM 调用封装
│   │   └── prompts.py        # Prompt 模板（v0.3 结构化重构）
│   └── web/
│       ├── routes.py         # FastAPI 路由（run/edit/save/load/interview）
│       ├── templates/index.html  # TailwindCSS + Lucide + Tab 布局
│       └── static/style.css  # 补充样式
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
| POST | `/api/interview` | B1：AI 答辩模拟（v0.3 新增） |
| POST | `/api/save` | B2：保存计划到 memory |
| GET | `/api/plans` | B2：列出已保存计划 |
| GET | `/api/load/{filename}` | B2：载入计划 |
| DELETE | `/api/plans/{filename}` | B2：删除计划 |
| GET | `/api/health` | 健康检查 |

## 三人分工

| 人 | 角色 | 端到端负责 | 占比 |
|---|---|---|---|
| **B** | 软件工程 | 骨架 + LLM封装 + Coordinator + Timeline + Reporter + Matcher增强(B3) + 动态编辑(B4) + Web + Memory(B2) + 答辩模拟(B1) + Prompt + 测试 | ~55% |
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
python -m pytest -v    # 39 passed
```

## 分支策略

详见 [BRANCHES.md](BRANCHES.md)。每人一个独享分支，只有 B 合并 `main`，每天至少集成一次。

## 提交范围

- **A 类（必做）**：A1~A5 ✅
- **B 类（加做）**：B1 答辩模拟 ✅、B2 Memory ✅、B3 完整角色匹配 ✅、B4 协作图动态编辑 ✅
- **C 类**：比赛阶段再扩展

## 单 Agent 调试

不需要启动 Web 服务，可以单独运行任意一个 Agent 来调试 Prompt 或算法。
详细用法见 [单 Agent 调试指南](docs/单Agent调试指南.md)。

快速示例：
```bash
python -m app.cli planner --course "软件工程" --desc "小组项目" --members "张三:前端,李四:后端" --deadline 2026-08-01
```

## 变更历史

详细版本变更记录见 [CHANGELOG.md](CHANGELOG.md)，包含每一版的改动内容、原因分析和收益说明。
