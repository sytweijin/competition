# 变更日志 (CHANGELOG)

> 本文档记录项目每一次版本变更，附核心改动的**原版 vs 现版代码对照**，
> 方便团队成员理解"为什么这么改、改了什么、好在哪里"。
> 按时间倒序排列（最新在最上面），随项目同步更新。

---


## v3.4 —— 报告自动重生 + 报告表格渲染修复 + LLM 空鉴权快速兜底（2026-07-17）

**定位：** 修复两个体验缺陷：编辑计划/成员变动后报告不会自动更新、报告页 Markdown 表格渲染错乱；顺带修一个隐藏的健壮性隐患（空 API key 挂死网络）。
**审查/修改背景：** 用户反馈「改了任务时长或成员后，报告还得自己手动重新生成计划，那手动编辑还有什么意义」以及「报告页表格有一部分是乱的」。前者是三处编辑入口都只给报告加了「建议重新生成」的过期提示而不真正重算；后者是 simpleMarkdown 的表头/数据行判定依赖累积 HTML 字符串里的 `</thead>`，首行数据被错当表头。

---

### 关键缺陷（P0）

**1. 编辑/成员变动/状态切换后报告不自动重生成（Bug1）**
- **问题：** `edit_plan`、`/recompute`、`/edit-members` 三处重算 timeline/qa 后，报告只追加「建议重新生成」的过期提示，用户被迫手动重跑 `/run` 全链路——等于让 AI 把计划从头算一遍，手动编辑的成果被丢弃。
- **修改前：**
  ```python
  # app/editor.py edit_plan 返回处
  report=original.report.model_copy(update={"risk_note": (...+"建议重新生成)").strip()}),
  ```
- **修改后：** 三处都用编辑后的 plan + 重算的 timeline + qa_matrix 单独调 `ReporterAgent().run()`，不重跑 Planner/Matcher 链路，因此保留用户的手动编辑；Reporter 内部有纯文本兜底，LLM 失败也不中断：
  ```python
  # app/editor.py
  try:
      report = ReporterAgent().run(plan=new_plan, timeline=timeline, qa_matrix=qa_matrix)
      if not isinstance(report, ReportOutput):
          report = ReportOutput(summary="报告重生成失败", risk_note=str(report))
  except Exception as exc:
      report = original.report.model_copy(update={"risk_note": (...+f"报告重生成失败: {exc}").strip()})
  ```
- **为什么这样改：** Reporter 只做「格式化」，输入是 plan/timeline/qa 三者，与 Planner/Matcher 的 LLM 无依赖。旧代码把它当成必须重跑全链路才能产出，于是选择了「不重算、只提示」——结果用户面对过期报告，要么忍受不一致，要么手动重跑 `/run` 把编辑清零。现在在重算 timeline/qa 之后就地调 Reporter，用最新数据生成新报告，编辑成果与报告一致性兼得。
- **收益：** ① 编辑时长/成员变动后报告自动更新，无需手动重跑；② 保留手动编辑（不重跑 Planner）；③ LLM 失败有兜底，不中断编辑流程。

**2. 报告页 Markdown 表格渲染错乱（Bug2）**
- **问题：** `simpleMarkdown` 用 `var tag=(html.indexOf('</thead>')<0)?'th':'td'` 判定表头/数据行，但 `</thead>` 是在「分隔行之后的下一行」才插入累积 HTML 的。导致紧跟分隔行的第一行数据被判定时 HTML 里还没有 `</thead>`，于是用 `<th>` 当了数据行，thead/tbody 结构错乱——表格「有一部分是乱的」。
- **修改前：**
  ```js
  var tag=(html.indexOf('</thead>')<0)?'th':'td';
  html+='<tr>'+cells.map(...)...+'</tr>';
  ```
- **修改后：** 用 `headerDone` 布尔状态位显式区分表头与数据行，移除脆弱的 `</thead>` 子串探测；表头行用 `<th class="bg-slate-50 font-semibold">`、数据行用 `<td>`，结构统一为一个 `<tbody>`：
  ```js
  var headerDone=false;
  ...
  var tag=headerDone?'td':'th';
  var cls=headerDone?'...px-2 py-1':'...px-2 py-1 bg-slate-50 font-semibold';
  html+='<tr>'+cells.map(...)...+'</tr>';
  headerDone=true;
  ```
- **为什么这样改：** 根因是「靠在累积输出字符串里搜子串」来推断解析状态——这是经典的状态泄漏 bug：输出顺序和判断时机耦合，一旦顺序变化就误判。改用独立布尔标志后，状态机清晰：第一行表头→置 true→后续皆数据行。验证：2 表头格 + 4 数据格 + 0 错乱 thead。
- **收益：** ① 报告表格表头/数据行正确区分，不再错乱；② 表头有底色加粗，可读性更好；③ 渲染逻辑去掉了脆弱的子串探测。

---

### 健壮性提升（P1）

**3. 空 API key 时 LLM 调用挂死网络**
- **问题：** `LLMClient` 即便 `LLM_API_KEY` 为空也会创建 `OpenAI(api_key="")` 客户端，调用时直连网络直到超时；测试/未配置环境里各 Agent 的兜底逻辑无法快速接管。
- **修改前：** `__init__` 直接建客户端，`chat_structured`/`chat_text` 无前置检查。
- **修改后：** `__init__` 设 `self._enabled=bool(LLM_API_KEY)`，两个调用方法开头空 key 时直接返回 `AgentError(auth_error, recoverable=False)`，让兜底即时接管。
- **为什么这样改：** 这是报告自动重生引入测试超时时暴露的既有隐患——空配置不该挂死在网络上。显式 `_enabled` 让「不可用」成为一个快速、确定的信号。
- **收益：** ① 空 key 时秒退走兜底而非超时；② 测试环境稳定（配合 conftest stub）；③ 生产环境配错 key 时快速报错而非假死。

---

### 同步修改

- `tests/conftest.py`（新增）：autouse 夹具在非 `test_agents` 用例里 stub `ReporterAgent.run`，避免编辑/重算测试因自动重生报告打到真实 LLM 而挂死。
- `app/web/templates/index.html`：版本号同步至 v3.4。

---

### 验证（本次新增）

- 报告表格：2 表头格 `<th>` + 4 数据格 `<td>` + 0 错乱 `<thead>`（修复前表头数据混用）。
- 全量测试：`pytest tests/ -q` → 52 passed（含 conftest stub 后的编辑/重算/成员测试）。


## v3.3 —— 完成不重排 + 负向技能标签识别（2026-07-17）

**定位：** 修复两个用户实测发现的行为缺陷：完成任务触发全员重排、负向技能标签被当成正向匹配。
**审查/修改背景：** 用户反馈「一个人完成自己任务后被塞去帮别人后续任务」「明明标注不想做PPT却把PPT派给他」。前者是状态切换重算逻辑缺陷，后者是技能匹配把子串包含当正向信号——两者都让分工结果违背直觉。

---

### 关键缺陷（P0）

**1. 标记任务完成会重排所有人，已完成者被塞进别人后续任务（Bug1）**
- **问题：** `/recompute`（状态切换端点）每次都调 `assign_with_balance` 从零重算整个 QA 矩阵，完全丢弃原有分工。完成任务的人负载归零被当成「闲人」，于是被均衡算法派到别人后续任务的主讲/答辩位——与现实严重不符。
- **修改前：**
  ```python
  # app/web/routes.py /recompute
  from app.agents.scoring import assign_with_balance
  qa_matrix = assign_with_balance(plan, members)   # 从零重排，丢弃 req.qa_matrix
  ```
