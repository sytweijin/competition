# 分支策略说明

> 三人并行开发 + 5 天冲刺，分工清楚的人不需要复杂 Git 流程。

## 分支概览

| 分支 | 负责人 | 用途 | 合并权限 |
|------|--------|------|----------|
| main | B | 稳定可演示版，代码最终汇总到这里 | 只有 B |
| coordinator | B | 骨架/LLM封装/FastAPI/Coordinator/Web/Timeline/Reporter/集成/答辩模拟 | B → main |
| agent/planner | 队友A | Planner Agent：课程→子任务JSON | B → main |
| agent/matcher | 队友C | Matcher（QA责任矩阵 + 可解释性） | B → main |
| fix/* | 谁修谁用 | Bug 修复分支 | B → main |
| docs/* | 谁写谁用 | 文档专用分支 | B → main |

## 工作流程

### 各人每天的工作

1. 早上先拉最新代码：
   git checkout main && git pull origin main
   git checkout 自己的分支
   git merge main

2. 写自己的代码

3. 下班前提交并推送：
   git add .
   git status
   git commit -m "这里写做了什么"
   git push origin 自己的分支名

### 合并到 main（只有 B 操作）

B 每天至少做一次集成检查：
   git checkout main
   git merge origin/agent/planner
   git merge origin/agent/matcher
   git merge origin/coordinator
   解决冲突后提交并推送

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
| fix: | fix: 修复 planner 日期格式错误 |
| chore: | chore: 更新 requirements.txt |
| docs: | docs: 更新 README 接口说明 |

## 首次 Git 设置

打开 Git Bash，运行：
   cd /c/Users/ty/Desktop/competition
   bash scripts/setup_git.sh

然后会提示关联 GitHub 远程仓库。