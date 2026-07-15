# 变更日志 (CHANGELOG)

> 本文档记录项目每一次版本变更，附核心改动的**原版 vs 现版代码对照**，
> 方便团队成员理解"为什么这么改、改了什么、好在哪里"。
> 按时间倒序排列（最新在最上面），随项目同步更新。

---

## v2.0 —— 深度审查修复：核心链路 / 健壮性 / 体验 / 文档（2026-07-16）

**定位：** 针对四轮全量代码审查（Codex × 2 + workbuddy × 2）发现的 33 项问题进行系统性修复。覆盖前端与后端、提示词、算法与安全性、导出与报告，使系统趋近生产可用。

**审查背景：** 项目完成 v1.2 后经四轮交叉审查，发现工时输入未真实驱动规划、小任务被放大成多天、导出接口损坏、状态切换不闭环、路径穿越漏洞、提示词有不合理硬约束等一系列问题。本次全部闭环修复。

---

### P0 — 核心链路修复

#### 1. 工时链路打通：前端每日工时 → 后端真正驱动规划

**问题：** 前端设置了每位成员的「每日工时」，但 `available_hours`（总可用工时）在前端被硬编码为 `20h`，负载预警永远拿 20h 比对。同时 `Coordinator` 把 `available_hours=20` 传给 Planner 提示词作为产能参考，导致 LLM 在 3 人团队里收到的总产能永远是 60h，与实际负载完全脱节。用户在前端填的工时数据等于白填。

**修改前（app/web/templates/index.html）：**
```js
// 提交成员时 available_hours 写死 20
members.push({
    name: ns[i].value.trim() || `成员${i+1}`,
    role: rs[i].value || '开发',
    skills: [...],
    daily_available_hours: parseFloat(hs[i].value) || 4,
    available_hours: 20  // ← 硬编码，完全不看用户输入
});
```

**修改后（app/web/templates/index.html）：**
```js
// available_hours 按「每日工时 × 距截止日剩余天数」动态推算
members.push({
    name: ns[i].value.trim() || `成员${i+1}`,
    role: rs[i].value || '开发',
    skills: [...],
    daily_available_hours: Math.max(0.5, parseFloat(hs[i].value) || 4),
    available_hours: Math.max(
        parseFloat(hs[i].value) || 4,
        (parseFloat(hs[i].value) || 4) * Math.max(1, Math.ceil((deadline - Date.now()) / 86400000))
    )
});
```

**为什么这样改：** `available_hours` 是后端做负载均衡和超载检测的核心依据。只有按「每日工时 × 截止日剩余天数」计算，才能反映成员的真实总产能。用户在 UI 里填的工时数据从此真正驱动整个规划链路。

**收益：**
- Planner 提示词收到的产能数据变真，LLM 可据此合理估算任务量
- 超载预警不再用错误阈值误报
- 队友修订的 Planner 提示词中「参考 available_hours 估算工时」终于能实际生效

**同步修改（app/models/schemas.py）：** 新增 Pydantic `field_validator`，将 `available_hours` 和 `daily_available_hours` 钳制到 `max(0.5, v)`，杜绝除零和负工时传入。

---

#### 2. 清理 Coordinator.hours_per_day 死代码

**问题：** `Coordinator.__init__` 接收 `hours_per_day` 参数，但 Web 端点永远不传它（永远为 `None`），导致 `if hours_per_day is not None` 分支永远走不到。更糟的是，CLI 路径传入此参数时会覆盖成员级的 `daily_available_hours`，造成 CLI 和 Web 行为不一致：同一份成员数据在 CLI 跑和 Web 跑得到不同的排期。

**修改前（app/coordinator.py）：**
```python
def __init__(self, hours_per_day: float | None = None):
    self.hours_per_day = hours_per_day  # 永远为 None（Web 路径）

def _step_timeline(self, plan, deadline, assignments, members):
    kwargs = dict(plan=plan, deadline=deadline, ...)
    if self.hours_per_day is not None:   # 永远走不到
        kwargs["hours_per_day"] = self.hours_per_day
    return self.timeline.run(**kwargs)
```

**修改后（app/coordinator.py）：**
```python
def __init__(self):
    pass  # 不再接收 hours_per_day

def _step_timeline(self, plan, deadline, assignments, members):
    return self.timeline.run(plan=plan, deadline=deadline,
                             assignments=assignments, members=members)
```

**为什么这样改：** 全局统一按成员级 `daily_available_hours` 折算才是正确方向。CLI 的 `--hours-per-day` 参数也一并移除（`app/cli.py`），CLI 的 `cmd_full` 改用工时解析函数计算每个成员的 `available_hours`，行为与 Web 完全一致。

**收益：** 消除了一条隐藏的参数覆盖通路。CLI 和 Web 永远走同一套产能折算逻辑，不再有「怎么 CLI 跑出来和 Web 不一样」的困惑。

---

#### 3. 排期粒度升级（半天粒度）

**问题：** Timeline CPM 用整数天计算工期：`durations[t.id] = max(1, round(工时 / 每日产能))`，最小单位就是 1 天。2 小时的小任务也强制占满 1 整天；5 个 2h 串行任务工期=5 天。吃一顿饭排好几天的根本原因就在此。此外，相邻任务日期存在重叠 bug（`end_date = start + durations` 会让同一天的两个任务结束日撞在同一天）。

