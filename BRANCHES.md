# 分支策略说明

> 三人并行开发 + 5 天冲刺，分工清楚的人不需要复杂 Git 流程。

**当前版本：v1.0** | 最后更新：2026-07-14

## 分支概览

| 分支 | 负责人 | 用途 | 合并权限 |
|------|--------|------|----------|
| main | B | 稳定可演示版，代码最终汇总到这里 | 只有 B |
| coordinator | B | 骨架/LLM封装/FastAPI/Coordinator/Web/Timeline/Reporter/集成/答辩模拟/Prompt | B → main |
| agent/planner | 队友A | Planner Agent：课程→子任务JSON | B → main |
| agent/matcher | 队友C | Matcher（QA责任矩阵 + 可解释性） | B → main |
| fix/* | 谁修谁用 | Bug 修复分支 | B → main |
| docs/* | 谁写谁用 | 文档专用分支 | B → main |

## 当前开发状态

### 已完成（v1.0）
- ✅ 完整的多 Agent 主链路（Planner → Matcher → Timeline → Reporter）
- ✅ B1 答辩模拟（5 维度 + 优先级 + Web API）
- ✅ B2 Memory 持久化（save/load/list/delete）
- ✅ B3 完整角色匹配（技能评分 + 负载均衡 + workload 可视化）
- ✅ B4 协作图动态编辑（add/remove/update + 自动重算）
- ✅ Timeline CPM 算法（成员级日工时折算 + 环容错 + 浮动天数）
- ✅ Web UI 全面重构（TailwindCSS + Tab 布局 + 答辩模拟集成）
- ✅ Prompt 结构化重构（Planner/Matcher/Reporter 全量优化）
- ✅ 24 个单元/集成测试（全部通过）
- ✅ 代码质量：类型契约统一 + 校验兜底 + 错误处理

### 待 A 完成
- 🔲 Planner Prompt 调优（当前骨架已可用，A 可进一步优化输出质量）

### 待 C 完成
- 🔲 Matcher QA 矩阵 Prompt 调优（当前 B3 评分增强已可用，C 可进一步优化可解释性）

## 工作流程

### 各人每天的工作

1. 早上先拉最新代码：
   ```bash
   git checkout main
   git pull origin main
   git checkout 自己的分支
   git merge main
   ```

2. 写自己的代码

3. 下班前提交并推送：
   ```bash
   git add .
   git status
   git commit -m "这里写做了什么"
   git push origin 自己的分支名
   ```

### 合并到 main（只有 B 操作）

B 每天至少做一次集成检查：
```bash
git checkout main
git merge origin/agent/planner
git merge origin/agent/matcher
git merge origin/coordinator
# 解决冲突后提交并推送
```

### 出现冲突怎么办

- 小冲突：B 在合并时直接解决
- 接口（schemas.py）冲突：停下来，三个人对齐接口字段后再合
- 发现跑不通：B 先不 push，通知作者修完再合

## 提交信息格式

| 前缀 | 举例 |
|------|------|
| feat/planner: | feat/planner: 添加了依赖环检测 |
| feat/matcher: | feat/matcher: 关键路径标红 |
| feat/coordinator: | feat/coordinator: 添加输出校验+重试 |
| feat/timeline: | feat/timeline: 成员级日工时折算 |
| feat/web: | feat/web: TailwindCSS 重构 + Tab 布局 |
| feat/prompt: | feat/prompt: Planner 结构化重构 |
| fix: | fix: 修复 matcher sanitize bug |
| chore: | chore: 更新 requirements.txt |
| docs: | docs: 更新 README 到 v1.0 |

## 版本发布

每次重大更新在 README 顶部标注版本号，并在 `schemas.py` 的 `FullPlan.version` 字段同步更新：

- v1.0（2026-07-14）：全面审查修复 + B4 前端落地 + 预警系统 + 导出
- v1.0（2026-07-14）：成员级日工时 + UI 重构 + Prompt 重构 + 答辩模拟 API
- v0.2.0：B3 角色匹配 + B4 动态编辑
- v0.1.0：初始骨架

## 首次 Git 设置

打开 Git Bash，运行：
```bash
cd /c/Users/ty/Desktop/competition
bash scripts/setup_git.sh
```

然后会提示关联 GitHub 远程仓库。