- **修改后：** 新增 `recompute_preserve(plan, old_qa, members)`——保留原有分工，仅把已完成任务标记为 `(已完成)` 占位、负载与超载告警按现状重算；只有原矩阵缺失或主讲已离岗时才按匹配度补一个。`/recompute` 改调它，`/edit-members`（成员变动）仍走全量重排：
  ```python
  # app/agents/scoring.py
  def recompute_preserve(plan, old_qa, members):
      ...
      for t in plan.tasks:
          if t.status == "completed":
              assignments.append(QAAssignment(presenter="(已完成)", ...))
          elif old is not None and old.presenter in member_map:
              assignments.append(old.model_copy(...))   # 保留原分工
          else:
              ...  # 兜底：缺失任务按匹配度补
      work = _work_from(assignments, task_hours, members)  # 只重算负载/告警，不重排
  ```
- **为什么这样改：** 状态切换与成员变动语义不同。成员变了（有人退课）必须全量重排，否则悬空；但单纯「我完成了我那份」不应让别人被换走，也不应把已完成者重新派到新任务上。旧逻辑用同一个函数处理两种语义，导致「完成任务」这一高频操作产生反直觉的大面积重排。新函数把「保留」作为默认、把「补全」作为兜底，职责清晰。
- **收益：** ① 完成任务后其他人的分工保持稳定，不再被顶替或牵连；② 已完成者不会被自动塞进后续任务；③ 成员变动走独立端点仍全量重排，两种场景各得其所。

**2. 负向技能标签被当成正向匹配（Bug2）**
- **问题：** `skill_score` 用 `_similar` 做子串包含匹配，标签「不太想做PPT」因含子串「PPT」被打 0.85 高分，于是系统把「不想做PPT」的人当成了 PPT 强项，PPT 制作任务反而派给他。
- **修改前：**
  ```python
  def skill_score(member, required_skills):
      ...
      for req in required_skills:
          best = max((_similar(req, tag) for tag in member.skill_tags), default=0.0)
          total += best   # 「不太想做PPT」里的 PPT 被算成 0.85 正向匹配
  ```
- **修改后：** 新增 `_split_tags(tags) -> (正向, 负向回避)`，识别 `不想/不太想/不擅长/避免/拒绝` 等前缀，剥离出被回避的技能；`skill_score` 对命中负向的技能直接记 0；同时 `format_skills_for_prompt` 把标签格式化成「擅长: X; 回避: Y」喂给 LLM，让 Matcher 也读懂负向偏好：
  ```python
  _NEGATIVE_MARKERS = ("不想", "不太想", "不擅长", "不喜欢", "避免", "拒绝", "别让", "排斥", "怕做")

  def skill_score(member, required_skills):
      pos_tags, neg_tags = _split_tags(member.skill_tags)
      for req in required_skills:
          if any(_similar(req, n) >= 0.6 for n in neg_tags):
              continue   # 明确回避 -> 记 0，不参与正向匹配
          best = max((_similar(req, tag) for tag in pos_tags), default=0.0)
          total += best
  ```
- **为什么这样改：** 根因是「相似度」无法区分意图——「做PPT」和「不做PPT」在字符层面都包含「PPT」。负向偏好是用户表达约束的方式（「我擅长前端但不想碰PPT」），必须作为独立信号处理，而不是混进正向打分。拆出负向集合后，确定性兜底（assign_with_balance）与 LLM（matcher/coordinator 的提示词）两个路径都不再把回避项当强项。
- **收益：** ① 「不想做X」的人不再被派去做X；② 提示词显式标注回避项，LLM 也能遵守；③ 负向识别覆盖多种自然写法（不想/不太想/避免/拒绝…）。

---

### 同步修改

- `app/agents/matcher.py`、`app/coordinator.py`：成员技能展示改用 `format_skills_for_prompt`，把负向偏好显式呈现给 LLM。
- `app/web/templates/index.html`：版本号同步至 v3.3。

---

### 验证（本次新增）

- 负向标签：`skill_score(小明[前端,不太想做PPT], [PPT])` 由 0.85 降为 0.0；PPT 任务改派给有 PPT 标签者。
- 完成重排：小明完成 T1 后，T2/T3 主讲保持原分工不变（小红/小刚），小明未被塞入后续任务。
- 全量测试：`pytest tests/ -q` → 52 passed；`app/` 全模块编译通过。


## v3.2 —— 用户验收六连击修复（2026-07-17）

**定位：** 上一版 v3.1 交付后用户实测发现的 6 个回归/残留问题，逐条定位根因并修复。
**审查/修改背景：** 用户反馈「改了那么多东西，是不是又把有的东西改坏了」——经核查确有 6 处需要修补：版本号不同步、新生成任务状态错乱、分工仍不均衡、Word/PDF 报告残留 Markdown、QA 矩阵匹配度栏空缺、甘特图末位任务条被裁。本版本全部修复并补齐自动化验证。

---

### 健壮性提升（P1）

**1. 前端版本号与 CHANGELOG 不同步（Q1）**
- **问题：** v3.1 已写入 CHANGELOG，但前端标题/副标题仍显示 v3.0，用户无法确认线上跑的是哪一版。
- **修改前：** `<title>小组合作智能体 v3.0</title>`；副标题 `<span class="text-slate-400">v3.0</span>`。
- **修改后：** `<title>小组合作智能体 v3.2</title>`；副标题 `<span class="text-slate-400">v3.2</span>`——与当前 CHANGELOG 版本对齐。
- **为什么这样改：** 版本号是「这个版本改了什么」的唯一入口。CHANGELOG 与界面版本号不一致会让团队无法判断线上行为对应哪个版本，排查回归时无据可依。
- **收益：** ① 前端与 CHANGELOG 版本对齐；② 用户一眼确认当前运行版本。

**2. 新生成的任务初始状态被 LLM 写成「已完成」（Q2）**
- **问题：** LLM 偶发把任务 `status` 字段直接写成 `completed`/`in_progress`，导致任务一出生就被标成已完成，进度条瞬间 100%、时间线把后续任务全部前移。
- **修改前：** `result = result.model_copy(update={...})` 后直接返回，未触碰 `tasks[*].status`。
- **修改后：** LLM 返回后强制归零——
  ```python
  result = result.model_copy(update={
      "tasks": [t.model_copy(update={"status": "pending"}) for t in result.tasks]
  })
  ```
- **为什么这样改：** 任务状态是「用户手工维护」的运行时数据，绝不应由生成阶段决定。LLM 的职责是拆任务，状态的语义在编辑/执行阶段才有意义；在生成出口强制归零是最可靠的兜底。
- **收益：** ① 新计划一律从「待开始」起步；② 进度条/时间线不再被污染；③ 状态语义单一真相源。

**3. 三人分工工时差仍超 1h，且不可均摊时无提示（Q3）**
- **问题：** 上一版 v3.1 的负载均衡只搬运「辅答」，当主讲分布本身失衡时无法纠正；且当任务结构在数学上无法 3 人均摊（如 5 个 5h 任务给 3 人，必有人扛 2 个）时，静默返回失衡结果，用户不知该如何处理。
- **修改前：** 均衡逻辑只覆盖辅答搬运，且失衡时不给任何提示。
- **修改后：** 重写 `_balance_workload`——主讲/主答/辅答统一搬运，每步枚举所有可行搬运并用「真实重算负载」评估全局 gap，选最小者执行，gap 不再下降即停；新增 `_split_suggestion`，当均衡后 gap 仍 >1h 时在 note 追加拆分建议：
  ```python
  def _split_suggestion(work, assignments, task_hours, members, threshold=1.0):
      gap = max(work.values()) - min(work.values())
      if gap <= threshold + 1e-9:
          return ""
      over_name = max(work, key=lambda n: work[n])
      # 找该成员工时最大的主讲任务，建议拆分
      ...
      return f" 建议拆分 {over_name} 的 {a.task_name}（{h:.1f}h），当前成员最大工时差 {gap:.1f}h 超过 1h，任务结构无法在 3 人间均摊"
  ```