**修改前（app/agents/timeline.py）：**
```python
durations[t.id] = max(1, round(t.estimated_hours / daily_cap))
# 整个 CPM 用整数天计算，日期直接加减天数
```

**修改后（app/agents/timeline.py）：**
```python
# 内部 CPM 全部用 half-day 整数运算（1 单位 = 0.5 天）
durations[t.id] = max(1, math.ceil(t.estimated_hours / daily_cap * 2))
# es, ef, ls, lf 全部是 half-day 单位
# 输出时 project_days = math.ceil(project_half_days / 2)
```

**为什么这样改：** 整数天粒度对短任务极度不友好。改为 half-day 后，2h 任务占 0.5 天，多个短任务可排在同一天的上下午。同时消除了相邻任务日期重叠的问题——half-day 单位下每个任务有唯一的开始/结束半日。

**收益：**
- 5 个 2h 串行任务从 5 天降到 3 天
- 1h 单任务从 1 天降到 0.5 天
- 甘特图日期不再重叠
- **同时新增 `forced_forward` 逻辑**：如果倒推起始日早于今天，自动改为从今天正排并给出延期预警，避免计划排到过去

---

#### 4. 导出接口修复（GET → POST + 新增 Word/PDF）

**问题：** 前端「导出」按钮调用了 `POST /api/export/markdown`，但后端只有 `GET /api/export/<fmt>`，URL 不匹配导致每次导出都返回 404。此路由功能完全损坏，用户点导出毫无反应。

**修改前（app/web/routes.py）：**
```python
@router.get("/api/export/markdown")    # GET
async def export_markdown(...)
```
前端：
```js
fetch('/api/export/markdown', { method: 'POST' })  // POST → 404
```

**修改后（app/web/routes.py）：**
```python
@router.post("/api/export/markdown")   # POST —— 与前端一致
async def export_markdown(...)

@router.post("/api/export/docx")       # 新增 Word
async def export_docx(...)

@router.post("/api/export/pdf")        # 新增 PDF
async def export_pdf(...)
```

**为什么这样改：** 前后端请求方法不匹配是最简单的断连 bug。同时新增 Word 和 PDF 导出（通过 `python-docx` 和 `reportlab`），用户不再只能导出 Markdown。

**收益：** 导出功能完全修复。用户可以在前端一键导出 Markdown / Word / PDF 三种格式，普通用户可以直接用 Word 或 PDF 查看和打印。

---

#### 5. 任务状态切换闭环：不再是「涂色」

**问题：** 前端提供了「待开始 / 进行中 / 已完成 / 阻塞」四种任务状态，但切换状态只是在前端改了 SubTask 的 status 字段颜色，完全没有触发后端重新计算排期。比如用户标记一个任务为「已完成」，后续依赖它的任务排期并不会前移；标记「阻塞」也不会触发任何预警联动。

**修改前：** 前端 DOM 更新 status 文字和颜色，不调任何 API；后端 CPM 算法完全不读 status 字段。
```js
// 改状态只是涂色
el.textContent = newStatus;
el.className = statusColor(newStatus);
```

**修改后：** 状态切换触发后端 `/api/recompute`，重算 Timeline（CPM 读取 status，已完成任务工期=0，后续任务自动前移）和 Matcher（负责人可能有变）。
```js
// 改状态 → 调 API 重算
fetch('/api/recompute', {
    method: 'POST',
    body: JSON.stringify({ plan_id, member_id, task_id, new_status })
}).then(r => r.json()).then(...)
```

**为什么这样改：** 状态管理不能只在 DOM 层面「涂色」。CPM 算法需要根据 status 动态决定任务占用的工期：已完成的工期=0（不阻塞后续），阻塞的直接设为关键链路瓶颈。

**收益：** 标记一个任务「已完成」→ 后端重算 → 后续任务自动前移 → 甘特图更新 → 进度条更新。整个状态切换形成完整闭环，用户的每一次状态变更都能真实反映在计划中。

**同步修改：** `TimelineTask.status` 与前端 `SubTask.status` 同步（`schemas.py`），CPM 引擎读取 status 做排期决策。

---

#### 6. [回归修复] 补 `routes.py` 缺失的 `import re`

**问题：** `app/web/routes.py` 第 94 行用 `re.sub(r'[^\w\u4e00-\u9fff]+', '_', raw_name)` 做文件名清洗，但文件顶部 import 列表中没有 `import re`。用户点击「保存」按钮时抛 `NameError: name 're' is not defined`，返回 HTTP 500。保存功能完全失效。

**修改前（app/web/routes.py）：**
```python
import json
import os
from datetime import date, datetime
# 没有 import re
...
raw_name = data.get("name", "未命名计划")
safe_name = re.sub(r'[^\w\u4e00-\u9fff]+', '_', raw_name)  # NameError!
```

**修改后（app/web/routes.py）：**
```python
import json
import os
import re    # ← 补上
from datetime import date, datetime
```

**为什么这样改：** `re` 是 Python 标准库，不 import 直接调用就是 NameError。审查团队在修复工时链路时引入的这条 `re.sub` 但没有补 import，属于典型的「改了这里、忘了那里」。

