# 小组合作智能体

**版本：v4.7** | 最后更新：2026-07-20（合入 v3.5-v3.8 算法修复：负载均衡全局重排、状态切换分工保留、健壮性加固）

## 一句话定位

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**——每个任务带角色化的 QA 归属，计划随现实变化而**实时重算**。

输入「课程信息 + 团队成员 + 截止日」，系统自动：**拆解任务 → CPM 排期 → 技能匹配分配答辩角色 → 生成报告**，并支持随时增删改任务、即时重算。

## 系统架构

```
AssignmentInput (课程 / 成员 / 截止日 / 每日工时)
     |
     v
+-----------+      +-----------+      +------------+      +-----------+
|  Planner  |----->|  Matcher  |----->|  Timeline  |----->|  Reporter |
+-----------+      +-----------+      +------------+      +-----------+
     |                  |                   |                  |
     v                  v                   v                  v
 validate_plan     skill_score        CPM + 日工时折算        FullPlan
 去重/去环/清依赖   enhance/workload   倒排日期 + 浮动天数      |
                                                              |
                    +-----------------------------------------+
                    |
                    v
               B4 动态编辑
               add / remove / update -> 重算 Timeline + Matcher
```

| Agent | 职责 | 实现 | LLM 失败兜底 |
|-------|------|------|-------------|
| Planner | 课程信息 -> 1-8 子任务（按规模弹性） | LLM | 确定性 5 阶段兜底 |
| Matcher | 任务 -> 主讲/主答/辅答 | LLM + B3 评分增强 | B3 确定性贪心分配 |
| Timeline | 任务依赖 -> 倒排日期 | 纯 CPM 算法 | 无需兜底（纯数学） |
| Reporter | 全部结果 -> 答辩报告 | LLM | 纯文本拼接兜底 |

**设计原则**：LLM 负责创造性拆解，确定性算法负责正确性保证（关键路径、技能评分、负载均衡、依赖校验）。每个 Agent 失败都有兜底，主链路永不中断。

## 版本演进

| 版本 | 日期 | 定位 |
|------|------|------|
| v4.7 | 2026-07-20 | 合入 v3.5-v3.8 算法修复：负载均衡全局重排、状态切换分工保留、健壮性加固 |
| v4.6 | 2026-07-19 | 任务短句与 AI 返回容错：限制条件不再单独生成任务，轻微 JSON 错误本地修复 |
| v4.5 | 2026-07-19 | 长课程手册解析：PDF 段落边界保留、成果识别、推送/Vlog 递归拆解 |
| v4.4 | 2026-07-19 | AI 调整建议按钮可拖拽 + 生成按钮反馈修复 |
| v4.3 | 2026-07-19 | 修复首次提交不走快速模式、默认调 AI |
| v4.2 | 2026-07-19 | 领域化兜底与最终协作视图恢复 |
| v4.1 | 2026-07-18 | 恢复工作台视觉与共享业务服务（project_service.py 分层） |
| v4.0 | 2026-07-18 | 任务拆解与分工双确认工作流：文件上传、可编辑草案、确认后分工 |
| v3.8 | 2026-07-19 | 负载均衡跳出局部最优（全局联合枚举重排）+ 甘特窄条带日期修复 |
| v3.7 | 2026-07-19 | 状态来回切换不再丢失原责任分工与匹配度 |
| v3.6 | 2026-07-19 | 提示词与代码自洽 + 测试可复现性（pytest.ini）+ 文档计数统一 |
| v3.5 | 2026-07-18 | 异步化收尾 + enhance 行为与提示词一致性 + Planner 兜底自适应 |
| v3.4 | 2026-07-17 | 报告自动重生 + 表格渲染修复 + 空鉴权快速兜底 |
| v3.3 | 2026-07-17 | 完成不重排 + 负向技能标签识别（不想做 X 的人不会被派去做 X） |
| v3.2 | 2026-07-17 | 用户验收六连击修复：版本同步/状态归零/分工均衡/报告渲染/甘特图末位 |
| v3.1 | 2026-07-17 | 审查复核选择性修复（13 项）：禁词乱码/状态切换重算/全员参与保证 |
| v3.0 | 2026-07-16 | 七轮审查全量修复：健壮性/一致性/测试/死代码清理 |
| v2.0 | 2026-07-16 | 深度审查修复（30 项问题）：工时链路打通/排期现实化/导出完整/状态闭环 |
| v1.2 | 2026-07-15 | 进度追踪 + 突发情况处理（成员退出/工时变更） |
| v1.1 | 2026-07-15 | 代码质量加固（6 个暗雷修复）：LLM 超时/错误分类/Structured Output 降级 |
| v1.0 | 2026-07-14 | 功能完整正式版：成员级日工时 + Prompt 全量重构 + Web UI 全面升级 |
| v0.4 | 2026-07-14 | 技能评分增强 + 动态编辑 + 精细打磨 |
| v0.3 | 2026-07-14 | Web 重做 + 计划持久化 + 答辩模拟 |
| v0.2 | 2026-07-14 | 核心算法：CPM 排期 + 技能评分 + B3/B4 角色匹配与动态编辑 |
| v0.1 | 2026-07-12 | 初始骨架：接口契约 + Agent 基类 + LLM 封装 + Coordinator + FastAPI |

详细版本变更记录见 [CHANGELOG.md](CHANGELOG.md)，包含每一版的问题分析、代码对照、原因和收益说明。

## 项目结构

```
competition/
├── app/
│   ├── main.py               # FastAPI 入口
│   ├── config.py             # 全局配置
│   ├── coordinator.py        # 总调度：主链路编排
│   ├── editor.py             # B4：动态编辑 + 重算
│   ├── cli.py                # 单 Agent 调试入口
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
├── tests/                    # 80 个单元/集成测试
├── memory/                   # B2 计划持久化
├── docs/                     # 项目文档
└── requirements.txt
```

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/run` | 生成完整计划（成员级每日工时） |
| POST | `/api/analyze-files` | 上传并分析任务要求文件 |
| POST | `/api/draft` | 仅生成任务拆解草案，不分工 |
| POST | `/api/confirm-draft` | 确认草案后自动分工 |
| POST | `/api/manual-assignment` | 保存手动拖拽后的负责人和协作者 |
| POST | `/api/draft/mutate` | 结构化修改任务草案（网页与未来 Agent 共用） |
| POST | `/api/workload` | 统一计算成员负载、占比和分工建议 |
| POST | `/api/chat` | 基于当前方案实时问答 |
| POST | `/api/edit` | B4：应用编辑并重算 |
| POST | `/api/interview` | B1：AI 答辩模拟（v0.3 新增） |
| POST | `/api/save` | B2：保存计划到 memory |
| POST | `/api/export/markdown` | 导出当前计划为 Markdown |
| POST | `/api/export/docx` | 导出当前计划为 Word 文档 |
| POST | `/api/edit-members` | 成员变动处理（退出/工时变更/新增成员）并重算 |
| POST | `/api/recompute` | 状态变更后实时重算排期与分工 |
| POST | `/api/export/pdf` | 导出当前计划为 PDF 文档 |
| GET | `/api/plans` | B2：列出已保存计划 |
| GET | `/api/load/{filename}` | B2：载入计划 |
| DELETE | `/api/plans/{filename}` | B2：删除计划 |
| GET | `/api/health` | 健康检查 |

## 快速启动

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑填入 LLM_API_KEY
python -m app.main     # 默认 http://127.0.0.1:8000
```

## 运行测试

```bash
python -m pytest -v    # 80 passed
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
