# 变更日志 (CHANGELOG)

> 按时间倒序排列（最新在最上面）。
> 每个版本附核心改动说明，方便理解「为什么改、改了什么、好在哪」。

---

## v2.0 —— 全面审查修复（2026-07-16）

**定位：** 针对三轮全量代码审查发现的 36 项问题做系统性修复，覆盖前端与后端、提示词、算法与安全性，使系统趋近生产可用。

---

### P0 — 核心链路修复（9 项）

1. **工时链路打通**
   - 前端 `available_hours` 不再硬编码 `20`，改为按「每日工时 × 距截止日剩余天数」自动推算真实产能
   - schema 新增工时下限校验，钳制 0.5h 下限，杜绝除零 / 负工时
2. **清理死代码**：移除 `Coordinator.hours_per_day` 全局参数及 CLI 入口；它曾覆盖成员级每日工时，导致 CLI 与 Web 行为冲突
3. **排期粒度升级**：Timeline CPM 从整数天升级为半天粒度，小任务不再强制占满一整天；5 个 2h 串行任务从 5 天降至 3 天
4. **导出接口修复**：新增 POST `/export/markdown`、`/export/docx`、`/export/pdf`；前端按钮拆分为三选项
5. **任务状态闭环**：状态切换（completed / blocked）触发后端 `/recompute` 重算；CPM 读取 status，已完成任务不占排期、后续任务自动前移；TimelineTask.status 与 SubTask.status 同步

**本轮自身引入回归的修复：**
6. 补 `routes.py` 缺失的 `import re`（保存按钮必崩 500），加回归测试
7. `index.html` 中 `loadBtn` 接线补回 `document.` 前缀（历史计划按钮失效）

### P1 — 健壮性提升（8 项）

8. **Planner 容错**：`validate_plan` 对依赖环改为断环保留（而非整体丢弃 LLM 结果）；新增 `tolerate_cycle` 参数供编辑场景严格拒绝
9. **Timeline 排期**：修复相邻任务日期重叠；起始日不再排到过去（自动从今天正排并提示延期）
10. **Matcher 提示词**：移除「要求 LLM 做增量负载均衡」的不合理硬约束，改为「系统自动校正」；传给 LLM 的信息包含完整工时
11. **负载系数常量化**：主讲 1.0 / 主答 0.3 / 辅答 0.15 并加文档；`enhance` 补充超载告警
12. **安全加固**：`/save`、`/load`、`/delete` 增加路径穿越防护；`/run` 校验至少 1 名有姓名成员
13. **CLI 对齐**：修正 `available_hours` 推算（按实际剩余天数，不再固定 x14），行为与 Web 一致
14. **edit-members 同步**：修改每日工时时同步重算 `available_hours`，避免超载阈值失真
15. **剩余天数口径统一**：前后端统一使用 `ceil` 向上取整，紧截止日场景不再结论打架

### P2 — 体验优化（7 项）

16. **Word / PDF 导出**：新增 `app/web/exporters.py`，生成结构化文档（任务表、时间线表、QA 表、风险提示），普通用户可直接打印批注
17. **报告渲染增强**：前端 `simpleMarkdown` 升级，支持 Markdown 表格与列表渲染
18. **提示词清理**：删除未被引用的 Timeline 死提示词
19. **Reporter / Interview 省 token**：改为传摘要视图给 LLM，不再塞整份 JSON
20. **skill_score 改进**：标签归一化 + 包含关系奖励（「前端」vs「前端开发」得分从偏低→0.85）
21. **Interview 错误态**：失败抛异常而非返回 `[Error]` 字符串混入问题列表；前端红色提示
22. **editor 重算顺序**：先算新 QA 矩阵，再用新分配回填负责人算 Timeline，确保甘特图与分工一致

### P3 — 打磨（6 项）

23. 版本号统一为 v2.0（原 README 写 v1.2 / 代码 v1.1 / 前端 v1.0）
24. `_fallback_plan` 根据团队总产能等比缩放工时（原写死固定值）
25. `edit-members` 成员变动后标记 report 已过期
26. `requirements.txt` 新增 `python-docx`、`reportlab`
27. `scoring.py` workload 注释同步为实际系数（0.5/0.25 → 0.3/0.15）
28. `planner.py` docstring 同步为弹性表述（5-8 → 1-8）

### 文档对齐

29. README 版本号、测试数（43→45）、timeout（60s→120s）、API 表（补导出端点 + 移除已删的 `hours_per_day`）全部同步

---

## v1.2 —— 进度追踪 + 突发情况处理（2026-07-15）

**定位：** 从「生成计划」升级为「生成 + 跟踪执行」，系统有了完整的生命周期。

### 1. 进度追踪（前端 6 处改动）

**曾经的问题：** SubTask 有 status 字段但只是摆设，用户改了状态没有反馈。

**改动：**
- 进度条：任务列表顶部显示整体进度（如"3/8 (37%)"），绿色填充条实时变化
- 状态联动：标记任务状态后，时间线 Tab 的甘特图同步显示（已完成半透明、进行中蓝框、阻塞红框）
- 新增 blocked 选项：之前只有待开始 / 进行中 / 已完成
- 实时刷新：改状态后进度条立即更新，无需刷新页面
- 阻塞计数：有阻塞任务时进度条下方显示警告