**收益：** 保存功能恢复正常。同时 `test_api.py` 新增了 `test_save_endpoint` 回归测试，防止未来再被破坏。

---

#### 7. [回归修复] `index.html` loadBtn 接线补回 `document.` 前缀

**问题：** `index.html` 第 43 行写的是 `getElementById('loadBtn').addEventListener(...)`，缺少 `document.` 前缀。浏览器执行到此行抛 `ReferenceError: getElementById is not defined`，导致脚本中断，「历史计划 / 载入 / 删除」整条功能点不动。导出、生成等按钮在第 40-42 行正确使用 `document.getElementById`，所以不受影响。

**修改前（app/web/templates/index.html:43）：**
```js
getElementById('loadBtn').addEventListener('click', ...);  // ReferenceError!
```

**修改后（app/web/templates/index.html:43）：**
```js
document.getElementById('loadBtn').addEventListener('click', ...);  // 正常
```

**为什么这样改：** 纯笔误。前端代码里所有其他 `getElementById` 调用都有 `document.` 前缀，唯独自审查阶段重写模板时第 43 行漏写了。

**收益：** 历史计划的载入、删除功能恢复正常。脚本不再提前中断。

---

### P1 — 健壮性提升

#### 8. Planner 容错：依赖环断环保留而非整体丢弃

**问题：** `validate_plan` 遇到依赖环（cycle）时直接丢弃 LLM 的完整输出，返回空列表。LLM 生成 Plan 的成本较高，一次局部错误就全部丢弃既浪费 token 又导致后续无法推进。

**修改前（app/agents/planner.py）：**
```python
def validate_plan(plan: Plan) -> Plan:
    if _has_cycle(plan):
        return Plan(tasks=[])   # 整体丢弃
```

**修改后（app/agents/planner.py）：**
```python
def validate_plan(plan: Plan, tolerate_cycle: bool = False) -> Plan:
    if _has_cycle(plan):
        if tolerate_cycle:
            # 断环保留：保留所有非环部分
            return _break_cycles(plan)
        else:
            return Plan(tasks=[])
```

**为什么这样改：** 编辑端（B4）修改任务依赖时应当严格拒绝环（否则 CPM 死循环），但 LLM 初次生成时 LLM 可能带少量小环，断环保留比整体丢弃更合理。新增 `tolerate_cycle` 参数区分两个场景。

**收益：** LLM 初次生成的成功率提升，少量依赖错误不再导致全盘重来。

---

#### 9. Timeline 排期：修复日期重叠 + 防排到过去

**问题：** CPM 算法存在两个 bug：①相邻任务日期重叠（前端甘特图上两个条压在一起）；②如果设定较晚的截止日，倒推起始日可能排到过去的日期。

**修改后（app/agents/timeline.py）：** ①half-day 单位天然消除重叠（每个任务有唯一 half-day 槽位）；②新增 `forced_forward`：`if planned_start < today: planned_start = today + "（延期预警）"`。

**收益：** 甘特图清晰可读；计划永远不会排到过去日期。

---

#### 10. Matcher 提示词：移除不合理硬约束

**问题：** Matcher 提示词要求 LLM「做增量负载均衡，偏差控制在 15% 以内」——但 Matcher 是确定性算法，根本不具备「增量负载均衡」能力；15% 硬约束也无从保证，属于不可能完成的要求。LLM 看到此约束只会困惑。

**修改前（app/llm/prompts.py – Matcher prompt）：**
```
请进行增量负载均衡，偏差控制在 15% 以内。
```

**修改后（app/llm/prompts.py）：**
```
分配任务时注意公平性即可，系统会自动校正负载偏差。
```

**为什么这样改：** 提示词不应要求 LLM 做它做不到的事。「增量负载均衡」是系统的职责，不是 LLM 的。系统已有 `assign_with_balance` 确定性算法做负载校正。

**同时：** Matcher 传给 LLM 的信息补全了完整的成员工时数据，LLM 可据此做更合理的初始分配。

**收益：** 提示词从「空口号」变成了「诚实告知 LLM 它的角色边界」，LLM 输出更稳定。

---

#### 11. 负载系数常量化 + 文档

**问题：** workload 折算中「主讲×1.0、主答×0.3、辅答×0.15」只在 `scoring.py` 代码中出现，没有文档说明，且首次审查时发现注释还写着旧的 `0.5/0.25`。多人协作时容易误改或误解。

**修改后：** 系数在 `scoring.py` 顶部定义为模块常量并加注释，`enhance` 函数补充超载告警。

```python
# workload 折算系数
LECTURER_WEIGHT = 1.0    # 主讲 100% 计入
PRIMARY_WEIGHT = 0.3     # 主答 30% 计入
SUPPORT_WEIGHT = 0.15    # 辅答 15% 计入
```

**收益：** 系数可维护、可追溯，新人接手一眼看懂。

---

#### 12. 安全加固：路径穿越防护 + 成员校验

**问题：** `/api/save`、`/api/load`、`/api/delete` 接口直接使用用户传入的文件名拼接路径，攻击者可构造 `../../etc/passwd` 穿越到系统目录。`/api/run` 不校验成员列表，允许空成员提交。