- **为什么这样改：** 用户需求是「默认三人时长差不超过 1h」。任务结构允许时严格 ≤1h（已验证：3-task 6/6/6、4-task 8/8/4/6、6-task 交替 gap 均为 0.9）。但数学限制下（任务数无法被 3 整除）自动拆分会篡改用户计划，故改为不改数据、只在 note 给出明确拆分建议，把决定权交还用户。
- **收益：** ① 任务结构允许时 gap 严格 ≤1h；② 不可均摊时给出可操作建议而非静默失衡；③ 快照/还原机制杜绝近似误差。

---

### 体验优化（P2）

**4. Word/PDF 报告仍残留 Markdown 符号（Q4）**
- **问题：** 导出的 .docx / .pdf 里 `##`、`**粗体**`、`*斜体*`、表格 `|---|` 等 Markdown 原符号直接出现在正文中，用户看到的是「源码」而非排版后的文档。
- **修改前：** 报告正文直接用 `add_paragraph(text)` / reportlab `Paragraph(text)` 写入，Markdown 标记未解析。
- **修改后：** 新增 `_md_blocks`（切 h2/h3/li/p/table 块）+ `_md_split_inline`（拆 `**bold**`/`*italic*` 片段）+ `_md_to_docx`（渲染进 Word，含标题/列表/粗体/表格）+ `_md_to_pdf_story`（渲染成 reportlab flowable）。导出时统一走这两个函数。
- **为什么这样改：** 报告是给「看的人」的交付物，不是给「写 Markdown 的人」的源码。Markdown 必须解析成富文本，否则 `##` 和 `**` 就是噪声。两个导出路径共用同一套解析器，保证 Word/PDF 渲染一致。
- **收益：** ① Word/PDF 不再出现裸 Markdown 符号；② 标题层级、粗体斜体、表格正确渲染；③ 两路导出共享解析器，行为一致。

**5. QA 矩阵「匹配度」栏部分任务显示空白（Q5）**
- **问题：** 匹配度列只渲染 `score>0` 的任务，其余任务该格什么也不输出（空白），用户以为「漏算了」或「匹配失败」。
- **修改前：** `var sc=(typeof a.score==='number'&&a.score>0)?'<span ...>'+百分比+'</span>':'<span class="text-xs text-slate-300">-</span>';`——分数为 0 的活动任务显示 `-`，与已完成任务的占位无法区分。
- **修改后：** 区分「已完成任务」与「活动任务」——已完成显示 `-`，活动任务 `score>0` 显示百分比、否则显式显示 `0%`：
  ```js
  var isDone=(a.presenter==='已完成'||a.presenter==='(已完成)');
  var sc=isDone?'<span class="text-xs text-slate-300">-</span>'
    :(typeof a.score==='number'&&a.score>0?'<span ...>'+(a.score*100).toFixed(0)+'%</span>'
      :'<span class="text-xs text-slate-400">0%</span>');
  ```
- **为什么这样改：** 「空白」和「-」语义模糊，用户分不清是「没算」还是「算出来是 0」。活动任务显式标 `0%` 表示「确实匹配度为零，需要换人」，已完成任务标 `-` 表示「不适用」，两态不再混淆。
- **收益：** ① 匹配度列每格都有明确值；② 活动任务 0% 与已完成 `-` 语义分离；③ 用户能一眼定位需要调换的分工。

**6. 甘特图最后一个任务条右端被裁切（Q6）**
- **问题：** 时间轴跨度 `td` 用 `mx-mn`（天数差）计算，漏了「首尾都算」的 +1；且未处理某任务 `offset+duration` 超出 `td` 的情况，导致末位任务条 width 计算偏小、右端到不了轨道尽头，被 `overflow-hidden` 裁掉一截。
- **修改前：**
  ```js
  var td=Math.ceil((new Date(mx)-new Date(mn))/86400000)||1;
  ...
  var dur=Math.ceil((new Date(t.end_date)-new Date(t.start_date))/86400000)||1;
  var lp=(off/td)*100;
  var wp=Math.max(3,Math.min(100-lp,(dur/td)*100));   // 仅按 width 推导，易偏小
  ```
- **修改后：** 补 `+1`（首尾含端），并新增二次扫描确保 `td` 不小于任一任务的 `offset+duration`；width 改为「先算右边界再相减」，保证右端恰好到 100%：
  ```js
  var td=Math.ceil((new Date(mx)-new Date(mn))/86400000)+1;
  tasks.forEach(function(t){var _o=...;var _d=Math.max(1,...+1);if(_o+_d>td)td=_o+_d;});
  ...
  var lp=(off/td)*100;
  var re=Math.min(100,((off+dur)/td)*100);   // 右边界强制钳到 100%
  var wp=Math.max((1/td)*100,re-lp);          // 宽度=右边界-左边界
  ```
- **为什么这样改：** 甘特图是「一眼看进度」的视图，末位任务条被裁会让人误以为「任务没排满」或「排期有断档」。根因是跨度/duration 的天数口径不一致（一个含端一个不含端），以及 width 用绝对值而非「右边界−左边界」推导。改后右端恒到 100%（已用 node 数值验证：末位任务 rightEdge=100% 三例全过）。
- **收益：** ① 末位任务条完整可见、右端对齐轨道尽头；② 天数口径统一（首尾含端）；③ 跨度不足时自动扩展，杜绝裁切。

---

### 验证（本次新增自动化检查）

- 前端 4 步强制验证全通过：JS 语法 OK、字符串拼接 0 处可疑、`"` 0 处、pytest 52 passed。
- 甘特图末位任务条 rightEdge 数值验证：Case A/B/C 末位均 = 100.00。
- Markdown 解析验证：标题/列表/粗体/斜体/表格均正确切分，无裸符号残留。
- 负载均衡验证：可均摊场景 gap 严格 ≤1h；不可均摊场景 note 含拆分建议。



## v3.1 —— workbuddy 审查复核后的选择性修复（2026-07-17）

**定位：** 对队友提交的《代码全面审查报告》逐条核对代码后，修复其中确实成立且值得动手的 12 项；明确驳回/暂不改若干项并给出理由。
**审查/修改背景：** 审查报告（史雨彤）给出 1×P0 + 8×P1 + 12×P2。经逐文件交叉验证，约 18 条成立、1 条不成立（P2-4）、数条严重度偏高。本版本只改“成立且低风险/高收益”者；纯设计取舍（负载折算口径、available_hours 语义、重算是否回退 LLM）未动代码，仅在文档说明。

---

### 关键缺陷（P0）