### 2. 突发情况处理（B4 动态编辑 + 成员管理）

**曾经的问题：** 计划一旦生成就"冻住"，成员退出或工时变化要重新跑整个 LLM。

**改动：**
- 突发情况 Tab：成员退出（选人点击移除）、工时变更（改每日工时）
- "应用"按钮调用后端 `/api/edit-members`
- 后端重算 Matcher（B3 确定性算法）+ Timeline（CPM），标记旧 report 已过期

### 其他 v1.2 改动

- B4 编辑面板：任务增删改操作优化
- export_markdown 修复
- 截图指引完善
- （含 v1.1 所有改动：代码质量加固、错误分类细化、Reporter/Interview 精简 prompt、TeamMember 验证器、Timeline 多成员并产）

---

## v1.1 —— 代码质量加固（2026-07-15）

- LLM 超时保护（120s）、错误分类细化（auth / rate_limit / parse / timeout / unknown）
- Reporter / Interview 精简 prompt（摘要视图替代全量 JSON）
- TeamMember 添加 field_validator（工时钳制 0.5h 下限）
- Timeline 支持多成员并行计算产能
- skill_score 标签归一化 + 包含关系奖励
- Coordinator 登录 /api/run 校验
- 配置化 CJK_FONT_PATH 环境变量支持跨平台字体

---

## v1.0 —— 功能完整正式版（2026-07-14）

- Web 前端：TailwindCSS 现代界面、多 Tab 切换、甘特图、实时预警
- Memory 持久化：save / load / list / delete
- 答辩模拟 Agent（10-15 道题，5 维度，优先级标注）
- B3 评分引擎：基于技能标签的确定性分配 + 负载均衡 + overload 检测
- B4 动态编辑：add / remove / update 任务，触发 Timeline(CPM) + Matcher(B3) 重算
- Prompt 全面重构：从简单指令升级为结构化 Prompt Engineering
- 导出接口可用：Markdown / Word / PDF 三种格式

---

## v0.4 —— B3 评分 + B4 编辑 + 精细打磨（2026-07-14）

- B3 确定性评分引擎上线：skill_score + assign_with_balance + enhance
- B4 动态编辑管线：apply_edits → validate_plan → recompute
- Planner 提示词弹性化（基于 jiajia-hua 的 v0.3）：去掉「5-8 任务」硬下限，改为按规模 1-8 个，补充极简场景示例
- Matcher 去除「负载均衡 15%」空口号，改为「系统自动校正」
- Reporter temperature 调至 0.5，提升生成自然度
- Timeline 支持按成员级每日工时折算
- 路径穿越防护 + 空成员校验

---

## v0.3 —— Web 重做 + Memory + 答辩模拟（2026-07-13）

- **Web 前端完全重做**：TailwindCSS 现代界面，多 Tab 切换，甘特图
- **Memory 持久化**：save / load / list / delete 计划
- **答辩模拟 Agent**：5 维度提问，优先级标注
- **Prompt 全面重构**：从简单指令升级为结构化 Prompt Engineering
- **成员可用工时传入 Planner**，Matcher 增加负载均衡提示
- **Reporter 增加风险维度**

---

## v0.2 —— 核心算法 + API 接入（2026-07-13）

- Timeline CPM 关键路径法（纯算法，不依赖 LLM）
- Reporter 纯文本兜底（LLM 失败时拼接基本报告）
- 多 Agent 基类 + LLM 封装（结构化输出 + 重试）
- 确定性评分引擎基础：skill_score + assign_with_balance
- 通路式拓扑 + forward/backward pass 计算关键路径、浮动天数

---

## v0.1 —— 初始骨架（2026-07-12）

**定位：** 项目从零到一，搭好架构骨架，所有 Agent 能跑通。

### 核心设计决策

- **Pydantic model 做接口契约**：所有 Agent 的输入输出都是强类型，格式变了立刻在类型校验时发现
- **LLM 和确定性算法分层**：LLM 负责创造性（拆任务、分配角色、写报告）；确定性算法负责严谨性（关键路径、技能评分、依赖校验）
- **Coordinator 做总调度**：Agent 之间不直接依赖，全部通过 Coordinator 中转

### 基础测试（3 个）
- test_coordinator.py：Coordinator 主链路（mock LLM）
- test_api.py：健康检查接口

## 版本规划

| 版本 | 定位 | 状态 |
|------|------|------|
| v0.1 | 初始骨架 | 已完成 |
| v0.2 | 核心算法实现 | 已完成 |
| v0.3 | Web 重做 + Memory + 答辩模拟 | 已完成 |
| v0.4 | B3 评分 + B4 编辑 + 精细打磨 | 已完成 |
| v1.0 | 功能完整正式版 | 已完成 |
| v1.1 | 代码质量加固 | 已完成 |
| v1.2 | 进度追踪 + 突发情况处理 | 已完成 |
| **v2.0** | **全面审查修复** | **已完成** |
| v2.x | 比赛阶段扩展 | 规划中 |