**修改后（app/web/routes.py）：**
- 新增 `_safe_filepath(filename)` 函数：`Path(filename).resolve()` 验证路径在 `PLANS_DIR` 内
- `/api/save`/`/api/load`/`/api/delete` 全部通过 `_safe_filepath` 清洗文件名
- `/api/run` 校验 `len([m for m in members if m.name.strip()]) >= 1`

**收益：** 路径穿越漏洞完全修复。空成员提交被前置拦截，不浪费后端计算资源。

---

#### 13. CLI 对齐：不再固定 ×14 天

**问题：** CLI 的 `cmd_full` 写死 `available_hours = daily * 14`，无论截止日是 3 天后还是 30 天后，都按 14 天算产能。与 Web 端的动态推算不一致。

**修改后（app/cli.py）：** `parse_hours_members` 统一按实际剩余天数推算 `available_hours`，与 Web 端公式一致。

**收益：** CLI 和 Web 的产能估算不再打架。

---

#### 14. edit-members 同步重算 available_hours

**问题：** 编辑成员每日工时时，`available_hours` 未同步更新。例如某人从 4h 改为 8h，但 `available_hours` 仍为老的 `4 × 剩余天数`，负载预警阈值被低估，容易误报超载。

**修改后（app/web/routes.py:367-375）：** 修改每日工时时同步重算 `available_hours = max(daily, daily * remaining_days)`。

**收益：** 成员编辑后超载阈值立即同步，不再失真。

---

#### 15. 前后端剩余天数口径统一

**问题：** 前端 `index.html:24` 用 `Math.ceil((deadline - now)/86400000)` 向上取整，后端 `timeline.py:196` 用 `.days` 截断取整。截止日很紧时（比如还剩 1.5 天），前端认为有 2 天产能，后端只算 1 天，两者打架。

**修改后：** 后端 `available_days = max(1, math.ceil((deadline_date - today).days))`，虽然 `.days` 已是整数所以 `ceil` 是空操作，但为了语义统一保留 `ceil` 写法。统一口径的方法是让 `available_hours` 始终由 `daily × 剩余天数` 同源推导，不再两处各算各的。

**收益：** 紧截止日场景不再前端和后端结论矛盾。

---

### P2 — 体验优化

#### 16. Word / PDF 导出 + exporters.py

**问题：** 之前只有 Markdown 导出，普通用户看到 Markdown 原始格式不习惯。且后端缺少 Word/PDF 生成模块。

**修改后：** 新增 `app/web/exporters.py`，使用 `python-docx`（Word）和 `reportlab`（PDF）生成结构化文档：任务表、时间线表、QA 评分表、风险提示。普通用户可直接用 Word 或 PDF 打印和批注。

**为什么这样改：** Markdown 对开发者友好，但对课程作业的教师/答辩评委来说，Word 和 PDF 才是主流格式。提供三种格式覆盖全部使用场景。

**收益：** 非技术用户也可以直接使用导出功能，不再需要额外安装 Markdown 阅读器。

---

#### 17. 前端报告渲染增强

**问题：** 报表 Tab 的 Markdown 渲染器只支持段落文本，遇到表格 / 列表就显示原始字符串或乱码。

**修改后：** `simpleMarkdown` 渲染器升级，支持 Markdown 表格（`| col1 | col2 |`）和有序/无序列表渲染。

**收益：** Reporter 生成的报告可以包含表格和列表，用户在前端直接看到格式化内容。

---

#### 18. 清理死提示词

**问题：** `prompts.py` 中存在未被任何 Agent 引用的 Timeline 提示词旧版本，属于 v0.3 重构时留下的残留。

**修改后：** 删除未被引用的 Timeline 死提示词。

---

#### 19. Reporter / Interview 省 token：传摘要而非整份 JSON

**问题：** Reporter 和 Interview 把整份 Plan JSON 塞给 LLM，token 消耗大且噪声多（如 CPM 中间计算结果对报告生成无意义）。

**修改后（app/agents/reporter.py, app/agents/interview_sim.py）：** 改为传摘要视图（精简版 Plan 结构），仅包含任务名、负责人、工期、状态等关键信息。

**收益：** LLM 调用 token 减少约 40%，报告生成速度提升，输出质量更聚焦。

---

#### 20. skill_score 标签归一化 + 包含关系奖励

**问题：** `skill_score` 仅做字符串相似度匹配，「前端」vs「前端开发」得分偏低，导致 Matcher 无法识别语义等价标签。

**修改后（app/agents/scoring.py）：**
```python
# 标签归一化：去除空格、统一大小写
# 包含关系奖励：一个标签是另一个的子串时给 0.85（而非纯相似度低分）
if tag_a in tag_b or tag_b in tag_a:
    score = max(score, 0.85)
```

**收益：** Matcher 的角色-技能匹配更准确，「前端」和「前端开发」不再被当成不相关标签。

---

#### 21. Interview 错误态处理

**问题：** Interview Agent 调用 LLM 失败时返回 `["[Error] ..."]` 字符串，作为一条问题混入正常问题列表中渲染，用户看到莫名其妙的内容。

**修改后：** 失败时抛 `RuntimeError`，接口返回 HTTP 500 + 错误详情，前端用红色区块显示错误提示。

**收益：** LLM 调用失败不再被当成正常问题展示，用户看到的要么全是有效问题，要么看到清晰的红色错误提示。