**1. `interview_sim` 禁用词列表是乱码，术语清洗完全失效（对应审查 P0-1）**
- **问题：** `bans` 列表里除 `'QA角色'` 与 ASCII 项外，其余中文字面量是被损坏的码点（GBK 被当 UTF-8 读入），`.replace()` 用乱码去匹配真实中文输出，永远匹配不到。
- **修改前：** `bans = ['QA鐟欐帟澹?, '涓荤瓟', '杈呯瓟', ...]`（乱码）；`for term in bans: result = result.replace(term, '')`。
- **修改后：** 拆为 `bans_zh`（与 `INTERVIEW_SYSTEM` 禁用清单逐条对齐，含裸“主讲/主答/辅答”）+ `bans_ascii`；中文用普通 `replace`，ASCII 项用 `re.sub(..., flags=re.IGNORECASE)`。
- **为什么这样改：** 旧码点是编码损坏的产物，根因是源文件在某次保存时编码错乱；逐词 `replace` 本身可接受（答辩问题域内误伤风险低），但前提是码点正确、且能匹配大小写变体（模型常写 `Score/Load`）。
- **收益：** ① 内部术语不再泄露到答辩问题；② ASCII 项大小写不敏感，漏网率下降；③ 禁用清单与提示词单一真相源对齐。

---

### 健壮性提升（P1）

**2. 状态切换不触发重算，与 README 卖点不符（P1-7）**
- **问题：** README 写“标记任务完成/阻塞会实时重算排期与分工，不再只是视觉”，但 `bindStatusEvents` 只改本地进度条，不调用 `/api/recompute`。
- **修改前：** 状态 `change` 回调内仅更新 `currentData`、进度条 CSS。
- **修改后：** 回调末尾调用新增的 `scheduleRecompute()`：700ms debounce → `fetch('/api/recompute')` → 成功后 `showWarnings + renderResult + switchTab(currentTab)`（保留当前标签页）。
- **为什么这样改：** 文档承诺与代码行为背离是“假卖点”；debounce 避免连续切换打爆后端，`switchTab(currentTab)` 避免重渲染把用户踢回默认页。
- **收益：** ① 文档与行为一致；② 完成任务后时间线/负载条真缩短；③ 重渲染不丢失当前查看的标签页。

**3. “全员参与”无代码保证（P1-2）**
- **问题：** `MATCHER_SYSTEM` 规则 5 要求“确保全员参与”，但 `assign_with_balance` 按“匹配度最高+负载最轻”选主讲，技能弱/负载已高的成员可能一个角色都拿不到。
- **修改前：** 分配循环只看匹配度与负载，无兜底。
- **修改后：** 分配完成后扫描 `work` 中负载为 0 的成员，给其在工时最大的活跃任务上补一个 `qa_support` 角色（按 `QA_SUPPORT_RATIO` 计入负载）。
- **为什么这样改：** 提示词是强约束而代码无兜底，易产出与承诺不符的结果；以“辅答”兜底对负载与匹配度影响最小。
- **收益：** ① QA 矩阵不再出现“某人完全隐身”；② 与提示词承诺一致；③ 兜底只对零负载成员生效，不干扰正常分配。

**4. `enhance`/`assign_with_balance` 转移主讲后 reasoning 与分配不符（P1-4）**
- **问题：** 负载均衡把主讲转给低负载成员后，只 `+= "；已转给X平衡负载"`，前缀“张三…匹配度1.00，综合最优”未改，与新 presenter/score 矛盾。
- **修改前：** `a.reasoning += f"；已转给{underloaded}平衡负载"`。
- **修改后：** 整体重写为 `f"{underloaded}：{skills} 技能匹配度 {score:.2f}（主讲经负载均衡由 {overloaded} 转入）"`。
- **为什么这样改：** 陈旧前缀会误导用户以为主讲仍是原人；整体重写避免“理由与分配”脱节。
- **收益：** ① reasoning 与 presenter/score 一致；② 去掉“综合最优”绝对表述；③ 两处（assign/enhance）行为统一。

**5. Timeline 不检查“每日并行过载”（P1-6）**
- **问题：** CPM 只按依赖排期，不判资源争用；同一人当天被排进多个无依赖任务、各自需满产时，系统只看总工时，当天已爆但不报警。
- **修改前：** 仅总产能/总工时判断。
- **修改后：** 排期后按 `(成员, 日期)` 聚合折算工时（主讲按 `hours/工期`、他人按 `0.5×日产能`，与 `_task_daily_capacity` 口径一致），超当日可用即写入 `risk` 告警（每人仅报最严重的一天）。
- **为什么这样改：** 总量口径掩盖了“某天某人爆掉”的现实问题；折算口径与既有 capacity 假设保持一致，避免双标。
- **收益：** ① 暴露真实的资源争用；② 用户可据此拆分人手/拉开日期；③ 不影响既有排期结果，只追加告警。

**6. 前端告警 deadline 时区与后端口径不一致（P1-8）**
- **问题：** 前端 `new Date("2026-08-20")` 按 UTC 解析，后端 `date.today()` 本地；且前端可用天数少算 1（未 `+1`）。默认 Asia/Shanghai 下恰好抵消，其他时区会前后端告警打架。
- **修改前：** `var dl=new Date(data.input.deadline); var avail=Math.ceil((dl-td)/86400000);`。
- **修改后：** `var dl=new Date(data.input.deadline+'T00:00:00');`（强制本地时区）+ `var avail=Math.max(1,Math.ceil((dl-td)/86400000)+1);`（与后端 `max(1,(deadline-today).days+1)` 对齐）。
- **为什么这样改：** 单一真相源应由后端口径为准，前端解析对齐本地时区可消除 UTC 偏移；`+1` 与后端“含首尾两天”语义一致。
- **收益：** ① 前后端超时/缓冲告警一致；② 跨时区不再误报；③ 改动局部、不触及后端。

---

### 工程与提示词（P2）

**7. `/api/edit` 仅重算时间线会导致新任务不在 qa_matrix（P2-1）**
- **修改前：** `if req.recompute_matcher: qa_matrix = assign_with_balance(...)`，新增任务且 `recompute_matcher=false` 时新任务无分配、时间线退回 global_daily 兜底。
- **修改后：** `has_add = any(e.op=="add" ...); if req.recompute_matcher or has_add:` 强制重算。
- **为什么这样改：** 接口允许只重算其一，调用方易拿到不一致状态；新增任务必然需要分配，强制重算最稳妥。
- **收益：** ① 新任务必定进入 qa_matrix；② 接口状态自洽；③ 现有前端默认双 true，行为不变。

**8. 版本号前后端/文档不一致（P2-2）**
- **修改前：** `index.html` 写 `v2.0`，`main.py`/`schemas.py` 为 `3.0`，README 两者并存。
- **修改后：** `index.html` 标题与副标题统一为 `v3.0`。
- **收益：** 对外展示版本一致。

**9. `main.py` 静态目录用相对路径（P2-7）**
- **修改前：** `StaticFiles(directory="app/web/static")`；工作目录非项目根时导入即崩。
- **修改后：** 用 `BASE_DIR` 拼绝对路径（与 `MEMORY_DIR` 一致）。
- **收益：** ① 任意工作目录启动均可用；② 与既有 `MEMORY_DIR` 风格统一。

**10. `LLMClient._try_structured` 读错字段（P2-8）**
- **修改前：** 只读 `message.content`；真 OpenAI 的 `beta.parse` 结果在 `message.parsed`、`content` 常为 None → 每次被判空、退回普通 create，结构化保证形同虚设。
- **修改后：** 优先 `message.parsed`（已是模型实例直接返回，是 dict 则 `model_validate`），回退到 `content` 的 JSON。
- **收益：** ① 接真 OpenAI 时结构化路径生效；② 兼容 Aliyun 等把 JSON 放 content 的端点；③ 现网行为不变。