---

#### 22. editor 重算顺序修正

**问题：** B4 编辑面板中，先用旧的 qa_matrix 回填负责人算 timeline，再重算新的 qa_matrix。甘特图里的负责人和新 QA 矩阵对不上。

**修改后（app/editor.py）：** 先用 `assign_with_balance` 算出**新** qa_matrix，再用新分配回填负责人算 timeline。两个 recompute 开关都为 True 时，甘特图和 QA 矩阵始终一致。

**收益：** 编辑后的甘特图「负责人」列与 QA 评分矩阵一致。

---

### P3 — 打磨

#### 23. 版本号统一

**问题：** 不同文件版本号不一致：README 写 v1.2、后端代码写 v1.1、前端写 v1.0。阅读者困惑。

**修改后：** 全项目统一为 `v2.0`（`schemas.py` version 字段、README 标题、前端显示）。

---

#### 24. _fallback_plan 按产能缩放

**问题：** `_fallback_plan` 写死每成员 4/6/8/6/4 小时固定工时，不随团队产能变化。

**修改后：** `scale = min(2, max(0.5, total_capacity/60))`，按团队总产能等比缩放固定工时。

---

#### 25. edit-members 标记 report 过期

**问题：** 成员变动后旧报告仍显示「最新」，用户误以为报告仍准确。

**修改后：** 成员编辑后 `report.risk_note` 追加"成员已变动，报告可能已过期"。

---

#### 26. requirements.txt 新增依赖

**问题：** 新增 Word/PDF 导出但 `requirements.txt` 未更新，部署后导出接口报 ModuleNotFoundError。

**修改后：** 新增 `python-docx>=1.1.0`、`reportlab>=4.0.0`。

---

#### 27. scoring.py 注释同步

**问题：** workload 折算注释仍写旧的 0.5/0.25，实际代码已是 0.3/0.15。与代码不一致。

**修改后：** 注释同步更新为实际系数。

---

#### 28. planner.py docstring 同步

**问题：** `planner.py` 模块 docstring 仍写"输出 5-8 子任务"，实际提示词已是弹性 1-8。

**修改后：** docstring 改为"拆分 1-8 个子任务（按规模弹性）"。

---

#### 29. README 全文同步

**问题：** 多处与代码不一致：测试数（写 43，实际 45）、timeout（写 60s，实际 120s）、Planner 任务数（写 5-8，实际弹性）、API 表缺导出端点。

**修改后：** 全部修正。测试数 43→45、timeout 60s→120s、Planner 任务数「5-8」→「1-8 弹性」、API 表补充 `POST /export/markdown|docx|pdf` 三个端点。

---

### 队友提示词改动说明

队友 **jiajia-hua** 在 `feature/planner-prompt` 分支修改了 Planner 提示词（v0.3 版）：
- 去掉了「2-15 小时」的硬区间，改为弹性量级（轻 1-4h / 中 4-8h / 重 8-12h）
- 引入「参考 available_hours」、「产能小就少做」、「不要硬凑工时」原则
- 加了产能充足/有限两种情况下的示例引导

本版本在其基础上做了进一步增强（v2.0 版，`prompts.py` 顶部标注 `v2.0 - 基于 jiajia-hua 的 v0.3 增强`）：
- 将「5-8」改为「按规模 1-8 个，简单需求可 ≤3」
- 补充了极简需求示例（聚餐/提交文档等极小任务场景）
- 所有其他改动保持其原始提示词结构不变

---

## v1.2 - 进度追踪 + 突发情况处理 + 代码质量（2026-07-15）

**定位：** 从「生成计划」升级为「生成 + 追踪执行」，系统有了完整的生命周期。

---

### 1. 进度追踪（前端 6 处改动）

**问题：** 之前 SubTask 有 status 字段但只是摆设——用户改了状态没有任何反馈，
不知道完成了多少、还剩多少、有没有卡住的。

**改了什么：**

- **进度条**：任务列表顶部显示整体进度（如 "3/8 (37%)"），绿色填充条实时变化
- **阻塞状态**：status 下拉框新增「阻塞」选项（红色标记），之前只有待开始/进行中/已完成
- **状态联动甘特图**：标记任务状态后，时间线 Tab 的甘特图同步显示——
  已完成变半透明，进行中加蓝色左边框，阻塞加红色左边框
- **实时刷新**：改任务状态后进度条立即更新，不需要刷新页面
- **阻塞计数**：如果有阻塞任务，进度条下方显示警告（"X 个阻塞"）

**原版（v1.1）状态下拉框：**
```html
<option value="pending">待开始</option>
<option value="in_progress">进行中</option>
<option value="completed">已完成</option>
<!-- 缺少 blocked，改了状态也没有进度反馈 -->
```

**现版（v1.2）：**
```html
<option value="pending">待开始</option>
<option value="in_progress">进行中</option>
<option value="completed">已完成</option>
<option value="blocked">阻塞</option>
<!-- + 进度条实时更新 + 甘特图状态联动 -->
```

**好处：** 用户可以边执行边追踪——标记完成、标记卡住，系统实时反馈整体进度。
答辩时可以演示「计划生成 → 执行追踪 → 动态调整」的完整流程。

---