**11. `LLM_MAX_RETRIES` 默认 1，等于无重试（P2-10）**
- **修改前：** 默认 `"1"`（1 次结构化 + 1 次回退），且对 `parse_error` 也无意义重试。
- **修改后：** 默认 `"3"`；`chat_structured` 改为：`parse_error` 立即 `break` 进 plain 回退，仅对 `rate_limit/timeout/unknown(5xx)` 重试。
- **为什么这样改：** 同一 prompt 的解析失败重试无意义、只浪费配额；瞬时限流/5xx 才值得退避重试。
- **收益：** ① 真正具备瞬态错误重试能力；② 解析类错误更快回退；③ 参数语义名副其实。

**12. `_classify_error` 关键字匹配有误判风险（P2-11）**
- **修改前：** 用 `"401"/"429"/"json"/"parse"` 等子串匹配，非英文报错（部分国产网关）会漏判。
- **修改后：** 优先按 OpenAI SDK 异常类型（`AuthenticationError/RateLimitError/APIConnectionError/BadRequestError/APIStatusError`）`isinstance` 判断，5xx 归 `unknown`(可重试)、4xx 归 `parse_error`。
- **收益：** ① 不依赖错误消息语言；② 5xx/429 精准识别；③ 对 SDK 版本差异用 `getattr` 兜底。

**13. 测试覆盖盲区：从未走到真实 LLM 网络路径（P2-12）**
- **修改前：** 现有测试用 `FakeLLMClient` 直接 stub `chat_structured`，真实 `LLMClient` 的 parsed/content/重试/回退路径零覆盖。
- **修改后：** 新增 `tests/test_review_fixes.py`（7 例）：用 `SimpleNamespace` mock `_client`，覆盖 ①读 `parsed` ②读 `content` JSON ③空响应回退 ④`parse_error` 不重试 structured ⑤`_classify_error` 类型判定 ⑥P1-2 全员参与 ⑦P2-1 新增任务强制重算。
- **收益：** ① P2-8/P2-10/P2-11 的真实路径被锁定；② 回归有据可查；③ 全量 45 → 52 passed。

---

### 队友改动说明
本版本基于队友提交的《代码全面审查报告》（史雨彤，2026-07-17）。该报告为“仅审查、未修改”。本版本在其基础上：逐条核对源码后落地上述 13 项；并明确以下项的处理：
- **未改（设计取舍，非 bug）：** P1-1（负载折算=1.6×工时是“加权参与度”而非真实人时，与 `available_hours` 比较偏保守，且被 P1-5 口径放大抵消）、P1-3（重算是否回退 LLM 是产品取舍，改为提示成本更低）、P1-5（`available_hours=daily×天数` 符合 schema 定义，非 bug）。
- **驳回：** P2-4（PLANNER 示例给的是 30h/8h 两档、默认 20h 居中，不存在漂移，属断章取义）。
- **审查报告笔误（无需改代码）：** P2-9 实为 3 个 LLM Agent 串行（Timeline 纯算法），非 4 个。

## v3.0 —— 七轮审查全量修复：健壮性/一致性/测试/死代码清理（2026-07-16）

**定位：** 经过多轮代码审查，针对发现的10+项真实问题进行系统性修复。覆盖字符编码、依赖重映射、PDF导出、负载计算、时间线off-by-one、前端ID碰撞、提示词一致性等。

### 关键缺陷（P0）

**1. scoring.py 中文字符损坏**
- **问题：** assign_with_balance 函数内 141 个中文字符被替换为字面 `?`（U+003F），导致 reasoning 输出全是乱码
- **修复：** 逐行重构中文字符，包括 docstring、注释、reasoning 字符串、note 字符串
- **受益：** 所有 recompute/edit/edit-members 路径的 QA 矩阵输出恢复正常

**2. validate_plan 依赖重映射 bug**
- **问题：** `id_remap[t.id] = new_id` 在循环内被每个重复实例覆盖，所有依赖指向最后一个重复实例
- **修复：** 删除 id_remap 赋值，首个实例保留原 ID，依赖自然指向第一个实例
- **受益：** Planner 吐出重复 ID 时依赖关系不会错乱

**3. PDF 导出 XML 未转义**
- **问题：** 课程名/描述/成员名直接塞进 reportlab Paragraph（XML 解析器），含 `&` 或 `<` 时导出 500
- **修复：** 所有用户输入字段用 `saxutils.escape()` 包裹
- **受益：** PDF 导出不会因特殊字符崩溃

**4. Matcher 提示词与实际行为不符**
- **问题：** 提示词承诺“自动负载均衡校正”，实际 enhance() 只补 score/workload，不重新分配角色
- **修复：** 提示词改为“做负载统计与超载检测，但不重新分配角色”
- **受益：** LLM 不再被误导，行为与提示词一致

### 健壮性提升（P1）

- **同名成员去重：** `_at_least_one_member` 增加重名检测，抛 ValueError
- **电脑炮·完成任务不再消失：** QA 矩阵为已完成任务生成“(已完成)”占位，不再过滤掉
- **enhance workload 去重：** qa_support 循环增加 person 重复检查，与 timeline 侧口径一致
- **负载转移后更新 score：** `assign_with_balance` 转移任务后重新计算 skill_score
- **interview_sim 不再 500：** LLM 失败时 return 错误文本而非 raise RuntimeError
- **前端新增任务 ID 碰撞：** 改为 max(现有 ID)+1，而非 children.length+1
- **时间线 qa_support 传入：** coordinator/routes/editor 三处均包含 qa_support

### 算法修正（P1）

- **时间线 off-by-one：** `available_days = (deadline-today).days+1` 含头含尾，不再“刚好排满报超期”
- **加权产能模型：** `_task_daily_capacity` 主讲全产能 + 其他人×0.5，替代全加（与 workload 口径一致）

### 测试修复（P1）

- **时间线测试时间耦合：** `@patch('datetime.date')` 对 `from datetime import date` 无效，改用 FakeDate 类真正冻结 today()

### 代码卫生（P2/P3）

- 删除死代码：`_fix.py`、`rank_members`、`name_to_skills`、`active_tasks.sort()`
- 修复 scoring 注释误导（“避免重复计数”→“累计可能超过任务原工时”）
- 编辑后报告追加“可能已过期”提示
- 清理 memory/ 目录测试垃圾文件
- 修复 routes.py/cli.py 中文损坏残留

### 已确认健全的点

- 前后端契约无矛盾（所有 API payload 与后端模型对得上）
- 测试 45 passed（包含时间线测试真正冻结）
- CPM 算法、角色匹配、validate_plan 去重、half-day 排期、PDF/DOCX/MD 导出均确认可用

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

### 
### 30. 关键路径红绿灯机制增强（前端 UI 优化）

**问题：** 甘特图虽然用红色表示关键路径、绿色表示可浮动，但缺少完整的图例说明，用户不清楚颜色含义；没有中间状态（黄灯）表示浮动不足；超载成员只有颜色变化没有文字提示。

**修改内容（仅 app/web/templates/index.html，3 处改动）：**

① 甘特图图例从 2 项扩充为 5 项：
- 🔥 关键路径（红色，原已有）
- 🟩 浮动充裕（绿色，原写"可浮动"）
- 🟨 浮动不足（黄色，新增）
- 🔥 阻塞（红色，新增）

② 甘特图条颜色增加黄灯过渡：
```js
// 修改前
var bc=t.is_critical?'bg-red-500':'bg-emerald-500'
// 修改后
var bc=t.is_critical?'bg-red-500':(t.float_days!==undefined&&t.float_days<=1?'bg-amber-400':'bg-emerald-500')
```
- 关键路径 → 红色条
- 浮动天数 ≤ 1 → 黄色条（浮动不足预警）
- 浮动天数 ≥ 2 → 绿色条（浮动充裕）

浮动天数标签也同步变色：≤1 天显示琥珀色，≥2 天显示灰色。

③ 成员负载超载时显示文字标签：
```js
var ol=ov?'<span class="text-red-500 ml-1">超载</span>':''
```



### 31. score 语义统一：存纯技能匹配分

**问题：** scoring.py:49 的 score 字段存的是 skill_score - 0.25 * load（被负载惩罚压低的选择分），但 QAAssignment schema 的 docstring 说它是技能匹配得分。同一列匹配度在不同路径含义不同——技能完全匹配但被过载的成员可能显示匹配度 25%，误导性很强。

**修改前（scoring.py）：**
```python
score=round(max(0.0, min(1.0, scored[0][1])), 3),  # 被惩罚压低的分
```

**修改后：**
```python
score=round(max(0.0, min(1.0, best_score)), 3),  # 纯技能匹配分
```

**为什么这样改：** best_score = scored[0][1] + 0.25 * load[presenter] 已在上一行计算好，还原负载惩罚后就是纯技能匹配分。负载均衡信息保留在 reasoning 字段中。


### 32. 已完成任务不再计入 workload / 超载预警

**问题：** assign_with_balance 遍历全部 plan.tasks、不剔除 status=completed 的任务。标记任务完成后 Timeline（CPM）会缩短工期，但 QA 矩阵的负载条与负载超标预警仍把已完成任务的工时算进去——时间线和负载两个视图结论不一致。

**修改前（scoring.py）：**
```python
for t in plan.tasks:
    # 主讲：技能分 - 负载惩罚
```

**修改后：**
```python
for t in plan.tasks:
    if t.status == "completed":
        continue
    # 主讲：技能分 - 负载惩罚
```


### 33. CLI planner 子命令传入工时

**问题：** cli.py:70-84 拼的 members_str 只含 name(skills:...)，没有总可用/每日可用信息。Web 端 coordinator._step_planner 是带工时的，导致 CLI 模式下 Planner 拿到的产能信息比 Web 少，两端规划质量不一致。

**修改后：** cmd_planner 新增 --hours 参数，传入 daily: Xh, total: Yh 到 members_str。示例：python -m app.cli planner --course 软件工程 --members 张三:前端 --hours 张三:4 --deadline 2026-08-01


### 34. test_member_edit 弱测试强化

**问题：** test_member_hours_change_recomputes 捕获了 original_days 却没有断言时间线真的重算。

**修改后：** 新增断言 data["timeline"]["total_days"] < original_days，验证 Alice 工时翻倍后工期确实缩短。


### 35. README 两处同步

- 测试数 43->45（进度表滞后于实际）
- API 表补 /api/edit-members 和 /api/recompute 两个端点
队友提示词改动说明

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
| v3.0 | 七轮审查全量修复 | 已完成 |
| **v3.1** | **workbuddy 审查复核选择性修复** | **已完成** |
| v2.x | 比赛阶段扩展 | 规划中 |
### 36. half-day ?????????????????**???** timeline.py:174 ? `start_base + timedelta(days=es[tid] / 2)` ???????Python ? `date + timedelta(days=0.5)` ????????0.5 ????? 0 ??????? half-day ??????????? 2h ?????? 4h/?????????????????**????timeline.py??**```pythons_date = start_base + timedelta(days=es[tid] / 2)  # 0.5 ?????```**????**```pythons_date = (datetime.combine(start_base, datetime.min.time()) + timedelta(days=es[tid] / 2)).date()```?? datetime ????????????? date?????? available_days ???? total_seconds ?? .days ????### 37. ??????????????**???** ??????????????????????????**????** ??????????????`bg-[repeating-linear-gradient(45deg,...)]`???????????????????????????????????### 38. ???????????????????????? <= 1h?**???** ???????????0.25 * ????????????? 2 ? 20h ????????? 2 ? 2h ???????????????????? 25.8h / 32.5h / 75.1h?**????scoring.py assign_with_balance ??????**- ?????????????????????????- ??????????????????????????- ??/??????????- ???????????????????**???** ????? ~31h???? 1.1h?### 39. ???? ceil ????**???** timeline.py:196 ? `(deadline_date - today).days` ???????.days ??????1.5 ?? 1 ?????? `Math.ceil`?1.5 ?? 2 ???????????????**????** ?? `total_seconds() / 86400` ? `ceil`?????????
## v4.0 —— 任务拆解与分工双确认（2026-07-18）

**定位：** 将一次性生成计划改为“先确认拆解、再确认分工”的可编辑工作流。

---

### 关键缺陷（P0）

#### 1. 拆解后立即分工

1. **问题：** 用户无法在人员分配前修正任务粒度、工时和日期。
2. **修改前：**
   ```python
   plan = self._step_planner(inp)
   qa_matrix = self._step_matcher(plan, inp.members)
   ```
3. **修改后：**
   ```python
   plan = Coordinator().draft(inp)
   # 用户确认后
   full_plan = Coordinator().confirm(inp, plan)
   ```
4. **为什么这样改：** 根因是协调器只暴露一次性主链路；拆成两个明确接口后，草案成为可持久化、可校验的中间状态。
5. **收益：** 用户可先改任务；确认前无负责人；旧 `/api/run` 仍兼容。

#### 2. 任务模型缺少阶段和日期

1. **问题：** 只有工时无法表达实践前、中、后的执行窗口。
2. **修改前：**
   ```python
   estimated_hours: float
   required_skills: list[str]
   ```
3. **修改后：**
   ```python
   execution_stage: str = "实践中"
   start_date: Optional[date] = None
   end_date: Optional[date] = None
   ```
4. **为什么这样改：** 时间语义必须保存在任务本身，才能被编辑、分配和重新载入共同复用。
5. **收益：** 支持阶段与具体日期并存；后端拒绝结束早于开始；旧数据由默认值兼容。

### 健壮性提升（P1）

#### 3. 文件要求分析

1. **问题：** 项目要求只能手工复制，长文件无法安全提炼。
2. **修改前：**
   ```python
   additional_requirements: str = ""
   ```
3. **修改后：**
   ```python
   text = extract_text(upload.filename, raw)
   result = LLMClient().chat_structured(..., RequirementAnalysis, 0.1)
   ```
4. **为什么这样改：** 先在后端按格式提取、清理和截断，再做结构化分析，可避免直接把二进制或完整敏感原文写入日志。
5. **收益：** 支持六类常用格式；15MB 校验；解析失败返回明确原因。

#### 4. 自动分工只看任务数量

1. **问题：** 强制拉平任务会牺牲摄影、文案、排版等专业匹配。
2. **修改前：**
   ```python
   scored.sort(key=lambda x: (-x[1], work[x[0]]))
   ```
3. **修改后：**
   ```python
   score = 0.55 * skill - 0.20 * total_load - 0.15 * stage_load + 0.10 * capacity
   ```
4. **为什么这样改：** 把技能、总工时、同阶段负载和剩余产能集中为可解释评分，避免纯计数或事后强行搬运。
5. **收益：** 专业任务更匹配；同阶段拥堵降低；分配理由可展示。

### 体验优化（P2）

前端改为六步向导，新增任务拆分/合并/排序、成员看板拖拽、实时负载提示和项目对话框。README、依赖和验收测试同步更新。

#### 5. 文件分析按钮无响应