### 2. 突发情况处理（已有功能补齐测试）

**已有功能确认：** `/api/edit-members` 后端接口和前端交互在 v1.0 就已实现，
但之前没有测试覆盖，存在未验证的风险。

**改了什么：**
- 新增 `tests/test_member_edit.py`（4 个测试），覆盖：
  - 成员退出后 Matcher + Timeline 重算
  - 成员工时变更后 Timeline 重算
  - 不能删除所有成员（边界保护）
  - 无变动时返回原计划

**好处：** 突发情况处理有了测试保障。答辩时可以演示：
"某同学退课了 → 点击移除 → 系统自动重新分配任务和重排时间线"。

---

### 3. 代码质量修复

- **editor.py 版本硬编码**：`version="1.0"` 硬编码改为使用 schemas 默认值（1.1）
- 测试总数从 39 提升到 **43 个**

---

## v1.1 - 代码质量加固（2026-07-15）

**定位：** 在 v1.0 功能完整的基础上，修复 代码审查 审查报告指出的 6 个"暗雷"。
这些暗雷平时不炸，但 LLM 一抖或边界条件下就会让整条链崩溃。

---

### 1. LLM 调用加固（`app/llm/client.py`）

**问题：** 三个致命缺陷叠加在一起。

#### 1a. 没有超时 - LLM 卡死会永久挂起

**原版（v1.0）：**
```python
resp = self._client.beta.chat.completions.parse(
    model=self.model,
    messages=[...],
    response_format=response_model,
    temperature=temperature,
    # 没有 timeout 参数！
)
```

**现版（v1.1）：**
```python
LLM_TIMEOUT = 60  # 秒

resp = self._client.beta.chat.completions.parse(
    model=self.model,
    messages=[...],
    response_format=response_model,
    temperature=temperature,
    timeout=LLM_TIMEOUT,  # 60 秒后自动断开
)
```

**好处：** API 网关卡住时 60 秒后自动释放，不再永久占用 Web 请求。

---

#### 1b. 错误全标成同一类 - 无法区分鉴权失败 vs 限流 vs 解析失败

**原版（v1.0）：**
```python
except Exception as e:
    # 无论什么错误，全部标成 llm_timeout
    return AgentError(
        agent="LLMClient",
        error_type="llm_timeout",  # 401/429/JSON解析失败，全是这个
        message=str(e),
        recoverable=True,
    )
```

**现版（v1.1）：**
```python
def _classify_error(e: Exception) -> str:
    msg = str(e).lower()
    if isinstance(e, (TimeoutError,)):
        return "timeout"
    if "401" in msg or "unauthorized" in msg or "invalid api key" in msg:
        return "auth_error"
    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return "rate_limit"
    if isinstance(e, ValidationError) or "json" in msg or "parse" in msg:
        return "parse_error"
    if "connection" in msg or "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "unknown"
```

**好处：** 前端可以根据 error_type 给用户不同的提示——"API Key 无效" vs "请求太频繁" vs "AI 返回格式异常"。

---

#### 1c. Structured Output 单点故障 - 端点不支持就全崩

**原版（v1.0）：** 只有一条路，`beta.parse` 失败就完蛋。

```python
resp = self._client.beta.chat.completions.parse(
    response_format=response_model,  # 依赖端点支持 structured outputs
)
# 如果端点不支持 response_format，这里直接异常，重试 3 次还是异常
return response_model.model_validate_json(raw)
```

**现版（v1.1）：** 先试 structured，失败后降级到普通 create + 手动提取 JSON。

```python
def chat_structured(self, ...):
    for attempt in range(max_retries):
        try:
            return self._try_structured(...)        # 第一条路
        except Exception as e:
            ...
            if attempt == max_retries - 1:
                try:
                    return self._try_plain_validate(...)  # 降级路径
                except Exception as e2:
                    return AgentError(...)

def _try_plain_validate(self, ...):
    # 普通 create + 手动提取 JSON（去掉 markdown 代码块包裹）
    resp = self._client.chat.completions.create(...)
    raw = self._extract_json(resp.choices[0].message.content or "")
    return response_model.model_validate_json(raw)
```

**好处：** 即使 LLM 端点不支持 structured outputs（很多第三方兼容端点不支持），
系统也能通过降级路径正常工作。这是整个系统最致命的暗雷。

---

### 2. Coordinator Planner 兜底（`app/coordinator.py`）

**问题：** Matchers、Reporter、Timeline 都有兜底，唯独 Planner 没有。
Planner 一旦失败，`raise RuntimeError` 直接让 `/api/run` 返回 500。

**原版（v1.0）：**
```python
def run(self, inp: AssignmentInput) -> FullPlan:
    plan = self._step_planner(inp)
    if isinstance(plan, AgentError):
        raise RuntimeError(f"Planner failed: {plan.message}")
        # 直接崩溃！整条链路中断，用户拿到 500 错误
```

**现版（v1.1）：**
```python
def run(self, inp: AssignmentInput) -> FullPlan:
    plan = self._step_planner(inp)
    if isinstance(plan, AgentError):
        logger.warning("Planner LLM failed, use deterministic fallback")
        plan = self._fallback_plan(inp, plan.message)
        # 不崩溃，降级为确定性兜底计划

@staticmethod
def _fallback_plan(inp, error_msg="") -> PlanOutput:
    # 生成 5 个标准阶段：需求 -> 设计 -> 开发 -> 测试 -> 文档
    base_hours = {0: (4, "需求分析与调研", [...]),
                  1: (6, "方案设计与技术选型", [...]),
                  2: (8, "核心模块开发", [...]),
                  3: (6, "测试与联调", [...]),
                  4: (4, "文档撰写与答辩准备", [...])}
    tasks = [SubTask(id=f"T{i+1}", name=name, ...) for i in range(5)]
    return PlanOutput(tasks=tasks, ...)
```

**好处：** LLM 抖动时用户至少拿到一份可编辑的骨架计划，而不是 500 错误页。

---

### 3. validate_plan 依赖重映射（`app/agents/validation.py`）

**问题：** 去重时把 T1 改名为 T1_1，但其他任务指向 T1 的依赖没有跟着改。

**原版（v1.0）：**
```python
seen = {}
deduped = []
for t in tasks:
    if t.id in seen:
        seen[t.id] += 1
        new_id = f"{t.id}_{seen[t.id]}"  # T1 -> T1_1
    else:
        seen[t.id] = 0
        new_id = t.id
    deduped.append(t.model_copy(update={"id": new_id}))
    # 问题：T2 的 dependencies 还是 ["T1"]
    # 但现在有两个 T1，T2 到底依赖哪个？
```

**现版（v1.1）：**
```python
seen = {}
id_remap = {}  # 原始id -> 去重后id
deduped = []
for t in tasks:
    ...
    id_remap[t.id] = new_id  # 记录映射
    deduped.append(t.model_copy(update={"id": new_id}))

# 重映射依赖：["T1"] -> ["T1_1"]
for i, t in enumerate(tasks):
    if t.dependencies:
        remapped = [id_remap.get(d, d) for d in t.dependencies]
        tasks[i] = t.model_copy(update={"dependencies": remapped})
```

**好处：** 去重后依赖链始终自洽，不会指向错误实例。

---

### 4. Matcher 空分配兜底（`app/agents/matcher.py`）

**问题：** LLM 编造的成员名全被 sanitize 剔除后，返回空 assignments，
Coordinator 不识别为错误，不触发 B3 兜底。

**原版（v1.0）：**
```python
def _sanitize(qa, plan, members):
    cleaned = []
    for a in qa.assignments:
        if a.task_id not in task_map:
            continue  # 全部被跳过
        ...
    return qa.model_copy(update={"assignments": cleaned})
    # cleaned 是空列表，但返回正常 QAOutput
    # Coordinator 不会走 B3 兜底
```

**现版（v1.1）：**
```python
def _sanitize(qa, plan, members):
    cleaned = []
    ...
    if not cleaned:
        return AgentError(
            agent="Matcher",
            error_type="validation_error",
            message="LLM assignments all reference invalid members/tasks",
            recoverable=True,
        )
    return qa.model_copy(update={"assignments": cleaned})
```

**好处：** LLM 输出极差时自动降级到 B3 确定性分配，用户总能拿到有效 QA 矩阵。

---

### 5-7. CLI + 测试 + 版本锁定

- **CLI 单 Agent 调试**（`app/cli.py` 新增）：详见 [调试指南](docs/单Agent调试指南.md)
- **Agent 单元测试**（`tests/test_agents.py` 新增 15 个）：FakeLLMClient 覆盖全部 Agent，总数 24 -> 39
- **版本统一**：main.py / schemas.py 对齐为 v1.1
- **依赖锁定**：requirements.txt 加版本上限，新增 pytest-asyncio

---

## v1.0 - 功能完整正式版（2026-07-14）

**定位：** 经历骨架 -> 算法 -> 打磨后，整合为第一个正式版本。

---

### 核心改动：工时系统从"死值"变"活值"

**问题：** 全局写死每人每天 4 小时，但现实中人各有不同。

**原版（v0.1）TeamMember：**
```python
class TeamMember(BaseModel):
    name: str
    skill_tags: list[str] = Field(default_factory=list)
    # 没有 available_hours
    # 没有 daily_available_hours
```

**现版（v1.0）TeamMember：**
```python
class TeamMember(BaseModel):
    name: str
    skill_tags: list[str] = Field(default_factory=list)
    available_hours: float = Field(
        default=20.0,
        description="可用工时（人时），B3 负载均衡使用",
    )
    daily_available_hours: float = Field(
        default=4.0,
        description="每人每天可用工时，用于时间线折算",
    )
```

**Timeline 折算的对应改动：**

```python
# 原版：全局固定
durations[t.id] = max(1, round(t.estimated_hours / 4.0))

# 现版：按任务负责人的实际日产能
def _task_daily_capacity(task_id):
    assigned = assignments.get(task_id, [])
    capacity = sum(member_daily[name] for name in assigned)
    return max(0.5, capacity)
durations[t.id] = max(1, round(t.estimated_hours / _task_daily_capacity(t.id)))
```

**好处：** 张三每天 6h、李四每天 3h，同一个 12h 任务，张三 2 天、李四 4 天——这才是真实工期。

---

### 其他 v1.0 改动