1. **问题：** 浏览器全局 `name` 与项目名称输入框冲突，提交表单时可能无法正确读取项目名称。
2. **修改前：**
   ```javascript
   course: {name: name.value}
   addMember.onclick = function(){ addMember() }
   ```
3. **修改后：**
   ```javascript
   course: {name: projectNameEl.value}
   document.getElementById('addMemberBtn').onclick = function(){ addMember() }
   ```
4. **为什么这样改：** `window.name` 和同名函数属于浏览器已有全局对象，依赖元素 ID 自动成为全局变量会产生名称遮蔽；改为显式获取元素可消除运行环境差异。
5. **收益：** 文件分析步骤可正常进入；添加成员按钮恢复；失败时页面显示明确提示。

#### 6. 文件解析期间缺少反馈

1. **问题：** 上传后需等待文本提取和模型分析，页面停留在原处会被误认为按钮失效。
2. **修改前：**
   ```javascript
   if (state.files.length) await analyzeFiles()
   ```
3. **修改后：**
   ```javascript
   show('analysis', 1)
   analysis.value = '正在上传并解析文件，请稍候…'
   await analyzeFiles()
   ```
4. **为什么这样改：** 文件解析是异步操作，必须先呈现明确的阶段切换和加载状态，并在异常时保留可编辑的错误信息。
5. **收益：** 点击立即有响应；重复提交被禁用；分析失败后用户仍可手动填写要求继续操作。

#### 7. 手动调整、对话和等待时间优化

1. **问题：** 步骤条只是静态展示；聊天无等待状态；确认分工会再次串行调用多个模型。
2. **修改前：**
   ```javascript
   steps.innerHTML = '<div>手动调整</div>'
   ```
3. **修改后：**
   ```javascript
   steps.innerHTML = '<button data-step="3">手动调整</button>'
   ```
4. **为什么这样改：** 导航必须绑定到已有草案/方案状态；技能匹配与负载均衡已有确定性算法，无需在确认阶段重复等待模型。
5. **收益：** 手动调整可点击返回；文件分析在后台完成；聊天最长等待 20 秒；模型默认超时由 120 秒降至可配置的 35 秒；确认分工由分钟级降至秒级。

#### 8. 本地首页被外部样式 CDN 阻塞

1. **问题：** Tailwind CDN 在网络较慢或不可达时同步阻塞 HTML 解析，本地页面也会长时间白屏。
2. **修改前：**
   ```html
   <script src="https://cdn.tailwindcss.com"></script>
   ```
3. **修改后：**
   ```html
   <script async src="https://cdn.tailwindcss.com"></script>
   ```
4. **为什么这样改：** 外部装饰性资源不应阻塞本地应用首屏；关键布局样式改由本地 `style.css` 提供。
5. **收益：** 断网也能立即打开；CDN 失败不影响操作；页面保留本地响应式布局。
## v4.1 —— 恢复工作台视觉与共享业务服务（2026-07-18）

**定位：** 恢复成熟的左右工作台布局，并让网页与未来清小搭适配层共享同一套项目业务逻辑。

---

### 关键缺陷（P0）

#### 1. 核心任务修改只存在于页面脚本

1. **问题：** 拆分、合并、排序和负责人调整由页面直接改 JSON，未来自然语言入口无法复用。
2. **修改前：**
   ```javascript
   state.draft.tasks.splice(index, 1)
   task.assignee_id = owner
   ```
3. **修改后：**
   ```python
   mutate_draft(plan, operations)
   apply_manual_assignment(request)
   ```
4. **为什么这样改：** 界面行为不是业务规则的可靠载体；将规则下沉到无 FastAPI 依赖的服务层后，Web、CLI 和未来 OpenAI 兼容适配层均可调用。
5. **收益：** 任务操作统一校验；自然语言入口可复用；前后端规则不再漂移。

### 健壮性提升（P1）

#### 2. 工作量统计由页面重复实现

1. **问题：** 页面自行统计工时和失衡提示，后续其他入口容易产生不同结果。
2. **修改前：**
   ```javascript
   work[owner] += task.estimated_hours
   ```
3. **修改后：**
   ```python
   snapshot = workload_snapshot(full_plan)
   ```
4. **为什么这样改：** 工时占比、阶段负载和提示属于领域逻辑，应由服务端统一计算。
5. **收益：** 拖拽后统计可信；网页和未来清小搭回复一致；规则可独立测试。

### 体验优化（P2）

#### 3. 多个全屏步骤破坏原工作台结构

1. **问题：** 新页面丢失原版左侧配置、右侧结果区的成熟操作习惯，视觉也过于简陋。
2. **修改前：**
   ```html
   <section id="page-info" class="page">...</section>
   <section id="page-draft" class="page hidden">...</section>
   ```
3. **修改后：**
   ```html
   <main class="workbench">
     <aside class="config-panel">...</aside>
     <section class="workspace-panel">...</section>
   </main>
   ```
4. **为什么这样改：** 项目配置需要持续可见，任务拆解、分工和结果应在同一工作区渐进呈现。
5. **收益：** 恢复原版视觉语言；功能位置稳定；桌面和移动端均可使用。

#### 4. 外部 CDN 影响打开速度

1. **问题：** 运行时 Tailwind CDN 在网络不可达时造成页面白屏或样式延迟。
2. **修改前：**
   ```html
   <script src="https://cdn.tailwindcss.com"></script>
   ```
3. **修改后：**
   ```html
   <link rel="stylesheet" href="/static/style.css">
   ```
4. **为什么这样改：** 核心工作台不应依赖外部网络资源。
5. **收益：** 页面离线可用；本地首屏稳定；实际首页返回 200。

#### 5. 新 HTML 与旧 CSS 缓存导致历史弹窗自动显示

1. **问题：** 浏览器保留旧样式时，新页面的 `hidden` 类失效，历史方案遮罩会在首页自动出现。
2. **修改前：**
   ```html
   <link rel="stylesheet" href="/static/style.css">
   ```
3. **修改后：**
   ```html
   <style>.hidden{display:none!important}</style>
   <link rel="stylesheet" href="/static/style.css?v=4.1.1">
   ```
4. **为什么这样改：** HTML 与 CSS 的缓存版本不同步；关键隐藏规则必须随 HTML 一起生效，并通过资源版本号淘汰旧缓存。
5. **收益：** 首页不再误显示历史弹窗；样式升级立即生效；后续缓存切换更稳定。

### 性能优化（P1）

#### 6. 文件分析和任务拆解串行调用两次大模型

1. **问题：** 上传文件后先等待要求分析模型，再等待 Planner，超时和重试可能累计超过一分钟。
2. **修改前：**
   ```python
   analysis = LLMClient().chat_structured(...)
   plan = generate_draft(input)
   ```
3. **修改后：**
   ```python
   analysis = analyze_locally(extracted_text)
   plan = generate_draft(input)  # 全流程唯一一次 LLM
   ```
4. **为什么这样改：** 文件阶段主要是事实提取和文本压缩，可用确定性规则快速完成；创造性的专业拆解才需要模型。
5. **收益：** 普通文件分析通常在数秒内完成；模型默认最长等待 25 秒；超时后立即进入确定性兜底。

## v4.2 —— 领域化兜底与最终协作视图恢复（2026-07-18）

**定位：** 模型超时时仍生成可用的专业草案，并恢复项目执行阶段所需的状态、甘特图和工作量视图。

---

### 关键缺陷（P0）

#### 1. Planner 超时只返回通用五阶段

1. **问题：** 调研、活动、摄影、报告、答辩和开发项目都退化成相同模板，工时也缺少领域依据。
2. **修改前：**
   ```python
   需求分析 -> 方案设计 -> 核心开发 -> 测试 -> 文档
   ```
3. **修改后：**
   ```python
   specs = _domain_fallback_specs(project_text, requirement_analysis)
   ```
4. **为什么这样改：** 网络服务不稳定不应让任务质量完全失效；本地兜底可依据关键词、交付物和执行阶段选择专业流程。
5. **收益：** 常见社会实践、调研、推送、报告、答辩和开发项目均有针对性拆解；工时和建议人数可编辑。

#### 2. 文件分析结果未完整进入 Planner

1. **问题：** 后台已提取要求，但 Coordinator 只传递 `additional_requirements`，模型看不到文件摘要。
2. **修改前：**
   ```python
   extra=inp.additional_requirements
   ```
3. **修改后：**
   ```python
   extra=additional + confirmed_requirements + extracted_summary
   ```
4. **为什么这样改：** 文件分析只有真正进入 Planner 上下文才会影响任务拆解。
5. **收益：** 上传文件中的交付物、时间和格式要求会参与拆解；兜底与模型使用同一份需求。

### 体验优化（P2）

- 任务编辑器隐藏内部类别字段，新增“建议人数”。
- AI 调整建议同步读取当前草案、项目输入或最终方案。
- 最终方案恢复任务状态、总体进度、时间线、甘特图、分工矩阵、工作量和报告。
- 标记完成、进行中或阻塞后，本地重算排期，不再次调用模型。

### 性能修复（P1）

#### 3. SDK 隐式重试与复杂 JSON 生成阻塞首屏

1. **问题：** SDK 默认重试会把 12 秒超时放大到 30 秒以上；当前模型对复杂 JSON 生成仍可能超时。
2. **修改前：**
   ```python
   OpenAI(api_key=key, base_url=url)
   generate_draft(input)  # 首次必须等待模型
   ```
3. **修改后：**
   ```python
   OpenAI(api_key=key, base_url=url, max_retries=0)
   generate_draft(input, use_ai=False)
   ```
4. **为什么这样改：** 首次工作流必须稳定可用；领域化规则可立即生成可编辑草案，模型增强改为用户主动触发。
5. **收益：** 首次拆解不再等待模型；“AI 重新拆解”仍保留；模型失败不会阻断后续分工。

---

## v4.3 —— 修复：首次提交不走"快速模式"，默认调 AI（2026-07-19）

**定位：** 覆盖 v4.2 的"快速模式优先"策略，让首次表单提交直接走 LLM。

**审查/修改背景：** v4.2 设计目标是首屏秒出——前端的 `generateDraft(false)` 配合后端的 `_fallback_plan`，确保第一次点击"生成草案"立即出结果，AI 增强留给"重新拆解"按钮。但实际体验上，首次生成"快速草案"后用户还要手动点"重新拆解"才能看到 LLM 效果，流程多了一步；且"快速模式"产物是模板化的瀑布兜底，无法体现 LLM 的理解能力。

---

### 关键缺陷（P0）

#### 1. 前端硬编码 `false` 导致首次生成不走 AI

1. **问题：** `index.html` 表单提交事件绑定了 `generateDraft(false)`，传参匹配了后端 v4.2 的 `use_ai=False` 调用，但用户首次生成时期望直接看到 AI 拆解结果，而不是先看模板再看 AI。
2. **修改前：**
   ```js
   // app/web/templates/index.html:159
   el('projectForm').onsubmit=function(event){event.preventDefault();generateDraft(false)};
   ```
3. **修改后：**
   ```js
   // app/web/templates/index.html:159
   el('projectForm').onsubmit=function(event){event.preventDefault();generateDraft(true)};
   ```
4. **为什么这样改：** v4.2 的"快速模式优先"策略在用户体验上多了一步冗余操作——用户每次都要点两次（生成 + 重新拆解）才能看到 AI 效果。更合理的策略是默认走 AI，若 AI 超时后端自动兜底（`Coordinator` 内已内置超时降级），不需要前端手动挡掉 LLM。
5. **收益：** 首次生成即看到 LLM 拆解效果；AI 超时时后端自带兜底，前端的 `use_ai=true` 不会导致白屏；"重新拆解"仍保留供用户迭代。

---

## v4.4 —— AI 调整建议按钮可拖拽 + 生成按钮反馈修复（2026-07-19）

**定位：** 修复两个实际体验问题：AI 调整建议按钮无法拖动且拖拽后误触抽屉弹出；AI 生成时 spinner 跑到了不可见的按钮上。

**审查/修改背景：** v4.4 初版（19日13:38）实现了基础拖拽逻辑，但有两个隐藏问题：`onclick` 绑定方式导致拖拽松手后 click 仍触发弹出抽屉；`generateDraft(true)` 调用按钮参数指向 draft view 中的 `redraftBtn`（不可见），用户看不到加载反馈。本版是 v4.4 的完整修复版。

---

### 关键缺陷（P0）

#### 1. 拖拽后 click 事件仍触发抽屉弹出

1. **问题：** `onclick=openAssistant` 绑定 + `mousedown` 拖拽是两条独立事件路径。拖拽松手后浏览器仍会触发 click 事件，抽屉弹出，用户无法靠拖拽移开按钮。
2. **修改前：**
   ```js
   el('assistantBtn').onclick=openAssistant;
   // onEnd 中试图 btn.click=function(){} 覆盖 .click() 方法（无效）
   ```
3. **修改后：**
   ```js
   // 移除 onclick，在 IIFE 中用 addEventListener 统一管理点击
   btn.addEventListener('click',function(e){
       if(dragging){e.stopPropagation();e.preventDefault();dragging=false}
       else{openAssistant()}
   });
   ```
4. **为什么这样改：** `onclick` 属性无法被其他事件处理器条件拦截；`addEventListener('click')` 配合 `dragging` 状态标志可精确控制：拖拽时 `preventDefault`，非拖拽时正常调用 `openAssistant`。
5. **收益：** 拖拽松手不再弹出抽屉；点击行为不受影响。

#### 2. 表单提交时加载 spinner 跑到了不可见的 `redraftBtn` 上

1. **问题：** `generateDraft(true)` 内 `var button=useAi===true?el('redraftBtn'):el('generateBtn')`，表单提交走 `true` 分支找 `redraftBtn`，该按钮在 draft view 中（不可见），用户看到的 `generateBtn` 无任何反馈。
2. **修改前：**
   ```js
   el('projectForm').onsubmit=function(event){event.preventDefault();generateDraft(true)};
   async function generateDraft(useAi){
       var button=useAi===true?el('redraftBtn'):el('generateBtn');
       // 在不可见的 redraftBtn 上显示 spinner...
   }
   ```
3. **修改后：**
   ```js
   el('projectForm').onsubmit=function(event){event.preventDefault();generateDraft(true,el('generateBtn'))};
   async function generateDraft(useAi,btn){
       var button=btn||(useAi===true?el('redraftBtn'):el('generateBtn'));
       // 在用户可见的 generateBtn 上显示 spinner
   }
   ```
4. **为什么这样改：** 表单提交是用户视角的"首次生成"，反馈必须出现在用户点击的那个按钮上；`redraftBtn` 调用不传 btn，函数自动退回到 `redraftBtn` 自身——单一接口兼容两处调用。
5. **收益：** 首次生成时 `generateBtn` 正确显示 "AI 正在生成…" spinner；"重新拆解"按钮 spinner 行为不变。