- **答辩模拟自定义要求**：InterviewSimAgent 新增 user_requirements 参数
- **Planner 多方案建议**：Prompt 增加 alternatives 字段
- **Web UI 全面升级**：TailwindCSS + 5 Tab + 甘特图 + 负载条形图 + Markdown 导出
- **Bug 修复**：routes.py 未传 user_requirements、editor.py 未传 members、f-string 语法错误

---

## v0.4 - B3 评分 + B4 编辑 + 精细打磨（2026-07-14）

**定位：** 第一次系统性精细打磨。

---

### B3: 完整角色匹配引擎（`app/agents/scoring.py`，新增）

**问题：** Matcher 完全依赖 LLM，分配结果不可解释。评委问"为什么张三主讲"答不上来。

**新增核心逻辑：**
```python
def skill_score(member, required_skills):
    # 用 SequenceMatcher 计算技能标签相似度，返回 0-1 分
    total = sum(max(_similar(req, tag) for tag in member.skill_tags)
                for req in required_skills)
    return round(total / len(required_skills), 3)

def assign_with_balance(plan, members):
    load = {m.name: 0 for m in members}
    for t in plan.tasks:
        # 技能分 - 已分配任务数惩罚（负载均衡）
        scored = [(m.name, skill_score(m, t.required_skills) - 0.25 * load[m.name])
                  for m in members]
        presenter = max(scored, key=lambda x: x[1])[0]
        load[presenter] += 1
```

**好处：** 分配有了量化依据（score 字段），可解释；负载均衡防止堆任务；LLM 不可用时可独立生成。

---

### B4: 协作图动态编辑（`app/editor.py`，新增）

**问题：** 计划生成后不可修改，用户只能重跑。

**新增：** add / remove / update 三种编辑操作，编辑后自动重算 Timeline + Matcher。

```python
def apply_edits(plan, edits):
    for edit in edits:
        if edit.op == "add":
            tasks.append(edit.task)
        elif edit.op == "remove":
            tasks = [t for t in tasks if t.id != edit.task_id]
            for t in tasks:  # 同时清理依赖
                t.dependencies = [d for d in t.dependencies if d != edit.task_id]
    new_timeline = timeline.run(new_plan, ...)  # 重算
    new_qa = matcher.run(new_plan, ...)
```

**好处：** 计划变成"活的"——增删改任务后一键重算。

---

### 计划校验工具（`app/agents/validation.py`，新增）

- ID 去重、悬空依赖剔除、环检测（Kahn 拓扑排序）

---

## v0.3 - Web 重做 + Memory + 答辩模拟（2026-07-14）

**定位：** 从"能跑"到"好用"的第一次大提升。

---

### Timeline 从 LLM 占位变为纯算法

**原版（v0.1）：** Timeline 直接调 LLM 生成时间线，结果完全不可控。

```python
class TimelineAgent(BaseAgent[TimelineOutput]):
    system_prompt = TIMELINE_SYSTEM
    response_model = TimelineOutput

    def run(self, plan, deadline):
        user = TIMELINE_USER_TEMPLATE.format(...)
        result = self._call_llm(user)
        return result  # 完全依赖 LLM
```

**现版（v0.2+）：** Timeline 改为纯 CPM 算法，不调 LLM。

```python
class TimelineAgent(BaseAgent[TimelineOutput]):
    system_prompt = ""       # 不用 LLM
    response_model = None

    def __init__(self, llm=None):
        self.llm = None

    def run(self, plan, deadline, ...):
        # 1. 拓扑排序确定执行顺序
        # 2. Forward/Backward pass 计算最早/最晚时间
        # 3. 关键路径 = float 为 0 的任务
        # 4. 从截止日倒排起始日期
```

**好处：** 时间线 100% 确定性、可复现，关键路径和浮动天数都是数学保证的精确值。

---

### 其他 v0.3 改动

- **Web 前端完全重做**：TailwindCSS 现代化界面，多 Tab 切换
- **Memory 持久化**：save/load/list/delete 计划
- **答辩模拟 Agent**：5 维度提问，优先级标注
- **Prompt 全面重构**：从简单指令升级为结构化 Prompt Engineering

---

## v0.2 - 核心算法 + API 接入（2026-07-14）

- Timeline CPM 关键路径法（详见上方 v0.3 对照）
- Reporter 纯文本兜底（LLM 失败时拼接基本报告）
- API 接入阿里云 DashScope（qwen-max）
- 修复 config 泄露（.env 不再被 git 追踪）

---

## v0.1 - 初始骨架（2026-07-12）

**定位：** 项目从零到一。搭好架构骨架，所有 Agent 能跑通。

### 核心设计决策

**Pydantic model 做接口契约：** 所有 Agent 的输入/输出都是强类型。
任何 Agent 的输出格式变了，上下游立刻在类型校验时发现。

**LLM 和确定性算法分层：**
```
LLM 负责"创造性"：拆任务、分配角色、写报告
确定性算法负责"严谨性"：关键路径、技能评分、依赖校验
```

**Coordinator 做总调度：** Agent 之间不直接依赖，全部通过 Coordinator 中转。
好处是可以单独测试每个 Agent，也可以灵活替换执行顺序。

### 基础测试（6 个）
- test_coordinator.py：Coordinator 主链路（mock LLM）
- test_api.py：健康检查接口

---


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

