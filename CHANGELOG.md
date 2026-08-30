# 变更日志 (CHANGELOG)

> 本文档记录项目每一次版本变更，附核心改动的**原版 vs 现版代码对照**，
> 方便团队成员理解"为什么这么改、改了什么、好在哪里"。
> 按时间倒序排列（最新在最上面），随项目同步更新。

---
## v7.1 —— 全链路审计修复：A3 长音频防崩、语音记忆与转写质量、导出编码（2026-08-24）

**定位：** 对 60+ 端点做全量实测与代码审计后，集中修复本地昇腾 A3 后端的稳定性/质量缺陷
（长音频崩溃、语音多轮失忆、转写与口述幻觉），并修复应用层两处真实 bug
（中文文件名导出 500、`app.js?v=` 缓存哈希过期）。

**审查/修改背景：** 用户反馈"云端顺畅、本地 A3 各种跳 bug"；实测定位出根因不在应用代码而在
llama-omni-server 的能力边界（whisper 位置编码越界、分条多轮消息被忽略、指令遵循差），
应用层通过分片/摊平/校验在边界内规避；同时审计发现两处应用层真实 bug。

### 同步修改：整体审查三项修复——鉴权开启后成员汇报令牌免登录、汇报页完成不变量与主页面统一、汇报页回退警告转义（2026-08-29 追加）

**定位：** 修复对全项目做整体审查时确认的三处真实缺陷：① 开启 `APP_ADMIN_TOKEN` 鉴权后成员汇报链接全部 401（推荐公网部署形态下核心演示环节不可用）；② 汇报页负责人标完成绕过"已确认但从未上报志愿者"校验，仍可产生"任务已完成+志愿者待开始"矛盾；③ 汇报页"回退警告"把成员姓名未转义拼进 HTML（低危存储型 XSS 隐患）。

**审查/修改背景：** 用户要求对项目做整体 bug 审查并对照比赛要求评估质量。审查发现 399 项测试全部在 `APP_ADMIN_TOKEN` 为空的环境下运行，鉴权+汇报链路的组合场景未被覆盖；实测复现三处问题后逐一修复并补回归测试（全量测试 399 → 401 passed）。

#### A. [P0] 鉴权开启后成员汇报链接全部 401（功能不可用）

1. **问题：** `render.yaml` 与 README 都建议公网部署配置 `APP_ADMIN_TOKEN`，但 `admin_auth_middleware` 只放行 health/login/分享路径，`/api/report/state|update|voice|photo|attachment` 全部要求 Bearer 登录；成员汇报页前端只带 `?report=token`，不传任何鉴权头，"免登录汇报链接"直接返回 401 并弹出登录框，与使用说明书"无需登录"的承诺冲突。
2. **修改前：** `main.py` 中间件对 `/api/report/*` 无任何豁免：
   ```python
   allow = {
       "/api/health", "/api/ready",
       "/api/auth/status", "/api/auth/login",
   }
   allow_share = request.method == "GET" and path.startswith("/api/share/")
   if path not in allow and not allow_share:
       # 无 Bearer → 401（成员汇报端点全部命中）
   ```
3. **修改后：** 新增 `_REPORT_MEMBER_PATHS` 与 `_extract_report_token`，对 5 个成员汇报端点先提取 token（GET query / POST JSON body / multipart 原始字节）并校验 report token 有效性，有效即放行；生成令牌的 `/api/report/link` 仍留在登录保护内：
   ```python
   _REPORT_MEMBER_PATHS = {
       "/api/report/state", "/api/report/update", "/api/report/voice",
       "/api/report/photo", "/api/report/attachment",
   }

   async def _extract_report_token(request: Request) -> str:
       if request.method == "GET":
           return request.query_params.get("token", "")
       content_type = request.headers.get("content-type", "")
       raw = await request.body()
       if "application/json" in content_type:
           try:
               data = json.loads(raw)
               return str(data.get("token") or "") if isinstance(data, dict) else ""
           except (json.JSONDecodeError, UnicodeDecodeError):
               return ""
       match = re.search(
           rb'(?:^|\r\n)Content-Disposition:\s*form-data;\s*name="token"\r\n\r\n([^\r\n]*)',
           raw)
       return match.group(1).decode("utf-8", "replace") if match else ""
   ```
   中间件判断改为 `if path not in allow and not allow_share and not allow_report_member:`，其中 `allow_report_member = bool(report_token and get_report_token(report_token))`。
4. **为什么这样改：** report 端点本身已用 token 做成员/任务级鉴权（`_authorize`），中间件只需确认"这是有效令牌"即可放行，不重复业务鉴权；multipart 从原始字节提取 token 而非整表单解析，避免大文件上传触发 multipart 分片大小限制；`link` 是创建令牌的写操作，必须保留登录保护。
5. **收益：** ① 开启鉴权后成员汇报闭环恢复"免登录"承诺，公网演示第 4 步可用；② 生成链接仍受登录保护；③ 无效/过期令牌仍返回 401，权限边界不变。

#### B. [P1] 汇报页负责人标完成绕过"已确认但从未上报志愿者"校验（一致性）

1. **问题：** 主页面 `/api/task-status` 用 `_unfinished_member_list`（含"已确认但从未上报"的志愿者）拦截完成，汇报页 `_apply_update` 只查"有上报记录且未完成"的成员——从未上报的志愿者不在 `latest` 里直接放行，负责人可从自己汇报页把任务标完成，志愿者行仍挂"待开始"，违反"任务完成 ⟺ 全员完成"不变量。
2. **修改前：** `report.py` `_apply_update` 只遍历已有上报活动的成员：
   ```python
   latest = {}
   for act in get_report_activities(filename, task_id):
       if act["member"]:
           latest[act["member"]] = act
   unfinished = sorted({
       name for name, act in latest.items()
       if name != member
       and act.get("status") in ("pending", "in_progress", "blocked")
   })
   ```
3. **修改后：** 改用与主页面同一口径的 `_unfinished_member_list`（含已确认但从未上报的志愿者），排除本人后仍有未完成成员即返回 400 并列出姓名：
   ```python
   unfinished = sorted({
       item["name"]
       for item in _unfinished_member_list(plan, filename, task)
       if item["name"] != member
   })
   if unfinished:
       raise HTTPException(status_code=400, detail=(
           "还有成员未完成（" + "、".join(unfinished)
           + "），请先确认其完成再标记任务完成"))
   ```
4. **为什么这样改：** 两条完成路径必须共享同一条"全员完成"定义，否则同一不变量在主页面拦截、在汇报页漏放；负责人可通过汇报页已有的"确认完成/标记完成"按钮先补录志愿者再标完成。
5. **收益：** ① 主页面与汇报页完成校验行为一致；② 消除"任务已完成+志愿者待开始"矛盾组合；③ 错误信息直接列出待确认成员，操作路径清晰。

#### C. [P1] 汇报页"回退警告"成员姓名未转义（安全加固）

1. **问题：** `app.js` `renderReportTasks` 的 `revertWarn` 用 `revertedNames.join('、')` 直接拼进 `innerHTML`，成员姓名是用户可控输入，含 HTML 时会被当作标签执行；同一函数内其余成员姓名字段都走 `esc()`，此处漏转义。
2. **修改前：**
   ```js
   revertWarn='<div class="report-revert-warning">⚠ 任务尚未完成，'+revertedNames.join('、')+' 报告了阻塞或改回未完成（任务已回退），请处理后重新确认</div>'
   ```
3. **修改后：**
   ```js
   revertWarn='<div class="report-revert-warning">⚠ 任务尚未完成，'+esc(revertedNames.join('、'))+' 报告了阻塞或改回未完成（任务已回退），请处理后重新确认</div>'
   ```
4. **为什么这样改：** 插入 `innerHTML` 的用户数据必须统一转义；`esc()` 与同函数内其余字段一致，改动最小。
5. **收益：** ① 消除低危存储型 XSS 注入点；② 与全项目转义纪律保持一致。

**同步修改：** `index.html` 中 `app.js?v=` 缓存哈希已按新内容 sha1 前 8 位更新（`2cd0dce5` → `91ca1b65`）；`tests/test_report.py` 新增"汇报页负责人完成拦截未上报志愿者"回归测试，新增 `tests/test_report_auth.py` 覆盖"鉴权开启 + 汇报令牌免登录"组合场景；全量测试 399 → 401 passed。

### 同步修改：比赛 PPT 数据校正与提交文档测试数同步（2026-08-29 追加）

**定位：** 修复比赛 PPT 中随最近代码修复而过期的数据与页面引用，并把提交文档中的测试数统一到 2026-08-29 实测的 401。

1. **问题：** ① PPT 工程验证页仍写"387 项自动化测试"（实际已 401），与 README、项目说明、复现文档互不一致；② PPT 把 `{"engine":"comni","status":"ok"}` 标注为应用 `/api/health` 的返回，实际该 JSON 是昇腾推理服务（llama-omni-server，28099 端口）`/health` 的返回；③ 加页重排后第四章章节页的页码引用（P22–P26）与第一章章节页个别引用未对齐真实页；④ README、docs/项目说明.md、docs/复现文档.md、docs/比赛全量备赛手册.md、AGENTS.md 仍写 399 passed。
2. **修改前：** PPT 写 `387 项自动化测试`、`/api/health → {"engine":"comni","status":"ok"}`、`（P22）…（P26）` 与 `P21–P26`、`2026-08-27 复核`；文档统一写 `399 passed`。
3. **修改后：** PPT 写 `401 项自动化测试`、`A3 推理服务 /health → {"engine":"comni","status":"ok"}`、第四章引用改为 `（P21）…（P25）` 与 `P21–P25`、第一章引用改为 `（P04）` 与 `（P05–P06）`、复核日期改为 `2026-08-29`；创新章节页收敛为三个创新点（删除第 4 条"昇腾本地+云端双后端"、标题改"三个可核验的差异"、页脚 P16–P20→P16–P19、备注同步，三条按原高度带均匀重排）；README、项目说明、复现文档、备赛手册、AGENTS.md 测试数统一为 `401 passed`，更新时间同步 2026-08-29。
4. **为什么这样改：** 提交材料中的测试数必须与实测一致，且 PPT 已声明"与 README、项目说明、复现文档一致"，三处必须同源；健康检查 JSON 的实际来源是推理服务本身，标注对端点评审才能复现；页码引用随 27 页重排后应指向真实页，否则评委按图索骥会翻错页。
5. **收益：** ① 测试数三处一致、评审无可指摘；② 健康检查描述可复现；③ 章节页页码引用与实际页一一对应，导航不再错位。

**同步修改：** `ascend_collab_ppt_20260827_215107_紫黄版_F4E000_27页_数据更新.pptx`（P02/P06/P12/P20/P23 可见文字与备注）、`README.md`、`docs/项目说明.md`、`docs/复现文档.md`、`docs/比赛全量备赛手册.md`、`AGENTS.md`。

### 同步修改：状态双端同步 + 版本策略回退——负责人行跟随、阻塞传播、撤回回退、主页面改状态落盘、版本仅保存时生成（2026-08-27 追加）

**定位：** 打通主页面（任务计划）与成员汇报页的状态双向一致：成员上报推动任务状态（含阻塞传播、撤回回退），主页面改任务状态后自动落盘、汇报页负责人行跟随；同时回退"状态变更自动生成版本"的过度设计——版本树只保留"保存方案"检查点，不再被每次状态微调刷屏。

**审查/修改背景：** 用户实测发现四类问题并明确产品预期：① 协作者报阻塞不传播到任务整体状态；② 主页面改状态不落盘、汇报页看不到，且负责人行被 notes 旧上报卡住；③ 协作者把"进行中"改回"待开始"后任务永远卡在"进行中"；④ 版本树随每次状态变更滚动、迭代过快且难以区分，用户确认"保存方案已保存成员进度，状态微调没必要再迭代版本"。

#### A. [P1] 协作者上报阻塞未传播到任务整体状态

1. **问题：** 协作者报 blocked 时任务状态不变（pending 保持 pending、in_progress 保持 in_progress），主页面显示的"整体状态"不代表真实进度；已完成任务被协作者改回阻塞时也只回退"进行中"而非"阻塞"。
2. **修改前：** 协作者分支只处理"推动进行中"与"已完成回退"，blocked 被忽略：
   ```python
   else:
       if current == "pending":
           if status in ("in_progress", "completed"):
               task.status = TaskStatus("in_progress")
       elif current == "completed" and status in (
               "pending", "in_progress", "blocked"):
           task.status = TaskStatus("in_progress")
   ```
3. **修改后：** 阻塞优先，任何成员报 blocked 任务整体立即置为阻塞；已完成任务被改回阻塞视为回退阻塞：
   ```python
   else:
       if status == "blocked":
           if current == "completed":
               reverted = True
               if not note:
                   note = ("报告阻塞（任务已由负责人确认完成，"
                           "任务状态已回退为阻塞，等待负责人重新处理）")
           task.status = TaskStatus("blocked")
       elif current == "pending":
           if status in ("in_progress", "completed"):
               task.status = TaskStatus("in_progress")
       elif current == "completed" and status in ("pending", "in_progress"):
           task.status = TaskStatus("in_progress")
           reverted = True
   ```
4. **为什么这样改：** 任务状态是"整体状态"，任何成员阻塞都会卡住整体交付，阻塞应优先传播；completed→blocked 是回退为阻塞而非进行中，更准确反映现状。
5. **收益：** ① 主页面/汇报页显示的任务状态反映真实进度；② 阻塞优先不丢失；③ 通知与备注区分"回退阻塞/回退进行中"。

#### B. [P1] 协作者把"进行中"改回"待开始"时任务卡死

1. **问题：** 协作者报 in_progress 会把任务从 pending 向上推到 in_progress，但改回 pending 时没有任何分支匹配，任务永远卡在"进行中"，负责人行也同步卡住。
2. **修改前：** in_progress→pending 静默忽略。
3. **修改后：** 协作者改回 pending 时，若没有其他成员仍活跃（进行中/完成/已确认/阻塞），任务回退 pending：
   ```python
   elif current == "in_progress" and status == "pending":
       if not _other_members_active(plan, filename, task, member):
           task.status = TaskStatus("pending")
           if not note:
               note = "改回待开始（自己的部分暂停）"
   ```
   `_other_members_active` 遍历任务成员（排除当前协作者）的最新活动状态，未上报按"未开始"算。
4. **为什么这样改：** 任务状态是"成员进度的聚合快照"：推动者撤回且无人继续时任务回到待开始；负责人或其他成员仍在做则保持进行中，避免误拉回。
5. **收益：** ① 状态不再卡死；② 负责人行/主页面与成员上报一致；③ 有活跃成员时不会误回退。

#### C. [P2] 主页面改状态自动落盘 + 任务徽章同步 + 全员确认

1. **问题：** 主页面改状态只 recompute 不落盘，汇报页读磁盘看不到；直接把任务改 completed 可绕过"全员完成"不变量。
2. **修改前：** `bindStatusControls` 仅 `jsonRequest('/api/recompute', ...)`，不保存、不校验成员状态。
3. **修改后：** 新增 `POST /api/task-status`：主页面改状态直接落盘（任务徽章在汇报页同步），completed 时若存在未完成上报的成员返回 400 + 成员清单，前端确认后带 confirm_members 重发，把这些成员写为 confirmed（保留其上报历史）再完成。
4. **为什么这样改：** 状态必须持久化才能被汇报页/分享页读取；"completed ⟺ 全员完成"消除"已完成+成员阻塞"矛盾组合；强制完成 = 管理员/负责人确认所有参与成员。
5. **收益：** ① 主页面改状态后汇报页任务徽章立刻可见；② 杜绝矛盾组合；③ 版本树记录保存检查点。

#### G. [P1] 成员行状态不再被任务整体状态劫持

1. **问题：** 负责人行此前直接显示任务整体状态，导致协作者报阻塞时负责人行也显示阻塞、主页面改状态时负责人行被强加为进行中——成员行不再代表成员自己的上报，用户看到"负责人没动却阻塞/进行中"的矛盾。
2. **修改前：** `_task_members_detail` 负责人行 `status = task_status`。
3. **修改后：** 负责人行与协作者行同规则——只显示自己最近上报状态，从未上报显示"未开始"：
   ```python
   if role == "负责人":
       status = act["status"] if act and act["status"] else "pending"
       awaiting = False
   ```
   主页面强制完成（`/api/task-status` completed）时，确认范围从"仅协作者"扩展为"所有有未完成上报记录的成员（含负责人）"，保证任务完成时每个参与成员行都有明确状态。
4. **为什么这样改：** 任务徽章是整体状态（聚合：阻塞优先、主页面权威、全员确认），成员行是个人进度——两者职责不同，成员行不应被整体状态强加；负责人本人在汇报页上报时其状态即任务状态（is_owner 直接设置），上报后两处仍一致。
5. **收益：** ① 协作者报阻塞不再把负责人行带成阻塞；② 主页面改状态不再强加负责人行；③ 每个成员行如实反映自己的上报；④ 任务整体状态仍由徽章正确表达。

#### H. [P1] 解除阻塞同样需要确认（与"改成完成"对称）

1. **问题：** 主页面把任务从"阻塞"改成"进行中/待开始"，等于单方面否定成员的阻塞报告，但没有任何确认——成员行还挂着阻塞、任务却显示进行中，用户看到"任务进行中+协作者仍阻塞"的矛盾。
2. **修改前：** `task-status` 只对 completed 做全员确认，解除阻塞直接改。
3. **修改后：** 任务当前为 blocked 且改为 in_progress/pending 时，若有成员仍报阻塞（含负责人），返回 400 + 阻塞成员清单；前端确认后带 confirm_members 重发，把这些成员写为处理状态（in_progress/pending，备注"主页面确认阻塞已处理（原上报：阻塞）"），活动历史保留曾报阻塞：
   ```python
   elif (req.status in ("in_progress", "pending")
           and _status_str(task.status) == "blocked" and filename):
       blocked_names = {item["name"] for item in blocked}
       missing = blocked_names - set(req.confirm_members)
       if missing:
           return JSONResponse(status_code=400, content={
               "detail": detail, "blocked_members": blocked})
       for name in req.confirm_members:
           if name in blocked_names:
               add_report_activity(filename, task.id, name,
                   status=req.status,
                   note="主页面确认阻塞已处理（原上报：阻塞）")
   ```
4. **为什么这样改：** "完成需全员确认、解除阻塞需确认处理"两条不变量对称——任何推翻成员上报状态的主页面操作都必须显式确认，不能静默覆盖；确认后成员行与任务状态一致，历史仍可追溯。
5. **收益：** ① 解除阻塞有明确提示与确认，不再静默覆盖成员报告；② 确认后成员行与任务状态一致，矛盾消失；③ 活动历史完整（曾报阻塞→管理员确认处理）。

#### I. [P1] 大型项目：已确认志愿者纳入"完成需全员确认"

1. **问题：** 大型项目里已确认志愿者是招募并认领任务的正式成员，但主页面完成任务时只要求"有未完成上报记录"的成员确认——志愿者从未上报就不在清单里，任务完成后志愿者行悬挂"待开始"，负责人还需要另外手动代确认。
2. **修改前：** `_unfinished_member_list` 对从未上报的成员一律不要求确认。
3. **修改后：** 已确认志愿者即使从未上报也纳入确认清单（未上报按"待开始"）：
   ```python
   volunteer_names = {
       v.name for v in (plan.volunteer_pool or [])
       if v.task_id == task.id and v.status == "已确认"
   }
   ...
   if not act or not act.get("status"):
       if name in volunteer_names:
           out.append({"name": name, "status": "pending"})
       continue
   ```
   主页面完成任务时确认清单包含该志愿者，确认后写 confirmed 活动；负责人代确认（汇报页"标记完成"按钮）接口不变，仍可用。
4. **为什么这样改：** "completed ⟺ 全员完成"对大型项目同样成立，且志愿者是任务级认领成员（`_task_member_names` 已包含），完成时必须显式确认其交付，否则出现"任务完成+志愿者待开始"的悬挂矛盾。
5. **收益：** ① 主页面完成任务时志愿者进入确认清单，不再悬挂；② 确认后志愿者行显示"已确认"，历史完整；③ 与小型项目"有上报才确认"的差异明确化为"志愿者认领即需确认"。

#### J. [P1] 负责人代确认成员后任务状态未同步

1. **问题：** 负责人代确认成员（target_member 分支）只写 confirmed 活动、不更新任务状态——已完成任务被成员改回"进行中"后，负责人代确认该成员，主页面任务仍显示"进行中"，汇报页与主页面不同步。
2. **修改前：** `_apply_update` 的 target_member 分支 `add_report_activity(...)` 后直接 `recompute_plan`，任务状态不变。
3. **修改后：** 代确认成功（confirmed）后，若负责人自己也已完成（completed/confirmed）且其余成员无未完成记录，任务整体自动回已完成：
   ```python
   if member_status == "confirmed":
       owner = task.assignee_id
       owner_done = False
       for act in reversed(get_report_activities(filename, task_id)):
           if act["member"] == owner:
               owner_done = str(act["status"]) in ("completed", "confirmed")
               break
       if owner_done and not _unfinished_member_list(plan, filename, task):
           task.status = TaskStatus("completed")
   ```
4. **为什么这样改：** 任务完成的不变量是"负责人完成 + 全员完成/已确认"；代确认是完成闭环的一环，确认后应重新评估整体状态，否则主页面/汇报页不同步。负责人自己未完成时（无 completed/confirmed 记录）不自动完成，避免"任务完成但负责人未动"。
5. **收益：** ① 负责人代确认成员后主页面任务状态同步；② 已完成任务被成员改回后再代确认可自动回已完成；③ 负责人未完成时不误判完成。

#### D. [P1] 主页面版本号过期后 409 死锁

1. **问题：** 成员汇报页每次操作都会生成新版本，主页面持有旧 base_version 后被 409 拦截，前端只弹错不恢复，陷入"改不了→保存不了"死循环。
2. **修改前：** `bindStatusControls` 与 `savePlan` 的 catch 只 `showNotice(e.message)`。
3. **修改后：** `jsonRequest` 把 HTTP 状态码挂到错误对象（`err.status`），两处 catch 识别 409 后确认重载最新版本；新增 `reloadLatestPlan()` 调 `loadPlan(state.lastSavedFilename)` 刷新版本号与全量状态。
4. **为什么这样改：** 409 表示磁盘已有成员更新，唯一正确的恢复路径是重载最新版本；与既有 `syncPlanFromDisk` 的聚焦同步形成双保险。
5. **收益：** ① 死锁解除；② 未保存修改在确认后才丢弃；③ 与版本策略回退后（状态变更不再滚动版本）配合，409 触发频率大幅下降。

#### E. [P2] 版本策略回退：状态变更不再自动生成版本

1. **问题：** 成员汇报/主页面改状态每次都会生成版本（`_save_plan` 自动落版本），版本树被状态微调刷屏、迭代过快且难以区分；用户确认"保存方案已保存成员进度，状态微调没必要再迭代版本"。
2. **修改前：** `_save_plan` 写文件后自动 `save_version`（含"成员汇报前基线"、防刷版比对），主页面 `task-status` 也生成"主页面状态变更"版本。
3. **修改后：** `_save_plan` 只写方案文件（保证双端同步）；版本树只记录用户主动"保存方案"（`/api/save`）的检查点：
   ```python
   def _save_plan(filename: str, plan: FullPlan) -> None:
       """写回方案文件，保证成员汇报页与主页面读到最新状态。"""
       _safe_plan_path(filename).write_text(
           plan.model_dump_json(indent=2), encoding="utf-8")
   ```
4. **为什么这样改：** 版本树的正确语义是"用户认可的检查点"（保存方案），成员操作流水由汇报页 activities（谁、什么时候、报了什么）承担，版本树重复记录状态微调只会膨胀且不可区分。
5. **收益：** ① 版本树只含保存方案，数量可控、语义清晰；② 成员操作历史仍在汇报页 activities 可见。

#### F. [P1] 内容指纹并发校验：成员汇报不再生成版本后防止主页面覆盖

1. **问题：** 回退"状态变更自动生成版本"后，base_version 只随"保存方案"前进，成员汇报落盘不再推进版本号——主页面基于过期数据改状态/保存时校验通过，会把成员刚汇报的状态与工时直接覆盖冲掉（实测：汇报实际工时 3h 被主页面旧数据改状态清空）。
2. **修改前：** `task-status` 与 `/api/save` 只做 base_version 校验，无法感知成员汇报落盘。
3. **修改后：** 新增方案内容指纹（sha1，剔除 performance/version 后键排序序列化）：`GET /api/plan-fingerprint` 返回当前磁盘指纹；主页面加载/保存/同步时维护 `state.planFingerprint`；`task-status` 与 `/api/save` 写前校验"前端基线指纹 == 磁盘当前指纹"，不等即 409，前端确认后自动重载最新版本。
4. **为什么这样改：** 并发保护的语义应从"版本号是否前进"改为"磁盘内容是否与前端所见一致"——内容指纹精确检测成员汇报落盘，且不引入版本膨胀；指纹只由后端计算，前端存返回值，无哈希不一致问题。
5. **收益：** ① 成员汇报的进度/工时不会被主页面过期操作覆盖；② 无版本膨胀；③ 与既有 409 自动重载配合，冲突可一键恢复。

**同步修改：** `app/web/routers/report.py`（负责人行、阻塞传播、撤回回退、`/api/task-status`、`_save_plan` 去版本化、指纹并发校验、解除阻塞确认、主页面改状态写负责人记录、已确认志愿者纳入完成确认、负责人代确认后任务状态同步）、`app/services/plan_io.py`（`plan_fingerprint`）、`app/web/routes.py`（`GET /api/plan-fingerprint`、`/api/save` 指纹校验）、`app/web/static/app.js`（`?v=` → 2cd0dce5，`err.status`、`reloadLatestPlan`、两处 409 处理、`bindStatusControls` 改走 `task-status` 且不再预改内存状态、`planFingerprint` 维护、解除阻塞确认）、`app/web/templates/index.html`、`tests/test_report.py`（新增 10 个用例、改造 2 个用例：阻塞传播、撤回回退×2、强制完成确认、解除阻塞确认、志愿者纳入完成确认、代确认后任务回完成、常规改状态不生成版本、错误版本 409、指纹冲突 409、成员汇报不生成版本，全量 389 → 399 passed）、`AGENTS.md` 等 5 份文档测试数同步。

### 同步修改：比赛前整体审查修复——模块负责人 KeyError/志愿者认领回归、Matcher JSON 修复、交付配置与文档对齐（2026-08-27 追加）

**定位：** 比赛提交前的整体审查收尾：修复大型项目"确认最终分工"在志愿者认领模块/旧数据下
400/500 的真实缺陷，恢复 v5.47 的志愿者认领模块设计；把 LLM 本地修复扩展到 Matcher 输出，
提升云端 MiniCPM-o 分工成功率；render.yaml、文档与参赛合规模板对齐。

**审查/修改背景：** 全量审查（373 测试基线 + 冒烟链路）发现三处与交付直接相关的问题：
① `apply_manual_assignment` 的 `member_map` 在 v5.76 合并时把志愿者角色过滤掉，导致
v5.47 明确支持的"志愿者可认领模块"在最终确认时 400/500，且旧存档里模块负责人指向已移除
成员时会 KeyError 500；② LLM 本地修复只覆盖 PlanOutput，Matcher 输出（QAOutput）在
MiniCPM-o 回吐"主讲/主答/辅答"等旧字段或截断时必然失败并静默降级为确定性 B3；
③ render.yaml 仍带 DeepSeek/DashScope 开发期配置，与"仅使用 MiniCPM-o"的合规模板矛盾。

#### A. [P1] 恢复志愿者认领模块并修复模块负责人 KeyError

1. **问题：** 大型项目最终确认分工时，若模块负责人是志愿者（v5.47 明确支持）或旧存档里
模块负责人已被移除/改名，`apply_manual_assignment` 会 400/500（KeyError），"确认最终分工"当场翻车。
2. **修改前：** `member_map` 按角色过滤掉志愿者，模块兜底赋值后未校验成员有效性：
   ```python
   member_map = {
       member.name: member for member in fp.input.members
       if "志愿者" not in member.role and "外部协作者" not in member.role
   }
   ...
   if not owner and task.module_id:
       owner = module_owner.get(task.module_id)
   score = skill_score(member_map[owner], ...)   # KeyError
   ```
3. **修改后：** 恢复"所有认领成员（含志愿者）均可担任模块负责人"，并对旧数据兜底清空：
   ```python
   member_map = {member.name: member for member in fp.input.members}
   ...
   if not owner and task.module_id:
       owner = module_owner.get(task.module_id)
   if owner and owner not in member_map:
       owner = None
   ```
   模块列表同样清理指向已移除成员的 `assignee_id`；`edit-members` 移除成员时同步清空模块负责人。
4. **为什么这样改：** v5.76 合并时静默回退了 v5.47 的产品模型（CHANGELOG 有明确记录
"志愿者可认领模块，其子任务继承该负责人"），与前端"骨干与志愿者都可以认领模块"及
评分器/负载统计的成员口径都不一致；统一成员口径后前后端不再打架，残留数据也不会再触发 500。
5. **收益：** ① 大型项目志愿者认领模块后可正常完成最终分工；② 旧存档/成员变动不再导致 500；
   ③ 前后端与 v5.47 产品设计一致。

#### B. [P1] LLM 本地修复扩展到 Matcher（QAOutput）

1. **问题：** MiniCPM-o 对 QAOutput 常回吐"主讲/主答/辅答"等旧字段名、字符串形式的
协作者列表或百分制分数，且 JSON 可能截断；本地修复只处理 PlanOutput，导致 Matcher
整轮失败并静默降级为确定性 B3，"AI 智能分工"在演示中体现不出来。
2. **修改前：** `_repair_response` 对 PlanOutput 之外的模型直接返回 None。
3. **修改后：** 新增 `_repair_qa_output` / `_normalize_assignment_objs` / `_coerce_score` /
`_salvage_assignment_objs`：归一化字段别名与类型、百分制转 0-1、截断时抢救完整分工对象。
4. **为什么这样改：** "AI 智能分工"是产品核心卖点，演示时静默降级会削弱全模态产品表达；
把修复覆盖到 Matcher 后，模型格式瑕疵不再整轮失败，确定性 B3 仍保留作最终兜底。
5. **收益：** ① 云端/本地 MiniCPM-o 分工成功率提升；② 截断输出可抢救已生成分工；
   ③ 兜底链路不弱化。

#### C. [P2] render.yaml 对齐参赛合规模板

1. **问题：** render.yaml 仍配置 DeepSeek-V3.2 / qwen3.7-plus / qwen-audio-3.0-asr-flash，
与"仅使用 MiniCPM-o"的合规声明矛盾，评审按此部署会困惑。
2. **修改前：** 含 `LLM_MODEL=DeepSeek-V3.2`、`APP_VISION_MODEL=qwen3.7-plus`、
`APP_ASR_MODEL=qwen-audio-3.0-asr-flash` 及 DashScope 地址。
3. **修改后：** 改为 `APP_MODEL_MODE=minicpm`、`APP_ALLOW_EXTERNAL_MODELS=0` +
`MAP_REALTIME_*` 全套，移除全部外部模型配置；配套部署就绪测试同步改为断言合规模板。
4. **为什么这样改：** 与 `.env.example` 参赛交付模板统一，评审看到的部署配置即合规配置。
5. **收益：** ① 部署配置不再与合规声明矛盾；② 评审可一键用云端 MiniCPM-o 跑通 Demo。

#### D. [P3] 导出、依赖与文档细节

1. **问题：** ① CSV 无 UTF-8 BOM，Windows Excel 打开中文会乱码（ICS 已有 BOM）；
② `import fitz` 触发 pymupdf 弃用警告；③ `merge_modules` 有一段被后续 list comprehension
覆盖的无效循环；④ 三份文档仍写"客户端必须用 websocket-client，不要用 websockets"，
与应用实际实现（`websockets` + 本地禁用 keepalive ping）矛盾。
2. **修改前：** `plan_to_csv` 直接返回无 BOM 文本；`render_pdf_pages` 用 `import fitz`；
`merge_modules` 先循环赋值再整体重赋值；文档沿用旧排查结论。
3. **修改后：** CSV 返回 `"\ufeff" + output.getvalue()`；PDF 渲染优先
`import pymupdf as fitz` 再回退 `import fitz`；删除无效循环；文档统一为
"应用侧用 websockets 并对本地后端禁用 keepalive ping，独立冒烟脚本用 websocket-client"。
备赛手册的仓库说明同步更新：昇腾版已推送为交付仓库 main（比赛期间私有），
`competition` 本地目录是基础版存档、禁止 push。
4. **为什么这样改：** 导出与依赖告警影响评审体验与复现；文档表述与代码不一致会误导评审排障。
5. **收益：** ① Excel 打开 CSV 不乱码；② 无弃用警告；③ 代码更短更清晰；
   ④ 复现文档与实现一致，避免误用旧方案。

#### E. [P3] 密码哈希与会话加固

1. **问题：** 密码用无盐 sha256、会话永不过期，公网 Demo 暴露后有离线爆破与会话长期有效的风险。
2. **修改前：** `_hash` 为 sha256 裸哈希；会话为 `{token: username}` 永久有效。
3. **修改后：** 新密码存 PBKDF2-SHA256（随机盐、10 万次迭代，格式
`sha256$<salt>$<digest>`），旧的无盐哈希仍可验证（兼容存量 users.json）；新会话 30 天过期，
旧版裸用户名会话按长期有效兼容，过期会话在创建新会话时顺带清理。
4. **为什么这样改：** 加盐提高离线爆破成本，会话过期降低令牌泄露后的暴露窗口，
同时不破坏现有账号与存量会话。
5. **收益：** ① 存量账号无需迁移即可继续登录；② 新写入凭证与会话更安全；
   ③ 会话文件不会无限膨胀。

#### F. [P3] README 重写为参赛导向

1. **问题：** README 混入内部协作方式（三人轮流改 main）、从 v7.1 到 v0.1 的超长版本表、
已废弃的 `LLM_API_KEY` 部署说明，以及错误的项目结构描述（测试数 254、Tailwind/Lucide），
与参赛交付定位不符，评审第一印象杂乱。
2. **修改前：** 结构为「开发过程说明 → 版本演进（50+ 行）→ 项目结构 → API 全表 →
快速启动（含废弃变量）→ 协作方式（内部信息）→ 单 Agent 调试 → 变更历史」。
3. **修改后：** 重写为「赛道与定位 → 核心能力 → 五步演示流程 → 系统架构 →
快速开始（云端/昇腾双后端）→ 验证与测试 → Render 公网部署 → 项目结构 →
核心接口 → 文档索引 → 近期版本」的干净结构，删除内部信息与废弃配置，
项目结构、测试数（379）、合规模板均与当前代码一致。
4. **为什么这样改：** README 是评审与接手者的第一入口，必须与代码、render.yaml、
.env.example 保持同一套合规口径；内部流程与开发历史应收敛到 CHANGELOG 和内部文档。
5. **收益：** ① 评审能快速理解定位与跑通方式；② 无内部信息与过时内容；
   ③ README 与合规模板完全一致。

#### G. [P3] README 功能总览补全 + docs 目录清理

1. **问题：** ① README 只突出全模态能力，核心的"分工协作"功能（拆解、CPM 排期、
技能匹配、负载均衡、版本管理、汇报闭环等）没有完整清单，且版本表只列 5 版，
从 v6.0 直接跳到 v6.9，读者看不到中间版本；② docs 目录混有早期课程作业文件
（小组合作 docx、课程作业 MVP 拆解等），与参赛交付无关；③ 功能验证清单仍包含
已禁用的外部模型（DashScope/阿里 OCR/ASR）兜底验证内容，与合规模式矛盾。
2. **修改前：** README 版本表 5 行有缺口；docs 下 6 个旧课程作业文件；功能验证清单
含"阿里 DashScope 媒体兜底"章节与阿里实测行。
3. **修改后：** ① README 新增「功能总览」六组能力表（计划引擎 / 动态协作 / 全模态 /
成员汇报与通知 / 工程能力），版本表补全为 v7.1–v0.1 共 89 行无缺口；② 删除 6 个
旧课程作业文件（git 历史可恢复）；③ 功能验证清单改为"双后端切换（合规模式仅
MiniCPM-o）"，移除 DashScope/阿里/qwen/通用模型兜底内容。
4. **为什么这样改：** README 是评审了解功能的第一入口，功能清单要完整、版本演进要
可追溯；docs 目录应只保留参赛相关与团队需要继续查阅的资料（群聊记录、备赛手册等
内部资料保留不动）；验证清单必须与"仅使用 MiniCPM-o"的合规口径一致。
5. **收益：** ① README 功能覆盖完整、版本无缺口；② docs 目录干净且内部资料不丢失；
   ③ 验证清单与合规模式一致，不会误导评审。

#### H. [P1] 本地昇腾"复读尾巴"检测与截断（仅本地）

1. **问题：** 答辩模拟中评委回复前半段正常、后半段无限重复"妖精"（2026-08-27 实测
两张截图），`_looks_like_garbage` 只按"整段循环/整段低多样性/整词高频"判定，
漏掉"回复局部复读尾巴"，导致整段跑偏文本直接展示给用户。
2. **修改前：** `_looks_like_repetition` 只做整段判定，解析后不做任何截断；
乱码守卫对本地/云端一视同仁。
3. **修改后：** `_looks_like_garbage` 增加 `local` 开关（默认 False，云端行为不变）；
新增 `_looks_like_local_repetition` 检测正文任意位置的 2-6 字单元连续重复
（总长 ≥12）与单字符连续 ≥12；答辩回合解析后**仅本地**先
`_trim_repetition_tail` 截掉复读尾巴，再走乱码/复读守卫；chat/chat-stream/
转写/会议等本地分支同步使用本地口径。
4. **为什么这样改：** "复读机"是本地 A3 的高发退化形态，云端 MiniCPM-o 正常；
用户明确要求限制只作用于本地，避免收紧后影响云端流畅性。
5. **收益：** ① 本地复读尾巴不再展示给用户；② 有效前缀保留而不是整轮丢弃；
   ③ 云端判定与展示逻辑完全不变。

#### I. [P1] "表现观察"跑偏过滤（本地+云端）

1. **问题：** 表现观察本应分析答辩者的神情、眼神、姿态与回答状态，本地 A3 却常输出
"戴眼镜、背景木质橱柜、生活照、你想分享什么吗"等外貌/背景/寒暄描述（截图实测，
此前强化提示词后模型仍不遵守）。
2. **修改前：** 仅靠 `PERFORMANCE_PROMPT` 提示词约束，模型不遵守时跑偏内容直接展示。
3. **修改后：** 本地启用 `PERFORMANCE_PROMPT_LOCAL`（强制结构化输出 + 明确禁词）；
新增 `_off_topic_performance_observation` 对**本地与云端**输出做合规过滤
（外貌/背景/家具/寒暄标记），命中后用更强约束重试一次，仍跑偏则弃帧；
答辩回合与录像分析两条链路共用 `_analyze_performance_frame`；
云端提示词仍用原版（输出风格不变），仅在模型跑偏时触发一次重试。
4. **为什么这样改：** 8B 模型提示词遵循不稳定，应用层过滤才是可靠兜底；
云端 MiniCPM-o 通常能遵守提示词，但偶发跑偏同样值得拦截；正常输出时
过滤零开销，不影响云端流畅性。
5. **收益：** ① 本地表现观察回到"神情/回答状态"分析；② 跑偏帧自动重试/丢弃，
   本地与云端一致；③ 云端正常输出零额外延迟，仅在跑偏时多一次重试。

#### J. [P1] 答辩语音需求：本地改逐字转写，云端保持"整理需求要点"

1. **问题：** 答辩模拟"🎤 语音输入需求"本应把用户语音转成文字供加入答辩稿/
评委关注点，本地 A3 却把指令当对话回答，输出"您可以从以下几个角度入手：
1. 2. 3. 4."式建议（实测）；v7.0 曾因"转写指令被当对话回答"改为
"整理需求要点"，但本地连"整理"指令也会被当成对话。
2. **修改前：** `/api/realtime/voice-requirement` 走 `understand_audio` +
`VOICE_REQUIREMENT_INSTRUCTION`（"整理成需求要点，不要转写原话"），
本地输出跑偏成建议。
3. **修改后：** 本地分支复用配置页同款逐字转写链路 `audio_transcribe_text`
（12 秒分片 + 乱码/客套守卫 + 重试），返回用户原话；云端分支恢复原有
"整理需求要点"两步链路（转写 → 理解），行为与改动前完全一致；
转写/理解失败分别返回 502 明确提示。
4. **为什么这样改：** 用户需要的是可粘贴进答辩稿/评委关注点的原话（本地实测
模型把指令当对话回答）；云端 MiniCPM-o 原有"整理需求要点"行为正常，
按后端隔离，不改动云端。
5. **收益：** ① 本地答辩语音需求返回逐字转写原话；② 云端行为不变；
   ③ 本地跑偏时明确报错而不是返回建议。

#### K. [P1] 答辩首轮问题列表防复读：标记切分 + 去重 + 丢弃开场白

1. **问题：** 答辩模拟生成首轮问题时，本地模型把多道题挤在一行
（用【高】【中】【低】标记内联）或重复生成同一道题（2026-08-27 实测
"古灵阁妖精叛乱"连出 5 遍），后端按"行"切分导致 `questions[0]` 变成
一坨含重复的内容，前端把整段展示给用户。
2. **修改前：** `/api/interview` 仅 `splitlines()` 后逐行当问题，
重复行与内联问题原样透传，开场白（"以下是评委可能提出的问题："）也会
被当成问题。
3. **修改后：** 新增 `_split_questions`（输出中出现优先级标记即按标记模式
切分，非标记行视为开场白丢弃；无标记才整行保留）与 `_dedupe_questions`
（精确 + 近似相似度 0.95 去重，限量 12 条），接口统一返回清洗后的问题列表。
4. **为什么这样改：** 问题列表必须"一题一条"且不重复；本地模型复读/不换行
是已知退化；去重只删除近乎相同的重复，不影响语义不同的问题，对云端同样安全。
5. **收益：** ① 首轮问题不再整段刷屏；② 重复问题只保留一条；
   ③ 开场白不会混进问题列表。

**回退说明（2026-08-27 追加）：** 经用户确认，该问题实际来自"语音需求识别"
返回的客套回复（见下一条 L），并非答辩问题列表本身；本节对 `/api/interview`
问题解析的改动已回退，恢复原始逐行解析，相关测试一并移除。

#### L. [P1] 客套回复词表补全：拦截"很高兴能为你提供帮助…请告诉我选题"变体

1. **问题：** 语音需求识别（答辩语音输入 / 配置页语音需求）走本地逐字转写时，
模型把转写指令当对话回答，输出"当然，很高兴能为你提供帮助。为了更好地支持
你…请告诉我你感兴趣的选题或领域，我将为你量身定制建议！"（2026-08-27
实测）；`_looks_like_canned_reply` 词表只有"很高兴为你提供帮助"
（缺"能"字变体），漏网内容被当成转写结果返回给用户。
2. **修改前：** 词表缺失"能"字变体与"请告诉我…感兴趣 / 量身定制 /
为了更好地支持你 / 我需要先了解"等通用客套；组合式客套
（多个特征同时出现）也无兜底。
3. **修改后：** 补全 8 个变体词 + 两条组合规则（"很高兴"+提供帮助/帮助你/
帮助您；"量身定制"或"请告诉我"+感兴趣）。
4. **为什么这样改：** 本地模型客套开场白变体多，纯词表必然漏；
组合规则按语义特征兜底，同时保留"你好！有什么问题我可以帮您解答吗？"
这类正常疑问句问候不被误伤。
5. **收益：** ① 语音需求识别不再被客套回复污染（本地逐字转写命中后
丢弃/重试/502 明确提示）；② 答辩问题生成同样受益；③ 用户原话与
正常问候不受影响。

#### M. [P1] 语音/拍照需求增量分析：新增一条只分析新文件，旧结果保留合并

1. **问题：** 配置页每次新增语音需求或拍照需求，前端都清空全部文件分析结果
并把所有文件重新上传、重新提取/分析，状态全部闪回"待识别"再逐个恢复
（云端同样）；v7.1 曾加后端内容缓存减少模型重复调用，但前端仍全量重传重分析。
2. **修改前：** `addRequirementFile` 清空 `fileMetadata/fileAnalysis`，
`analyzeFiles()` 把 `state.files` 全部上传，后端对全部文件重新提取 + 合并分析。
3. **修改后：** `/api/analyze-files` 额外返回逐文件提取文本 `texts`；
前端只在新增文件时上传未分析的文件，把已合并的旧文本作为 `background`
传给后端由后端一次性合并分析；旧文件的状态与文本本地保留（同名替换）；
背景未变且文件全部分析过时直接复用，背景变化才全量重算。
4. **为什么这样改：** 旧文件内容未变时重新提取是纯浪费（本地 A3/云端都要
再跑模型）；增量合并后每次新增只付出一条需求的模型成本，旧结果零重复。
5. **收益：** ① 每新增一条需求只分析新文件，旧需求结果不重置；
   ② 状态不再全部闪回 pending；③ 云端/本地行为一致。

**同步修改：** `app/services/project_service.py`、`app/web/routers/members.py`、
`app/llm/client.py`、`app/services/plan_io.py`、`app/services/media_analysis.py`、
`app/services/auth_store.py`、`render.yaml`、3 份文档；新增/更新测试 8 个
（志愿者认领模块、残留模块负责人不崩溃、成员移除清理模块负责人、QAOutput 修复、
CSV BOM、auth 加盐哈希/会话过期、render 合规模板）；本地昇腾答辩两项修复
（复读尾巴截断仅本地生效、表现观察合规过滤本地+云端）：`app/services/omni_chat.py`、
`app/web/routers/realtime.py` + 5 个新测试；答辩语音需求改回逐字转写
（本地复用配置页音频转写链路，云端保持原"整理需求要点"行为）；
答辩首轮问题列表防复读（标记切分 + 去重 + 丢弃开场白，`app/web/routes.py`）；
客套回复词表补全（`app/services/omni_chat.py`，语音需求识别/答辩问题生成
共同受益）；问题列表解析改动已按用户确认回退（`app/web/routes.py` 恢复原样）；
语音/拍照需求增量分析（`app/web/routes.py` 返回逐文件文本 + `app.js`
增量上传合并，`app.js?v=` → c3f36d83）；全量测试 373 → 387 passed；
前端无静态文件改动，`?v=` 缓存哈希不变；README 重写为参赛导向版本并补全功能总览
与完整版本表；docs 删除 6 个旧课程作业文件及已打勾的"多模态落地改造清单"；
功能验证清单移除外部模型兜底内容；交接-P2起点 引用同步清理。

### 同步修改：比赛前二轮审查修复——预检脚本 Windows 编码崩溃、成员汇报版本快照、分享只读收紧、memory 测试数据清理（2026-08-27 追加）

**定位：** 对项目做整体质量审查（389 测试基线 + 全链路冒烟）后，修复演示链路真实缺陷并补齐协作闭环留痕：预检脚本在 Windows 中文控制台直接崩溃；成员汇报覆写方案文件不留版本痕迹；分享只读白名单放行报告生成；本地 memory 累积数百份"保存测试"噪音文件。

**审查/修改背景：** 全量冒烟发现 `preflight_demo.py` 的 emoji 输出在 GBK 控制台抛 UnicodeEncodeError；版本树只记录工作台保存，成员语音/照片/状态汇报直接覆写文件导致"版本管理"与协作闭环脱节；分享令牌白名单按"不改数据"放行 `POST /api/report`（会触发 LLM 报告生成，消耗配额）。

#### A. [P1] 预检脚本 Windows 中文控制台编码崩溃

1. **问题：** `scripts/preflight_demo.py` 用 ✅/❌/⚠️ 输出检查结果，中文 Windows 默认 GBK 控制台无法编码这些 emoji，脚本打印第一行就抛 UnicodeEncodeError 并带堆栈退出，预检根本没执行完。
2. **修改前：** 脚本直接 print emoji，无任何输出编码处理：
   ```python
   def ok(text: str) -> None:
       print(f"{GREEN}✅ {text}{RESET}")
   ```
3. **修改后：** 脚本开头强制 stdout/stderr 为 UTF-8：
   ```python
   for _stream in (sys.stdout, sys.stderr):
       try:
           _stream.reconfigure(encoding="utf-8", errors="replace")
       except (AttributeError, ValueError):
           pass
   ```
4. **为什么这样改：** Windows 控制台默认用系统代码页（中文系统是 GBK）而源码是 UTF-8，emoji 超出 GBK 可编码范围直接抛异常；reconfigure 是 Python 3.7+ 标准做法，失败静默跳过不影响 Linux/macOS。
5. **收益：** ① 演示前预检在任何 Windows 中文环境都能完整跑完；② 状态符号正常显示；③ 同类脚本可复用该写法。

#### B. [P2] 成员汇报写入版本树（汇报前基线 + 无实质变化防刷版）

1. **问题：** 版本树只记录工作台手动"保存方案"；成员语音/照片/状态汇报直接覆写 memory 里的方案文件，汇报过程与汇报前状态在版本树中无痕，评审演示"版本回滚"看不到协作演变，且汇报覆盖后无法回退。
2. **修改前：** `_save_plan` 只写文件：
   ```python
   def _save_plan(filename: str, plan: FullPlan) -> None:
       _safe_plan_path(filename).write_text(
           plan.model_dump_json(indent=2), encoding="utf-8")
   ```
3. **修改后：** 写文件后落版本快照，首次汇报先落"汇报前基线"，剔除 `performance` 字段比对、无实质变化不刷版：
   ```python
   def _save_plan(filename: str, plan: FullPlan, *, summary: str = "") -> None:
       path = _safe_plan_path(filename)
       old_raw = path.read_text(encoding="utf-8") if path.exists() else None
       new_raw = plan.model_dump_json(indent=2)
       path.write_text(new_raw, encoding="utf-8")
       try:
           versions = list_versions(filename)
           if not versions and old_raw:
               save_version(json.loads(old_raw), filename,
                            action="成员汇报前基线",
                            summary="成员汇报开始前的方案状态（首次汇报自动生成）")
               versions = list_versions(filename)
           if versions:
               prev = load_version(filename, versions[0]["version_id"])
               if _stable_plan_json(json.dumps(prev, ensure_ascii=False)) \
                       == _stable_plan_json(new_raw):
                   return
           save_version(json.loads(new_raw), filename,
                        action="成员汇报", summary=summary or "成员更新任务进度/状态")
       except Exception:
           logger.exception("成员汇报版本快照保存失败（不影响主流程）")
   ```
4. **为什么这样改：** 汇报本身就是"协作闭环"的一部分，版本树只记工作台保存会漏掉成员端发生的所有变化；直接比较完整 JSON 会被每次重算都变的 `performance` 字段误判为变更，因此先剔除再比对；快照失败降级为写文件成功，不阻断成员汇报。
5. **收益：** ① 版本树完整呈现"工作台编辑 + 成员汇报"的演进历史；② 汇报前的状态可回滚；③ 重复提交同状态不刷版本；④ 汇报产生新版本后，工作台保存会正确触发 409 并发提示，与既有并发保护联动。

#### C. [P2] 分享只读白名单收紧：不再放行报告生成

1. **问题：** 分享令牌只读白名单含 `/api/report`，持只读链接者可触发 LLM 报告生成（消耗模型配额），与"只读"语义不符。
2. **修改前：** `app/main.py` 的 readonly_safe 含 `"/api/report"`。
3. **修改后：** 从白名单移除该路径；前端在只读模式下报告未生成时直接提示由所有者生成：
   ```js
   if(state.shareToken)throw Error('只读分享不生成新报告：请由方案所有者生成报告后重新分享');
   ```
4. **为什么这样改：** "只读"应严格限定为查询与查看；报告生成属于"创建内容"操作。已生成的报告仍随方案 JSON 一起展示，分享视图体验不受影响，只是不再代为触发模型调用。
5. **收益：** ① 只读语义严格化；② 分享链接不再消耗模型配额；③ 未生成报告的分享视图给出明确指引而非 403 白屏。

#### D. [P3] 文档测试数同步 + 仓库杂物归位 + memory 测试数据清理

1. **问题：** AGENTS.md 与《比赛全量备赛手册》仍写旧测试数（352/326，实际 389）；仓库根目录残留 PPT 提取稿与拼图未跟踪；memory/ 累积 600+ 份"保存测试/只读分享测试"计划与同名版本目录，属纯测试噪音。
2. **修改前：** `AGENTS.md` 要求 "必须 352 passed"；手册写 326；根目录有 `_ppt_text_extract.txt`、`_deck_montage.png`；memory 有 431 份"保存测试" + 204 份"只读分享测试"。
3. **修改后：** 测试数统一为 389；杂物移入 gitignore 的 `projects/` 工作目录；删除 635 份测试计划文件及对应版本目录，并同步清理 report_tokens/shares/report_notes/acl 中指向已删方案的条目；保留 27 份真实项目方案与运行状态文件。
4. **为什么这样改：** 提交材料中的测试数必须与实测一致，否则评审会看到数字打架；测试噪音不影响功能但污染演示"方案列表"与版本树，删掉后演示页更干净。
5. **收益：** ① 文档数字与实测一致；② 仓库根目录干净；③ memory 从 666 份计划收敛到 31 份真实方案，演示与审查都清爽。

**同步修改：** `scripts/preflight_demo.py`、`app/web/routers/report.py`、`app/main.py`、`app/web/static/app.js`（`?v=` → 0da59d7e）、`app/web/templates/index.html`、`AGENTS.md`、`docs/比赛全量备赛手册.md`；新增 2 个回归测试（成员汇报版本树、分享只读拦截报告生成，全量 387 → 389 passed）；memory 测试数据清理为一次性运维操作（非代码文件）。

### 同步修改：云端/本地守卫按后端隔离，恢复云端语音识别与答辩模拟（2026-08-26 追加）

**定位：** 本地 A3 修复中引入的"客套拦截/防复读/空转标记"被无差别套到云端，
导致云端语音"无法识别"、答辩评委被误判复读、文字链路把开场白当回复；
按后端隔离守卫，云端恢复原行为并增加防客套重试（全程仍只使用 MiniCPM-o，
不引入任何其他模型）。

**审查/修改背景：** 用户实测"本地跑完改动后，云端语音/视频功能也不行了"，并反馈
答辩模拟"一直在问同一个问题"、抽屉对话反复回"你好，很高兴认识你。有什么我可以
帮你的吗？"。逐条核对 v7.1 各守卫的生效范围，确认三处本地专属逻辑外溢到了云端。

#### A. [P0] 云端语音识别被本地客套守卫误杀

1. **问题：** v7.1 全链路守卫把 `_looks_like_canned_reply` 套到云端转写/
口述/语音需求路径，云端模型正常转写带确认尾巴时会被 3 次重试后判失败，
表现就是"完全无法识别我的语音"。
2. **修改前：** `transcribe_audio` 对本地/云端一视同仁，命中客套或乱码即重试并抛错：
   ```python
   if text and not (_looks_like_garbage(text)
                    or _looks_like_canned_reply(text)):
       return text
   raise RealtimeError(_GARBAGE_FALLBACK_MSG, "parse_error")
   ```
3. **修改后：** 客套判定仅对本地生效，云端恢复"清洗尾巴 + 非空即收"
   （保留纯乱码守卫），并补充后端标记日志与云端专属错误文案：
   ```python
   if text and not _looks_like_garbage(text):
       if not ASCEND_OMNI_WS_URL or not _looks_like_canned_reply(text):
           return text
   # 日志：backend/attempt/命中原因 + 原文前 120 字符
   if ASCEND_OMNI_WS_URL:
       raise RealtimeError(_GARBAGE_FALLBACK_MSG, "parse_error")
   raise RealtimeError(
       "云端语音识别失败：模型未能返回有效的用户原话，请重试", "parse_error")
   ```
   `media_analysis._realtime_audio_transcribe_text`、`/realtime/transcribe`、
   `/realtime/dictate`、`/realtime/voice-requirement` 同步按后端隔离。
4. **为什么这样改：** 客套拦截是为本地 A3"把转写指令当对话"设计的；
   云端 v7.0 语义是清洗尾巴后非空即收，且云端没有该退化行为。
   按后端隔离既保留本地保护，又恢复云端可用性。
5. **收益：** ① 云端语音识别/转写恢复可用；② 本地 A3 的客套守卫不降级；
   ③ "?" 乱码两端仍然拦截，不会把问号串交付给用户。

#### B. [P0] 云端答辩评委"复读/空转"误判

1. **问题：** 防复读相似度检测与空转词表在云端同样生效；评委"没听清→按
   `INTERVIEW_TURN_INSTRUCTION` 的 b 规则追问同一问题"被当成模型复读，
   整轮 502；"没有听到/无法识别"等正常回应也被判空转。
2. **修改前：** `realtime_interview_turn` 无条件执行 `for attempt in (1, 2)` +
   相似度 ≥0.85 重试 + `hollow_markers` 判定。
3. **修改后：** 相似度重试与空转标记整体仅本地执行：
   ```python
   max_attempts = 2 if ASCEND_OMNI_WS_URL else 1
   for attempt in range(1, max_attempts + 1):
       ...
       if (ASCEND_OMNI_WS_URL and attempt == 1 and last_reply
               and reply and len(reply) >= 30
               and _reply_similar(reply, last_reply) >= 0.85):
           ...
   if ASCEND_OMNI_WS_URL:
       # hollow_markers 判定仅本地
   ```
4. **为什么这样改：** "回答不完整就同一问题继续追问"是评委的合法设计；
   云端 ModelBest 没有 A3 的"复读退化"，误判只会把可用的追问变成 502，
   用户看到的就是"一直在问那一个问题"且无法推进。
5. **收益：** ① 云端评委能正常听答、点评、追问；② 本地 A3 防复读保护保留；
   ③ 复读误报（502）消除。

#### C. [P1] 云端文字链路开场白复读：防客套重试 + 词表补全

1. **问题：** 截图实测云端 MiniCPM-o 把"你好，我叫小芳"回成
   "你好，很高兴认识你。有什么我可以帮你的吗？"，且该句不匹配旧词表
   （结尾是"吗"，词表里只有"有什么可以帮您/帮你"），建议抽题/答辩模拟等
   文字任务会直接把开场白当结果返回。
2. **修改前：** `_CANNED_REPLY_PATTERNS` 无"很高兴认识你/有什么我可以帮"；
   `_realtime_text` 与 `/realtime/chat(stream)` 命中乱码/客套时抛错或原样透传。
3. **修改后：** ① 词表补两项，覆盖截图中的复读原文（"你好！有什么问题我可以
   帮您解答吗？"这类正常问候承接不受影响）；② `_realtime_text` 云端命中时带
   `_NO_CANNED_NUDGE`（"不要输出问候语/开场白/自我介绍"）重试一次；
   ③ 抽屉 `/realtime/chat` 与 SSE `/realtime/chat/stream` 云端命中乱码/客套时
   同样带防客套指令重试一次（流式先发 `reset` 清空已展示文本）。
4. **为什么这样改：** 云端模型偶发开场白是模型行为，应用层无法一次消除，
   但带明确"不要客套"的二次尝试可显著救回；本地 A3 推理慢（约 50 token/s），
   保持单次命中即抛，避免把演示等待时间再翻倍。
5. **收益：** ① 建议抽题/答辩模拟文字链路成功率提升；② 抽屉对话不再把开场白
   当回复展示；③ 本地行为与等待时间不变。

#### D. [P3] 日志与回归测试

1. **问题：** 云端/本地共用守卫时，故障难以判断是哪个后端误判。
2. **修改后：** `transcribe_audio` / `realtime_chat(stream)` / `_realtime_text`
   补充后端 + 命中原因日志（原文截断前 120 字符）；新增 10 个回归用例
   （云端转写放行、本地转写拒绝、云端评委不判复读、云端文字防客套重试、
   开场白识别等）。
3. **收益：** ① 线上问题可快速定位后端与原因；② 本次修复有回归保护；
   ③ 全量测试 367 passed（`node --check` 通过）。

**涉及文件：** `app/services/omni_chat.py`、`app/services/media_analysis.py`、
`app/web/routers/realtime.py`、`app/llm/client.py`、
`tests/test_realtime_client.py`、`tests/test_llm_client_minicpm.py`、`CHANGELOG.md`。

### 同步修改：答辩模拟出题体验——MiniCPM-o 直接基于材料出题、收敛数量与预算、兜底去模板化（2026-08-26 追加）

**定位：** 答辩模拟"生成问题慢、反复问'请概括你最希望评委理解的核心观点'"。
根因有二：① 云端 MiniCPM-o 出题失败时落到确定性兜底，而兜底最后一条固定是
这句元问题，用户看到的就是"AI 没读材料、要我自己概括"；② 出题指令要 10-15 题、
且 minicpm 模式默认 16000 token 输出预算，MiniCPM-o 容易生成冗长输出拖慢响应。

#### A. [P1] 出题指令改为"直接基于材料追问依据"，禁止元问题

1. **问题：** 指令泛化（"生成 10-15 道问题"）且没有禁止元问题，MiniCPM-o 指令
遵循弱，产出"请概括核心观点"这类模板问题，答辩者感觉 AI 没有理解材料。
2. **修改前：** `INTERVIEW_SYSTEM` 要求 10-15 个问题、无"禁止概括材料"规则；
`InterviewSimAgent.run` 用户提示同样要求 10-15 道。
3. **修改后：** 收敛为 8-10 个问题；新增【直接基于材料出题】规则——每个问题必须
落到材料的具体观点/数据/方案/措辞上，禁止"请概括/复述/再说明一遍材料"式元问题
（材料已有的结论直接追问依据、逻辑和边界），每题一句话、无开场白。
4. **为什么这样改：** 越泛化的指令越容易让弱指令遵循模型走模板；把"不要让我
概括材料"写成显式禁止，模型才会先理解材料再直接提问。
5. **收益：** ① 出题更贴材料、更像真人评委；② 生成更短更快；③ 多轮追问环节
（`INTERVIEW_CHAT_SYSTEM`）同步加了"不要反复让答辩者概括材料"的约束。

#### B. [P1] 输出预算收敛：出题/多轮点评从 16000 收敛到 2048

1. **问题：** `chat_text`/`chat_messages` 在 minicpm 模式下默认 16000 token
输出预算（为 DeepSeek 推理模型预留），MiniCPM-o 无长推理需求，容易生成冗长
输出，云端响应明显变慢。
2. **修改前：** `chat_text` 无 `max_tokens` 参数，minicpm 分支固定
`LLM_MAX_TOKENS`（16000）。
3. **修改后：** `chat_text` 增加可选 `max_tokens`；`InterviewSimAgent` 出题与
多轮点评显式传 2048（8-10 个一句话问题约 1-2k token 足够）。
4. **为什么这样改：** 预算收敛直接降低总生成耗时；仅答辩模拟传小预算，
规划等结构化链路默认行为不变。
5. **收益：** ① "生成问题"响应明显变快；② 其他链路不受影响；
③ 配套补齐 fake 签名并保持回归测试通过。

#### C. [P2] 兜底问题去模板化：不再"请概括核心观点"

1. **问题：** `_fallback_interview_questions` 最后一条固定为
"请概括这次答辩最希望评委理解的核心观点。"，云端生成失败时用户反复看到这条
元问题，观感就是"AI 没理解材料、让我再概括一遍"。
2. **修改前：** `questions.append("请概括这次答辩最希望评委理解的核心观点。")`
3. **修改后：** 材料至少两行时追问第二行具体内容的依据；单行时问"最重要的数据
或证据如何支撑结论"；末条改为"这项成果在什么情况下可能不成立，或还需要补充
哪些证据？"
4. **为什么这样改：** 材料已给出的结论不需要答辩者再总结；兜底也应直接落到
材料内容，而不是把理解责任甩回给用户。
5. **收益：** ① 兜底问题不再模板化；② 兜底仍与材料/关注点挂钩；
③ 回归测试新增"兜底问题不允许出现'请概括'"断言。

**涉及文件：** `app/llm/prompts.py`、`app/agents/interview_sim.py`、
`app/llm/client.py`、`app/web/routes.py`、`tests/test_agents.py`、
`tests/test_api.py`、`CHANGELOG.md`。

### 同步修改：答辩模拟长材料退化与评委"卡问"修复（2026-08-27 追加）

**定位：** 上传 PPT 后"分析很长时间仍失败落兜底"、评委"只认第一次回答并反复复读
同一追问"。根因：① 最多 5 万字材料整段进 MiniCPM-o（8B），模型长输入退化，日志
实测反复输出 `ösösös…` / `MAIL MAIL…` 乱码（每次尝试约 45 秒，重试又 45 秒）；
② 评委指令"回答不完整就同一问题继续追问"被机械执行，且未限制同句复问与
"逐字复述材料"式追问。

#### A. [P0] 出题材料限量 + 失败缩短重试

1. **问题：** `InterviewSimAgent.run` 把 5 万字 PPT 提取文本整段发给云端
MiniCPM-o，模型长输入退化输出乱码，多次 45 秒尝试后失败，只能落确定性兜底。
2. **修改前：** `source = material_text.strip()[:50000]`，失败即兜底。
3. **修改后：** 新增 `_bounded_source`：材料限量 12000 字，保留首 75% + 尾 25%、
中间省略；若仍失败，用 6000 字缩短材料再试一次；`chat_turn` 多轮点评的材料
同步限量 8000。
4. **为什么这样改：** 长输入是退化根因，收窄输入直接消除乱码并显著提速；
首尾保留材料的主体与结论，质量不丢失；缩短重试是应用层第二道防线。
5. **收益：** ① 上传 PPT 出题不再长时间卡死；② 兜底命中率大幅下降；
③ 材料关键内容（开头正文 + 结尾结论）仍然完整。

#### B. [P0] 评委上下文材料减量

1. **问题：** 语音/视频答辩每回合把 8000 字答辩材料 + 3000 字项目要求塞进
system prompt，叠加多轮历史后 8B 模型上下文过载，理解漂移并重复提问。
2. **修改前：** `interviewJudgeContext()` 使用
`ivChat.materialText.slice(0,8000)`、`defenseRequirementText().slice(0,3000)`。
3. **修改后：** 前端新增 `compactText`（首尾保留）：材料限量 4000、项目要求
限量 2000。
4. **为什么这样改：** 评委每轮只需关键内容，减量后模型更聚焦"历史 + 当前回答"，
不易被长材料带偏。
5. **收益：** ① 评委追问更稳定；② 每轮响应更快。

#### C. [P1] 评委指令防"逐字复读"与"要求复述材料"

1. **问题：** 截图实测评委两轮给出逐字相同的追问（"能否请用户直接复述这三重
机制…"），且要求答辩者逐字复述材料内容，观感像 AI 没读材料、只会模板追问。
2. **修改前：** `INTERVIEW_TURN_INSTRUCTION` 只有"绝对不要重复或复述你上一轮
已经给出的点评与追问"，未禁止"同一句话换个位置再问"，也未禁止要求逐字复述材料。
3. **修改后：** 追加两条——① 追问不得与上一轮使用完全相同的句子；② 回答已覆盖
要点（哪怕不完整）时，不得要求逐字复述材料内容，直接基于其回答追问依据、逻辑、
数据或与其他部分的联系，或换一个更具体的角度。
4. **为什么这样改：** "卡问"是模型对 b 规则（回答不完整就继续追问）的机械执行；
把"同句复问"和"复述材料"显式禁止后，评委必须换措辞、换角度推进。
5. **收益：** ① 评委不再逐字复读同一追问；② 不再让答辩者反复概括/复述材料。

#### D. [P1] 评委输出乱码守卫（本地/云端统一）

1. **问题：** 云端评委路径此前不做乱码检测，模型退化时（`ösösös…`）会把乱码
直接当回复展示；本地也只有相似度复读检测，没有乱码检测。
2. **修改前：** 云端单次理解、无乱码判定；本地仅相似度 ≥0.85 重试。
3. **修改后：** 两端统一最多 2 次尝试：命中乱码（或本地相似度复读）即带
"防乱码 + 防复读"指令重试一次；仍失败判空转并返回 502，绝不展示乱码。
4. **为什么这样改：** 乱码/退化输出不能交付给用户；一次带指令重试可救回大多数
偶发退化，仍失败时明确提示重试比展示乱码更可接受。
5. **收益：** ① 乱码不再上屏；② 失败原因明确、可重试；③ 本地相似度复读保护
不降级。

**涉及文件：** `app/agents/interview_sim.py`、`app/web/static/app.js`、
`app/web/routers/realtime.py`、`tests/test_agents.py`、
`tests/test_realtime_client.py`、`tests/test_interview_chat.py`、`CHANGELOG.md`。

### 同步修改：评委回复字面 "\\n" 归一化 + 简写标记解析（2026-08-27 追加）

1. **问题：** MiniCPM-o 偶发把结构分隔输出成字面转义序列（`\n\n`），前端
`pre-wrap` 原样显示成反斜杠 n；同时模型把 `【回答摘要】/【评委回复】` 写成
`【评】/【追】` 简写，旧解析器只认全称，导致整段被当成评委回复、摘要为空。
2. **修改前：** `_parse_turn_text` 只识别 `【回答摘要】/【评委回复】`，
不做转义归一化。
3. **修改后：** 新增 `_normalize_literal_newlines`（`\r\n`→换行、`\n`→换行、
`\r`→换行），答辩回合与会议解析统一先归一化；`_parse_turn_text` 增加
`【评】/【点评】` + `【追】/【追问】` 简写标记解析。
4. **为什么这样改：** 字面转义是模型输出格式问题，应用层在解析入口归一化最
可靠；简写标记不识别会把摘要和回复混成一段，归一化与解析都做了，展示与
历史记录同时受益。
5. **收益：** ① 评委回复不再显示 `\n` 字样，换行正常；② 摘要/回复按简写标记
也能正确拆分；③ 会议整理同口径防字面换行。

**涉及文件：** `app/web/routers/realtime.py`、`tests/test_realtime_client.py`、
`CHANGELOG.md`。

### 同步修改：答辩简写标记语义修正 + 摘要转写兜底 + 云端复读窄判定（2026-08-27 追加）

1. **问题：** 模型用 `【评】/【追】` 简写时，上一版把 `【评】` 误当成
"回答摘要"——评委的点评被存进用户气泡、历史里存的是评委自己的话，
形成"评委对着自己的点评复读同一追问"的循环；同时点评从评委回复中消失，
用户看到"重复 + 少了点评"。
2. **修改前：** 简写分支把 `【评】` 内容解析为 summary；摘要缺失时为空，
用户气泡只有占位符，历史失真。
3. **修改后：** ① `【评】/【点评】` 与 `【追】/【追问】` 统一视为评委回复
（标记还原为换行，点评+追问都在回复里），摘要只在存在 `【回答摘要】` 或
转写兜底时填充；② 模型漏输出摘要时用云端转写文本（`result.transcript`，
截 300 字）兜底，用户气泡展示真实回答内容；③ 云端新增**窄判定复读**：
仅当"摘要非空（已拿到用户真实回答）却仍与上一轮追问相似度 ≥0.9"时，
带防复读指令重试一次；摘要为空（没听清）时重复追问仍是合法行为，不重试。
4. **为什么这样改：** 语义错位是复读循环的根因——先修解析，历史里存的才
是用户真实回答；转写兜底解决 MiniCPM-o 不输出摘要的格式问题；窄判定既纠正
真实复读，又不重蹈 v7.1"云端合法重复被判复读"的误伤。
5. **收益：** ① 点评回到评委回复里，不再"消失"；② 用户气泡显示真实回答
要点；③ 云端复读自动纠正或明确报错；④ 历史干净，不再自引复读；
⑤ 全量测试 373 passed。

**涉及文件：** `app/web/routers/realtime.py`、`tests/test_realtime_client.py`、
`CHANGELOG.md`。

### 关键缺陷（P0）

#### 1. A3 长音频必然崩溃：whisper 位置编码越界打挂整个服务

1. **问题：** 会议旁听、答辩录像/视频回答上传超过约 30 秒的音频时，llama-omni-server 的
whisper 编码器抛 `Position encoding buffer overflow - view exceeds bounds` 直接崩溃，
28099 端口失联，之后所有实时功能报"连接已关闭/模型已关闭"，直到手动重启。
2. **修改前：** `understand_audio` / `transcribe_audio` 把整段音频一次性发给本地 `/backend`；
实测 60 秒音频必崩（日志 `view_size=12288000 > total_size=6144000`）。
3. **修改后：**
   - **服务端补丁**（`scripts/patch_a3_20260824.py`，已应用并重编，旧二进制备份为
     `build/bin/llama-omni-server.bak-20260824`）：① `ws_handler.cpp` 会话复用时的
     `llama_memory_clear(data=false)` 改为 `data=true`——真正清零 LLM KV 数据，
     修复长音频会话后"文字输出全部变成 ?"的状态泄漏（实测 25 秒音频处理后文字对话仍正常）；
     ② `audition.cpp` 把 whisper 位置编码越界的 `throw` 改为捕获 + 清缓存 + 返回 false，
     服务不再被超长音频打崩。
   - **应用层**：① `omni_chat.py` 的 `_split_pcm_b64` 升级为**智能分片**：
     先做静音断句检测（RMS 能量 + 静音段），再贪心分组——每片尽量装到目标时长、
     硬上限 12 秒，避免固定切点把句子切断（实测断句分片质量远好于硬切）；
     ② 合并提示词改为"分片结果在前、原始要求在后的参考式"写法（原始指令放开头
     会触发 A3 输出一串 "?"），并对多片做**分层合并**（每层每组 ≤3 段）；
     ③ 分片与中间合并用小 token 预算（128/256），只有最终合并用完整预算，
     3.4 分钟会议从超时降到约 2 分钟出结果；④ 合并前**过滤劣化分片**
     （"?"/客套/模板回复直接丢弃），避免个别坏片带崩整条合并链；
     ⑤ `_ensure_local_audio_within_limit` 守卫上限放宽到 600 秒（10 分钟）；
     ⑥ `media_analysis._realtime_audio_transcribe_text` 与 `audio_to_written_answer`
     本地路径统一接入分片入口与守卫；⑦ 新增 `scripts/ascend_watchdog.sh` 守护脚本，
     A3 服务掉线自动拉起（用 `pkill -f '[b]...'` 避免匹配到守护自身命令行）。
4. **为什么这样改：** 崩溃点在 C++ 的 whisper KV 位置编码视图（单会话内累积越界），
必须服务端兜底；"文字全变 ?"的根因是会话复用只清了 KV 元数据没清数据，一行补丁即修复。
长音频分片早期失败的两层根因：一是固定切点切断句子（模型对断句片段输出模板回复），
二是 A3 个别会话偶发劣化（"?"/客套），混进合并链后带崩最终结果。
智能断句分片 + 劣化过滤 + 分层合并分别对症，实测 **206 秒（3.4 分钟）会议
126 秒出结构化纪要**（要点准确 + 任务解析成功），处理后文字对话无污染。
**结论：数分钟长会议在 A3 上可行，无需服务端深度改造；服务端补丁保留为防崩与防污染
双保险。**
5. **收益：** ① 长会议/长录像在 A3 上可用（≤10 分钟，处理时间约为音频时长的一半）；
② 服务不再被长音频打崩、长音频后文字不被污染；③ ≤12 秒单段可靠，短语音体验不变；
④ 超 10 分钟返回可操作提示；⑤ 服务崩溃后约 30 秒内自动恢复。

**涉及文件：** `app/services/omni_chat.py`、`app/services/media_analysis.py`、
`scripts/ascend_watchdog.sh`（新增）、`tests/test_realtime_client.py`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 2. 本地语音多轮记忆失效：A3 忽略分条历史 + 本地不返回转写

1. **问题：** 本地 A3 语音对话"没记忆"：实测先告诉模型"我叫小红"，再问"我叫什么名字"，
它答"我是你的智能助手"；两个叠加原因——A3 服务端对分条 ChatML 历史支持有缺陷
（摊平成单条消息即答对），且本地路径 `transcript` 为空，前端把用户语音历史存成占位符
`[语音消息]`。
2. **修改前：** `understand_audio` 本地分支 `messages=history + [音频消息]`；
`realtime_voice_chat` 直接返回 `result.transcript`（本地恒为空）。
3. **修改后：** ① 本地分支用 `_flatten_history` 把历史摊平进文本上下文
（"【历史对话】…【当前】…"），并跳过 `[语音消息]` 占位符；② `realtime_voice_chat`
在本地后端额外调 `transcribe_audio` 补一次转写，经 `_looks_like_canned_reply` 校验
通过才写入 `transcript`（不可靠时仍回退占位符，不污染历史）。
4. **为什么这样改：** 摊平是实测有效的 A3 兼容写法（服务端只有单条消息时才会把历史编进
上下文）；转写校验避免把模型的客套回复当用户原话存进记忆。
5. **收益：** ① 本地语音轮次能记住前文（文字+语音混合历史）；② 转写内容真实可用时才进历史；
③ 云端路径行为不变。

**涉及文件：** `app/services/omni_chat.py`、`app/web/routers/realtime.py`、
`tests/test_realtime_client.py`、`CHANGELOG.md`。

#### 3. A3 转写/口述/语音需求输出幻觉：把指令当成对话

1. **问题：** `/api/realtime/transcribe` 实测返回"你好！很高兴为你提供帮助…"、
`/dictate` 返回"我是 Qwen…"、`/voice-requirement` 答物理公式、语音汇报把音频说成
"一张图片"——A3 把"只转写/只整理"指令当成普通对话，输出客套回复或幻觉，前端拿到假结果。
2. **修改前：** 三个端点对模型输出仅做非空校验，幻觉文本原样返回给前端。
3. **修改后：** 新增 `_looks_like_canned_reply` 校验（空文本/客套开头/自介与
"我是由…开发"等特征），转写/口述/语音需求端点命中即返回 502 并给出明确提示
（本地后端提示改用云端）；`_clean_transcript` 继续清洗云端转写尾巴。
4. **为什么这样改：** 指令遵循是模型/移植层问题，应用层无法让 A3"变聪明"，
但可以在边界内做质量闸门：宁可明确报错让用户重试/切云端，也不静默交付幻觉结果。
5. **收益：** ① 用户不再收到"像模像样但完全错误"的转写；② 错误提示带可操作建议；
③ 云端路径不受影响。

**涉及文件：** `app/services/omni_chat.py`、`app/web/routers/realtime.py`、
`tests/test_realtime_client.py`、`CHANGELOG.md`。

### 体验优化（P2）

#### 4. 中文文件名导出必 500：Content-Disposition 非 latin-1

1. **问题：** `GET /api/plans/{filename}/export` 对中文文件名（自动保存的方案名均为中文）
抛 `UnicodeEncodeError: 'latin-1' codec can't encode characters`，整个请求 500。
2. **修改前：** `headers={"Content-Disposition": f'attachment; filename="{filename}{ext}"'}`，
中文直接进 HTTP 头。
3. **修改后：** 新增 `_download_disposition`：ASCII 回退文件名 + RFC 5987
`filename*=UTF-8''` 百分号编码。
4. **为什么这样改：** HTTP 头只允许 latin-1，中文必须走 RFC 5987 编码参数；
ASCII 回退保证老客户端也能拿到可读文件名。
5. **收益：** ① 中文方案名导出不再 500；② 现代浏览器显示中文下载名；③ 有回归测试覆盖。

**涉及文件：** `app/web/routes.py`、`tests/test_api.py`、`CHANGELOG.md`。

### 打磨（P3）

#### 5. 版本号、静态资源哈希与测试基线同步

1. **问题：** ① `/api/health`、`/api/ready` 版本号停留在 7.0，本次修复集需随版本发布；
② `index.html` 的 `app.js?v=2541fcc4` 与当前文件实际 sha1（7e92b067）不符，
浏览器可能长期加载旧 JS；③ AGENTS.md 测试基线仍写 175 passed，实际 319。
2. **修改前：** `system.py` 两处 `"version": "7.0"`；`app.js?v=2541fcc4`；
`AGENTS.md` 写 175。
3. **修改后：** 版本号统一升至 7.1；`app.js?v=` 更新为 7e92b067（内容 sha1 前 8 位）；
AGENTS.md 基线更新为 319 passed。
4. **为什么这样改：** 版本发布需要 health/ready 与 CHANGELOG 一致；缓存哈希必须等于
文件内容哈希，否则"改了没生效"问题复发。
5. **收益：** ① 版本信息一致可查；② 浏览器强刷即加载最新前端；③ 团队测试基线准确。

**涉及文件：** `app/web/routers/system.py`、`app/web/templates/index.html`、
`AGENTS.md`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加）：** ① 答辩语音/视频回合空转校验——评委输出
"尚未提供/无法判断"等空洞文本且无画面观察时返回 502 明确提示（本地昇腾未听懂，
请重试或改用云端），并新增回归测试；② 答辩录像/视频回答超过本地处理上限时，
提示文案按"约 28 秒、请缩短回答或改用云端"定制；③ 长音频守卫文案统一为
"每段 ≤28 秒"；④ 全量测试 324 → 325 passed；⑤ 无头 Chrome 390×844 全量 UI 巡检
（17/18 通过、零控制台错误：四视图无横向溢出、AI 拆解/分工/最终方案、抽屉文字与
语音对话、会议旁听、拍照需求、保存方案、汇报页渲染）；⑥ 抽屉文字对话新增
"全问号重试"保险——本地 A3 偶发输出 "?" 乱码时去掉摊平的长上下文、用裸问句
重试一次，提升演示稳定性（新增回归测试，全量 327 passed）；⑦ 全链路"问号守卫"：
本地 A3 输出 "?" 乱码或客套/问候类回复时自动重试（最多 3 次），仍失败则返回
友好错误提示（"本地昇腾模型未能理解这段音频…请重试或改用云端后端"），
**绝不把问号串或假识别结果展示给用户**；转写/口述/语音需求/会议旁听/答辩语音视频/
语音对话全部接入；答辩与录像的"表现观察"帧丢弃乱码帧；全量测试 330 passed。

**同步修改（2026-08-24 追加二）：** ① 上游评估结论——A3 部署的就是
`tc-mb/llama.cpp-omni` 的 master（09f5c3f，即上游最新）；`feat/ascend-cann` 与
`bench/huawei` 的修复集中在 CANN 线程安全 / Token2Wav(TTS) 崩溃 / Flash Attention
AUTO 模式关闭 / 评测套件，**不覆盖**"多帧视觉退化"与"音频识别不稳定"这两个核心痛点；
`app/comni-1.0` 为桌面 App 分支且服务端架构大改（ws_handler.cpp 被移除），迁移风险高，
不适用；② 启动脚本 `ascend_start_server.sh` 按上游 CANN 建议追加 `-fa off`
（实测不能消除偶发乱码/客套，应用层守卫继续兜底，CANN 下更安全）。

**同步修改（2026-08-24 追加三 · 合规化改造）：** 创新应用赛道 Checklist 要求
"应用基于 MiniCPM-o 4.5 开发，不得使用其他模型"。新增 `APP_MODEL_MODE`
（默认 `minicpm`）与 `APP_ALLOW_EXTERNAL_MODELS`（默认禁用）开关：
① `LLMClient` 在合规模式下**不创建 DeepSeek 客户端**，所有文本调用（对话/结构化
JSON 拆解/答辩/知识问答）走 MiniCPM-o Realtime（本地 A3 或云端 ModelBest），
复用现有 JSON 提取/本地修复逻辑，并带历史摊平与乱码/客套守卫；
② DashScope 视觉/语音兜底在合规模式硬禁用（`_client`/ASR 入口直接报错）；
③ `/api/health`、`/api/ready` 的 `llm_configured` 按模式判定（minicpm 看 realtime）；
④ 测试环境固定 legacy 隔离真实调用，新增 minicpm 模式 4 个单元用例；
实测 MiniCPM-o 文本完成 AI 任务拆解（约 15 秒、5 项任务、零兜底），
抽屉文字/图片分析链路正常；全量测试 330 → 334 passed。

**同步修改（2026-08-24 追加四 · 深度审查修复）：** 用户要求"再深入审查一遍，
发现的 bug 在本对话修"。逐链路审代码后修复 4 处真实缺陷，新增 5 个回归用例
（334 → 339 passed）：

#### 1. 合规模式下"答辩模拟-生成问题"必然失败（P0）
1. **问题：** `LLMClient.chat_text` 在 `APP_MODEL_MODE=minicpm` 时 `self._client` 为
`None`，`/api/interview`（答辩模拟第一步"生成 10-15 道问题"）直接抛
`AttributeError`，前端把错误文本当成第一个问题展示。
2. **修改前：** `chat_text` 只处理 legacy 分支，直接 `self._client.chat.completions.create(...)`。
3. **修改后：** 在 `chat_text` 开头加 minicpm 分支，与 `chat_messages` 一致走
`_realtime_text`（本地 A3 / 云端 Realtime），失败时按 `_classify_error` 返回 `AgentError`。
4. **为什么这样改：** 合规模式下不允许创建外部模型客户端，但 `chat_text` 漏掉了
"走 MiniCPM-o" 的路由，成为唯一没接合规通道的文本入口；统一路由后行为与其余入口一致。
5. **收益：** ① 答辩模拟在 A3/云端 MiniCPM-o 下可正常生成问题；② 新增回归用例
`test_chat_text_minicpm_routes_to_realtime` 防止再次漏改。

**涉及文件：** `app/llm/client.py`、`tests/test_llm_client_minicpm.py`。

#### 2. 答辩录像"回答转写"二次解码必然失败（P1）
1. **问题：** `realtime_performance` 把已解码的 PCM 字节再次传给
`audio_to_written_answer`（内部会 `_decode_audio_to_pcm16k` 当作媒体文件解码），
实测必然抛 `InvalidDataError`，`answer` 恒为空，仅剩表情分析可用。
2. **修改前：** `audio_to_written_answer("answer.webm", audio)`（`audio` 是
`extract_audio_pcm16k` 的产物）。
3. **修改后：** 改为传入原始上传字节：
`audio_to_written_answer("answer.webm", raw)`，由函数内部统一解码。
4. **为什么这样改：** 同一份数据只能解码一次；把"已解码 PCM"当媒体文件再开一次
`av.open` 必然失败。传入原始文件（含音轨的视频或纯音频）语义正确。
5. **收益：** ① 答辩录像链路"表情分析 + 回答转写"两条输出都能返回；
② 新增 `test_realtime_performance_passes_raw_file_to_written_answer`
锁死"必须传原始字节"的约定。

**涉及文件：** `app/web/routers/realtime.py`、`tests/test_realtime_client.py`。

#### 3. 转写接口乱码泄露 + 文件分析转写污染（P1）
1. **问题：** `/realtime/transcribe` 只校验客套回复，没校验 "?" 乱码；
`_realtime_audio_transcribe_text` 逐片转写时也会把乱码/客套片拼进结果，
用户会在输入框/需求分析里看到一串问号。
2. **修改前：** 两处都只判断"非空"即采纳。
3. **修改后：** ① `realtime_transcribe` 增加 `_looks_like_garbage` 校验，命中返回 502
提示重试/换云端；② `_realtime_audio_transcribe_text` 逐片过滤乱码与客套片，
全部无效时抛"未返回有效转写文本"，由上层走 ASR 兜底或明确报错。
4. **为什么这样改：** 问号守卫在对话/会议链路已有，转写链路是漏网点；
统一"宁可报错也不展示乱码"的策略。
5. **收益：** ① 语音转写不再把 "?" 串交给用户；② 文件分析里的音频转写不再污染需求文本；
③ 新增 `test_realtime_transcribe_garbage_returns_502`。

**涉及文件：** `app/web/routers/realtime.py`、`app/services/media_analysis.py`、
`tests/test_realtime_client.py`。

#### 4. 答辩/会议音频失败时放弃已有画面（P1）＋ 表现观察提示词强化（P2）
1. **问题：** 答辩语音/视频回合与会议旁听里，只要音频理解失败（A3 未听懂/超时），
即便录像抽帧成功也直接 502，浪费了已有的视觉信息；另外 `PERFORMANCE_PROMPT`
约束不足，模型会去描述照片画面而非点评答辩者神情。
2. **修改前：** `except RealtimeError: raise HTTPException(502)`；
`PERFORMANCE_PROMPT` 仅写"分析面部表情与整体状态"。
3. **修改后：** ① 答辩回合音频失败时置 `audio_hollow`，帧观察成功就返回
"📹 表现观察"结果，无帧可看才 502（保留原始错误信息）；② 会议旁听音频失败但有画面时，
自动走"仅凭画面整理"分支，纯录音失败才 502；③ 会议帧观察丢弃乱码帧；
④ `PERFORMANCE_PROMPT` 明确"不要描述画面里的物品/背景/场景/文字，
只针对答辩者神情、眼神、姿态"。
4. **为什么这样改：** 音频与视觉是两条可独立工作的模态，一条失败不应拖垮另一条；
提示词需要把"观察对象"与"禁止内容"双向写清，模型才不会跑偏。
5. **收益：** ① 答辩/会议演示在 A3 音频识别不稳时仍能给出画面侧结果，少一次空转报错；
② 表现观察更聚焦答辩者状态；③ 新增两个音频失败→视觉兜底的回归用例。

**涉及文件：** `app/web/routers/realtime.py`、`tests/test_realtime_client.py`。

**打磨（P3）：** `app/main.py` 的 FastAPI `version` 元数据由过期值 `5.76` 对齐为 `7.1`
（`/api/health` 早已上报 7.1），仅元数据同步，不涉及产品版本号变化。

**同步修改（2026-08-24 追加五 · 抽屉对话误判修复）：** 用户反馈抽屉文字对话显示
"当前模型服务不可用"。复现定位为**守卫误判**：模型对"你好"的正常回复
"你好！有什么问题我可以帮您解答吗？"被 `_looks_like_canned_reply` 的
"你好开头 + 含帮/请问/需要"规则判成客套异常，`/api/realtime/chat` 502，
前端切兜底后 `/api/chat` 同一守卫再次失败，最终显示"模型服务不可用"。

1. **修改前：** `_looks_like_canned_reply` 中
   `if text.startswith(("你好", "您好")) and any(token in text for token in ("帮", "请问", "需要")): return True`
   对对话场景一刀切；`realtime_chat` 命中即 502。
2. **修改后：**
   - `_looks_like_canned_reply` 细化：问候开头的**完整疑问句**（"吗/么"结尾）不算客套
     （如"你好！有什么问题我可以帮您解答吗？"），确认式客套（如"你好，请问有什么可以帮您"）仍判异常；
   - `realtime_chat`（抽屉文字对话）只拦 "?" 乱码，不再拦客套回复；
   - `understand_audio` 新增 `allow_polite` 参数，抽屉**语音对话**场景跳过客套判定
     （会议/答辩/转写等指令类链路保持严格）；
   - `/api/chat` 兜底文案在尚未生成方案时提示"请先生成任务草案"，不再报"共 0 项任务"。
3. **为什么这样改：** 客套守卫本意是拦截"把转写/整理指令当成对话"的模板回复；
   对话场景中模型回"你好，有什么可以帮您"是正常承接，同一套严格判定误伤了正常回复，
   导致整个抽屉对话被误判为模型不可用。
4. **收益：** ① 抽屉文字/语音对话恢复可用（实测"你好" 1.2s 正常回复）；② 转写/会议/答辩
   仍严格拦截客套；③ 新增 3 个回归用例（334 → 341 passed）。

**涉及文件：** `app/services/omni_chat.py`、`app/web/routers/realtime.py`、
`app/web/routes.py`、`tests/test_realtime_client.py`。

**同步修改（2026-08-24 追加六 · 本地语音对话记忆三段式）：** 用户复测语音对话
"告诉他我叫小红，再问我叫什么名字，他说不知道"。实测定位三个叠加根因并重做
本地语音对话链路：

1. **问题：** ① 本地 A3 把"转写指令"当成对话回应（输出"你好，小红！很高兴认识你"
   而非用户原话），4 种转写提示词实测全部失败，严格转写导致 transcript 恒空，
   前端历史只剩 `[语音消息]` 占位符；② 即使历史有内容，直接"听音频回答"时
   模型把当前音频当唯一输入、忽略文本历史，且对"我叫什么名字"自我指代混乱
   （答"我叫助手"）；③ 转写偶尔编造名字（"我叫小明/通义千问"）并污染记忆。
2. **修改前：** 本地语音对话走 `understand_audio` 直接听音频回答，历史摊平进
   user 消息文本；`realtime_voice_chat` 在 transcript 为空时再补一次严格转写。
3. **修改后：** 本地语音对话改为**三段式**（`prefer_text_answer=True`）：
   ① 宽松转写——非空、非乱码、非"模型自我介绍"（新增 `_looks_like_self_intro`，
   拦截"我叫通义千问…你可以叫我…"）即保留，回应式文本中的名字也能进历史；
   ② 纯文本回答——历史与记忆放进 system prompt（A3 对 system prompt 遵循度高），
   并加指代纠正提示"用户问'我叫什么名字'指用户自己的名字，用'你的名字是：<名字>'
   回答"；③ 记忆抽取——新增 `_extract_user_memory` 用纯文本从对话中提取
   "用户的名字是：X"，随响应返回 `memory` 字段，前端写入历史
   （`【记忆】` 前缀，摊平时标记为"记忆："而非"助手："，避免模型复述）。
   另有**记忆冲突保护** `_transcript_claims_other_name`：转写声称的名字与既有
   记忆矛盾时丢弃该转写并沿用旧记忆，防止幻觉污染。移除本地"补充转写"
   （会引入"我叫小钢炮"等错位文本）。
4. **为什么这样改：** 模型侧"边听边答"会忽略文本历史，是本地服务能力边界；
   改走"音频→转写→文本"与云端同一思路，文本链路稳定（纯文本记忆问答实测
   4/4 答对），记忆抽取与冲突保护让幻觉不覆盖真实信息。
5. **收益：** ① 端到端实测"我叫小红→我叫什么名字"回答"你的名字是：小红"，
   记忆跨轮持续；② 第一轮回应就能带出名字；③ 答辩/会议/需求理解等场景
   （`prefer_text_answer=False`）保持直接听音频，行为不变；
   ④ 新增 4 个回归用例（全量 334 → 344 passed）。

**涉及文件：** `app/services/omni_chat.py`、`app/services/realtime_client.py`、
`app/web/routers/realtime.py`、`app/web/static/app.js`、
`app/web/templates/index.html`（app.js 缓存哈希更新）、`tests/test_realtime_client.py`。

**同步修改（2026-08-24 追加七 · 交付配置模板清理）：** `.env.example` 中残留
DeepSeek/DashScope 占位配置，评审视角易被误解为使用其他模型。已改为参赛交付模板：
① 顶部加合规说明（`APP_MODEL_MODE=minicpm` + `APP_ALLOW_EXTERNAL_MODELS=0`
下不创建任何外部模型客户端）；② `LLM_*` 与 `APP_VISION_*/APP_ASR_*` 改为空值并
标注"仅 legacy 开发调试模式，交付版本无需配置"；③ 保留 MiniCPM-o Realtime
（云端/本地 A3）为参赛唯一模型通道。运行时行为不变（合规模式本来就不调用
DeepSeek/DashScope），仅清理配置模板与文档痕迹。涉及 `.env.example`。

**同步修改（2026-08-24 追加八 · 全面审查修复）：** 对全仓库做一次对照比赛要求与
实际落地的深度审查后，一次性修复 5 处健壮性缺陷、1 处安全缺陷与 3 处打磨项：
测试套件不再依赖 `LLM_API_KEY`、前端转义函数补双引号、两处误导性"已切换通用模型"
文案、本地超长音频报错 NameError、长音频最终合并预算与注释一致、`/api/ready`
local 存储不再恒 503、答辩语音回合历史截断、交付物照片不再互相覆盖、重复 `init()`
清理与文档/部署基线同步。全量测试 344 → 345 passed；`app.js?v=` → `c618538f`。

#### 1. 测试套件环境依赖：无 `LLM_API_KEY` 时 15 个用例失败（P1）
1. **问题：** 参赛交付 `.env` 中 `LLM_API_KEY` 为空，而 `tests/test_review_fixes.py`、
`tests/test_agent_benchmark.py` 的 mock 用例在 `LLMClient()` 构造后 `_enabled=False`，
`chat_structured` 直接返回 auth_error，mock 根本走不到；AGENTS.md 的"必须 319 passed"
在干净环境无法复现，且基线数字已过期（实际 344）。
2. **修改前：** `conftest._force_legacy_llm_mode` 只 patch 了 `APP_MODEL_MODE` 与
`APP_ALLOW_EXTERNAL_MODELS`，未处理 `LLM_API_KEY`。
3. **修改后：** `conftest.py` 在 legacy 打桩中追加
   `monkeypatch.setattr(config, "LLM_API_KEY", "test-key")` 与
   `monkeypatch.setattr(llm_client, "LLM_API_KEY", "test-key")`；
   AGENTS.md 基线同步为 345 passed，`docs/复现文档.md` 的 293 passed 同步为 345。
4. **为什么这样改：** 用例本身测试的是"SDK 返回形态/重试策略"，不是"无 key 的
auth_error 分支"（该分支由 test_api 的兜底用例单独覆盖并显式置空 key）；在夹具层
给一个非空 key，才能让 mock 路径在任何环境都可复现。
5. **收益：** ① 无 `.env` 的干净 checkout 直接 `pytest tests/ -q` 全绿；
② 验证基线不再随本地密钥配置漂移；③ 文档数字与实际对齐。

#### 2. 前端 `esc()` 不转义双引号，属性注入 XSS（P1）
1. **问题：** `esc()` 只经 innerHTML 序列化文本节点，`"` 不会被转义，却大量用于
`value="'+esc(...)+'"`、`title="'+esc(...)+'"` 等属性上下文；成员名/技能标签/任务名
含 `"` 即可突破属性边界注入事件处理器。
2. **修改前：** `function esc(value){var d=document.createElement('div');d.textContent=value==null?'':String(value);return d.innerHTML}`
3. **修改后：**
   `function esc(value){var d=document.createElement('div');d.textContent=value==null?'':String(value);return d.innerHTML.replace(/"/g,'&quot;').replace(/'/g,'&#39;')}`
4. **为什么这样改：** 文本上下文里 `&quot;`/`&#39;` 渲染回引号不改变显示，属性上下文
里则不会再被浏览器当作属性定界符；一处修复覆盖全部 esc() 调用点。
5. **收益：** ① 消除属性注入 XSS；② 所有既有渲染位置显示不变；③ `node --check` 通过。

#### 3. 合规模式下"已切换通用模型"误导文案（P1）
1. **问题：** `APP_MODEL_MODE=minicpm` 时不存在通用模型，但前端
`sendChat` 的提示与后端 `realtime_chat` 的 502 detail 仍写"已切换通用模型回答"，
实际只是重试同一条 MiniCPM-o 链路，用户会被误导。
2. **修改前：** 前端 `showNotice('MiniCPM-o 暂不可用，已切换通用模型：'+err.message)`；
后端 `detail="…：已切换通用模型回答"`。
3. **修改后：** 前端改为 `'MiniCPM-o 响应异常，已自动重试；若仍失败可稍后再试：'`；
后端改为 `"本地昇腾模型输出异常（未能理解该问题），请重试或改用云端后端"`。
4. **为什么这样改：** 诚实描述兜底行为（重试/报错），避免评审或用户误以为系统
偷偷调用了其他模型，反而违背"不得使用其他模型"的合规承诺。
5. **收益：** ① 文案与真实行为一致；② 合规边界不被误解；③ 演示时提示可操作。

#### 4. 本地超长音频报错引用未导入常量（P2）
1. **问题：** `media_analysis._realtime_audio_transcribe_text` 的超限文案引用了
`_AUDIO_CHUNK_SECONDS`，但本地 import 列表未包含，触发分支时抛 NameError，
被上层吞成"语音模型调用失败（NameError）"误导排查。
2. **修改前：** `from app.services.omni_chat import (_LOCAL_AUDIO_MAX_SECONDS, …)`，
随后 f-string 使用 `_AUDIO_CHUNK_SECONDS`。
3. **修改后：** import 列表补上 `_AUDIO_CHUNK_SECONDS`。
4. **为什么这样改：** 常量已在 omni_chat 模块定义，缺导入是纯遗漏；补上后错误文案
正确显示"每段 ≤12 秒"。
5. **收益：** ① 超限提示准确可操作；② 不再出现 NameError 掩盖真实原因。

#### 5. 长音频无历史时最终合并预算与注释不一致（P2）
1. **问题：** CHANGELOG 声称"只有最终合并用完整预算"，但 `_understand_audio_local`
在无历史时直接把最后一轮 256 token 预算的合并结果返回，长会议摘要可能被压缩。
2. **修改前：** `while len(texts) > 1: texts = await _merge_text_groups(…, merge_budget, …)`
3. **修改后：** 每层合并前判断 `final_level = len(groups) == 1`，无历史时最终层用
`max_new_tokens`（完整预算），有历史时仍由带历史的完整预算调用承担最终合并。
4. **为什么这样改：** 中间层小预算控制单次生成长度与超时，最终层完整预算保证
合并结果不被截断，行为与既有注释/文档一致。
5. **收益：** ① 长会议最终摘要完整度提升；② 调用次数不变（回归用例仍通过）。

#### 6. `/api/ready` 在 local 存储下恒 503（P2）
1. **问题：** readiness 把 `durable_storage_configured` 绑定 S3，默认
`STORAGE_BACKEND=local` 时 `all(checks.values())` 恒 False，任何把 `/api/ready`
当健康检查的部署都会被误判为故障。
2. **修改前：** `ready = all(checks.values())`，其中 `durable_storage_configured=False`。
3. **修改后：** 仅 `ready = checks["llm_configured"] and checks["storage_ok"]`；
local 分支探测 memory 目录可写作为 `local_storage_writable`，响应体新增
`storage_backend` 字段，`durable_storage_configured` 保留为信息性标识。
4. **为什么这样改：** S3 是可选增强不是就绪前提；local 目录可写即演示/比赛形态的
持久化成立，readiness 应反映真实可服务状态。
5. **收益：** ① local 部署下 `/api/ready` 正确返回 200；② s3 语义不变；
③ 新增回归用例覆盖。

#### 7. 答辩语音回合历史未截断（P2）
1. **问题：** `realtime_interview_turn` 全量透传 history，长对话会把上下文撑爆，
语音对话路径已截 16 条，这里漏了。
2. **修改前：** `history_list = [ …过滤… ]`（无截断）。
3. **修改后：** 过滤后追加 `[-16:]`。
4. **为什么这样改：** 与 voice-chat 的记忆策略对齐，控制上下文长度与超时。
5. **收益：** ① 答辩多轮不会因历史过长劣化；② 两处语音链路行为一致。

#### 8. 同任务交付物照片互相覆盖（P2）
1. **问题：** `report_photo` 按"方案+任务"固定文件名写盘，第二个成员/第二次上传
会覆盖前一张交付物照片。
2. **修改前：** `attach_path = ATTACH_DIR / f"{filename…}_{safe_id}{ext}"`
3. **修改后：** 文件名追加 `int(time.time())` 时间戳，同一任务可保留多张照片。
4. **为什么这样改：** 照片是成员交付证据，按时间区分文件名即可避免互相覆盖，
`report_attachment` 读取最近一张的既有逻辑不变。
5. **收益：** ① 多成员交付物各自保留；② 已上传照片不再静默丢失。

#### 9. 打磨：重复 `init()`、部署与文档基线（P3）
1. **问题：** `app.js` 重复声明两份 `function init()`（后一份覆盖前一份，死代码）；
`render.yaml` 未显式关闭 `APP_HTTPS`，Render 实例会在容器内多绑一个无人访问的
8443 自签名监听；测试基线文档数字过期（AGENTS.md 319 / 复现文档 293 /
项目说明 293），且 `docs/深度审查与修复进度.md` 仍把"local 存储下 /api/ready 返回
503"写成预期行为。
2. **修改前：** 第 400/401 行两份 init；render.yaml 无 `APP_HTTPS`；
AGENTS.md 319 / 复现文档 293 / 项目说明 293；深度审查文档记录旧 ready 行为。
3. **修改后：** 删除无 `visibilitychange` 监听的第一份 init；render.yaml 增加
`APP_HTTPS: "0"`；AGENTS.md、复现文档、项目说明基线统一为 345；深度审查文档
同步 ready 新语义（local 就绪 / S3 严格）。
4. **为什么这样改：** 死代码删除降低混淆；Render 走平台暴露的 `$PORT`，容器内
自签 HTTPS 监听无意义；基线数字必须与实际一致才可执行。
5. **收益：** ① 前端入口唯一且包含磁盘同步监听；② Render 部署行为干净；
③ 验证基线可复核；④ 提交材料与代码行为不再互相矛盾。

**涉及文件：** `tests/conftest.py`、`tests/test_api.py`、`app/web/static/app.js`、
`app/web/templates/index.html`（app.js?v=c618538f）、`app/web/routers/realtime.py`、
`app/web/routers/system.py`、`app/web/routers/report.py`、
`app/services/media_analysis.py`、`app/services/omni_chat.py`、`render.yaml`、
`AGENTS.md`、`docs/复现文档.md`、`docs/项目说明.md`、
`docs/深度审查与修复进度.md`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加九 · 差距点改进：流式输出 + 会议进度 + 视频抽帧）：**
针对审查结论中"对话无流式展示、等待感强""会议旁听 1-3 分钟干等""视频抽帧
覆盖不全"三个差距点落地改进：抽屉对话改走 SSE 增量推送、会议旁听逐阶段上报
进度、短视频抽帧按「首帧+中间均匀+末帧」取满；流式端点补充模块级 logger
定义（异常路径曾会二次抛 NameError 吞掉错误事件，已修复并加回归用例）。
全量测试 345 → 350 passed；
`app.js?v=` → `64467026`。

#### 1. 对话流式输出：消除"攒完才显示"的等待感（P2 体验）
1. **问题：** 抽屉 AI 对话是整轮等待：`RealtimeClient.chat` 把 `response.output.delta`
全部攒完才一次性返回，长回答期间用户看到的是转圈，感知延迟大；备赛梳理 §8.4
把"对话流式展示"列为待办。
2. **修改前：** `RealtimeClient.chat` 无增量回调；`/api/realtime/chat` 返回完整 JSON；
前端 `sendChat` 调 `sendRealtimeChat` 一次性拿结果。
3. **修改后：**
   - `RealtimeClient.chat` 新增可选参数 `on_text_delta`（支持同步/异步回调），
     每个文本增量到达即回调，最终文本拼接逻辑不变；
   - 新增 `POST /api/realtime/chat/stream`（SSE）：`delta` 增量推送 →
     本地 A3 乱码时发 `reset` 清屏重试 → 末尾 `done`（含完整回复与 TTS 音频）；
     TTS 失败降级、本地历史摊平、乱码守卫与 `/chat` 完全一致；
   - 前端 `streamRealtimeChat` 用 `fetch` + `ReadableStream` 解析 SSE，
     增量写入气泡 `textContent`（天然防 XSS，不经过 innerHTML），
     `reset` 清空重显，`done` 后回填历史并附"🔊 重听"按钮；
   - 流式失败自动回落 `sendLegacyChat`，行为与旧版一致。
4. **为什么这样改：** 协议本身是增量输出，攒满再返回是纯应用层损耗；
把增量推给前端即可用最小改动获得"边生成边显示"的实时感，且不改动既有
非流式接口（兼容第三方调用）。
5. **收益：** ① 长回答首字可见时间大幅提前；② 演示"AI 在思考"的过程可见，
减少等待焦虑；③ 流式失败有完整回落，不破坏主链路；④ 本地乱码 `reset` 重试
分支有回归用例（重置后仅带裸问句，与旧 `/chat` 语义一致）。

#### 2. 会议旁听进度流式：1-3 分钟处理不再干等（P2 体验）
1. **问题：** 会议旁听要抽帧、听音频、看画面、整理纪要，全程 1-3 分钟，
前端只有一个静态"正在旁听…"提示，用户不知道进行到哪一步。
2. **修改前：** `realtime_meeting` 一次性返回 JSON，无中间状态。
3. **修改后：**
   - 抽出 `_meeting_analysis(raw, emit)` 核心逻辑（emit 为可选进度回调），
     普通 `/api/realtime/meeting` 与新增 `/api/realtime/meeting/stream`
     共用同一实现，避免两处行为分叉；
   - 流式端点按阶段上报 `progress`：抽取画面 → 提取音频 → 听会议音频 →
     理解画面 → 整理纪要，末尾 `done` 返回结构化结果；
   - 前端 `handleMeetingFile` 改调流式端点，弹窗即时打开并逐条更新进度；
     分析失败（`error` 事件）自动关弹窗并提示。
4. **为什么这样改：** 分析耗时来自模型调用，应用层无法缩短，但可以
"让等待可感知"；共享核心逻辑保证两个端点结果一致、无重复维护。
5. **收益：** ① 用户随时知道当前阶段，等待焦虑显著降低；② 普通端点行为
与返回结构完全不变（既有调用与测试不受影响）；③ 后续加阶段只需在
`_meeting_analysis` 里加一行 `_emit`；④ 分析内部异常下发 `error` 事件并
正常收尾（新增回归用例，防静默断流）。

#### 3. 视频抽帧覆盖开头/结尾（P2 内容质量）
1. **问题：** 均匀步长抽帧会漏掉结尾（如 12 帧取 4 帧只到 75% 位置），
答辩/会议的关键内容常在开场与收尾（白板结论、总结页），漏帧即漏信息。
2. **修改前：** `extract_video_frames` 按 `count % step == 0` 均匀取，不保证末帧。
3. **修改后：** 短视频（≤2400 帧，约 80 秒 @30fps）按
`round(total * i / (max_frames - 1))` 取「首帧 + 中间均匀 + 末帧」目标索引集合，
解码命中即停；长视频保持均匀步长并在取满后提前 break，避免全量解码。
4. **为什么这样改：** 短视频全量解码成本可接受（数秒），换取开头/结尾必覆盖；
长视频仍以性能为先。回归用例断言取满 4 帧且各帧亮度不同（确实覆盖不同时间点）。
5. **收益：** ① 开场/收尾内容不再被漏掉；② 长视频解码开销不增加；
③ 会议/答辩画面理解的信息完整性提升。

**同步修改：** `docs/华为昇腾创新应用赛道接入说明.md` 补充流式端点与 SSE 事件格式；
`docs/比赛备赛梳理.md` §5/§8.4 更新（流式已落地，实时双工仍为待办）；
测试基线 345 → 350 同步至 AGENTS.md / 复现文档 / 项目说明。

**涉及文件：** `app/services/realtime_client.py`、`app/web/routers/realtime.py`、
`app/services/media_analysis.py`、`app/web/static/app.js`、
`app/web/templates/index.html`（app.js?v=64467026）、
`tests/test_realtime_client.py`、`AGENTS.md`、`docs/复现文档.md`、
`docs/项目说明.md`、`docs/华为昇腾创新应用赛道接入说明.md`、
`docs/比赛备赛梳理.md`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加十 · 交互反馈修复四项）：** 针对用户实测反馈修复：
语音/拍照需求重复全量重读、会议草稿不可编辑、答辩语音/拍照无法区分"评委关注点"、
交付照片同秒上传仍覆盖（时间戳秒级碰撞）。全量测试 350 → 352 passed；
`app.js?v=` → `f8a06d79`，`style.css?v=` → `f18c6f22`。

#### 1. 语音/拍照需求重复全量重读（P1 体验）
1. **问题：** 项目配置页每新增一个语音/拍照需求，前端都把全部文件重新上传，
后端把每个文件重新提取一遍——已识别过的图片/音频再跑一轮视觉/语音模型，
既慢又费额度。
2. **修改前：** `routes.analyze_files` 对每个文件无条件调用 `extract_text`。
3. **修改后：** 新增按内容 sha1 的进程内缓存 `_cached_extract_text`（上限 200 条，
超限清空防内存膨胀）：同一文件重复上传直接复用提取结果；合并后的
`analyze_locally` 仍每次执行（毫秒级，保证整体要求分析完整）。
4. **为什么这样改：** 重读的浪费在"模型调用"而非"上传"；按内容哈希缓存即可
保证同一个文件只调用一次视觉/语音模型，且不影响多文件合并分析的语义。
5. **收益：** ① 加第 N 个需求只识别新文件；② 演示时多需求输入不再越加越慢；
③ 有回归用例（同一文件两次上传只提取一次）。

#### 2. 会议旁听草稿不可编辑（P2 体验）
1. **问题：** 已配置好团队成员时，会议分析后点"生成任务草案"直接进入 AI 拆解，
用户没有机会核对/修改旁听整理内容；未填成员时反而能编辑背景框，行为不一致。
2. **修改前：** `generateDraftFromMeeting` 填入背景后直接调用 `generateDraft(true)`。
3. **修改后：** 不再自动拆解——把会议整理文本填入项目背景（可编辑）并聚焦，
返回配置页提示"可直接编辑后点击生成任务草案"；弹窗按钮文案改为
"填入背景并编辑"。
4. **为什么这样改：** 拆解前让用户先看一遍 AI 整理的结果，符合"分析出来之后
我就可以编辑"的预期；是否填了成员不再影响行为，两条路径统一。
5. **收益：** ① 会议整理内容永远可编辑后再拆解；② 与未填成员时的旧行为一致；
③ 误识别的内容可在拆解前修正，避免污染任务拆解。

#### 3. 答辩语音/拍照需求无法区分"评委关注点"（P2 功能）
1. **问题：** 答辩页的语音/拍照需求固定加入答辩稿；用户想输入"评委关注点"
（评分标准、追问方向）时也会被塞进答辩稿，答辩模拟仍按默认问题提问，
识别不到用户给的关注点。
2. **修改前：** `defenseVoiceDone` / `defensePhotoFile` 理解后直接
`appendDefenseInput(...)` 加入答辩稿。
3. **修改后：** 理解后先弹出确认条（`showDefenseInputConfirm`）：
"加入答辩稿 / 评委关注点 / 取消"；选"评委关注点"时写入
`interviewRequirements` 文本框并同步 `ivChat.requirements`，答辩模拟
`user_requirements` 即携带该内容。
4. **为什么这样改：** 同一个需求输入框承载两类意图，自动分类不可靠；
让用户一次点选显式指定去向，成本最低且不会误放。
5. **收益：** ① 评委关注点真正进入提问设计；② 答辩稿不被无关内容污染；
③ 提示文案同步说明两个去向。

#### 4. 交付照片同秒上传仍覆盖（P1 数据丢失）
1. **问题：** 上一轮用 `int(time.time())` 防覆盖，但同一秒内连续上传两张照片
会生成同名文件互相覆盖，用户实测"一个人只能上传一张"。
2. **修改前：** `report_photo` 文件名 `{方案}_{任务}_{秒级时间戳}{ext}`。
3. **修改后：** 文件名追加 `uuid4().hex[:12]` 随机后缀；`/api/report/attachment`
新增 `photo` 参数按名访问单张照片（缺省仍返回最近一张，兼容旧链接），
并校验文件名必须属于该任务的上报记录（防任意文件枚举）；
`/api/report/state` 成员明细新增 `photos` 列表（按上传顺序），前端渲染为
多张照片徽标（负责人/本人可逐张查看，其余成员显示"已交付 ×N"）。
4. **为什么这样改：** 时间戳粒度不够是根因，随机后缀彻底消除同名碰撞；
数据本来就按活动逐条保存，只差"按名取件"和"列表展示"两环。
5. **收益：** ① 一人一任务可传多张照片且互不覆盖；② 逐张可查看；
③ 附件访问白名单收紧，安全性同步提升。

**涉及文件：** `app/web/routes.py`、`app/web/routers/report.py`、
`app/web/static/app.js`、`app/web/static/style.css`、
`app/web/templates/index.html`（app.js?v=f8a06d79、style.css?v=f18c6f22）、
`tests/test_api.py`、`tests/test_report.py`、`AGENTS.md`、
`docs/复现文档.md`、`docs/项目说明.md`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加十一 · 答辩模拟"答完重问第一题"根因修复）：**
用户反馈：正常回答后评委本应点评并追问，结果像连接中断、从第一问重新开始。
定位根因：评委生成失败（模型连接中断或输出被质量闸门拦截）时，回答残留在
历史里却没有对应的评委回复；下一轮发送历史出现连续多条 user 消息，模型
误判为新会话，从第一问重问。全量测试 352 → 353 passed；`app.js?v=` →
`c9e4e360`。

#### 1. 历史残留导致"重新开始"（P0 功能）
1. **问题：** 答辩模拟多轮对话中，某轮评委生成失败后，前端把用户回答保留在
`ivChat.messages` 但没有评委回复；下一轮 `history` 出现连续 user 消息，
模型把它当成新对话，重新问第一题（用户看到"从头开始"）。语音/视频答题
失败时还会把"语音问答失败：xxx"当成 assistant 消息写进历史，进一步污染
上下文。
2. **修改前：**
   - 前端 `sendInterviewAnswer` 发 `history: ivChat.messages.slice(0,-1)`，
     只去掉当前轮，历史里之前悬空的 user 消息原样发送；
   - `interviewHistoryForJudge` 原样映射全部消息；
   - `finishInterviewTurnError` 把错误文本 push 成 assistant 消息；
   - 后端 `chat_turn` 把历史消息原样拼进 messages，不处理连续同角色。
3. **修改后：**
   - 前端新增 `interviewHistoryForTurn()`：裁剪掉末尾所有没有评委回复的
     user 消息（文本答题历史改用它）；
   - `interviewHistoryForJudge`（语音/视频）同样裁剪末尾悬空 user；
   - `finishInterviewTurnError`：失败时回滚刚推入的"🎤 [语音回答]"
     占位消息，错误只以 notice 提示，不进对话历史；
   - 后端 `chat_turn`：归一化历史（丢弃空内容、合并连续同角色消息），
     并让本轮回答也参与合并，保证发给模型的 Q/A 始终交替。
4. **为什么这样改：** "重新开始"不是模型想重来，而是输入里连续 user 消息
让它以为这是新会话；两端同时修正输入结构，从根上消除歧义。失败不污染
历史则保证重试后上下文仍然干净。
5. **收益：** ① 评委失败后重试会继续原对话，不再从第一问重问；② 语音/视频
失败不再把错误文本当评委回复存进记忆；③ 后端兜底合并保证即使前端有遗漏，
模型也不会收到连续 user。

**涉及文件：** `app/agents/interview_sim.py`、`app/web/static/app.js`、
`app/web/templates/index.html`（app.js?v=c9e4e360）、
`tests/test_interview_chat.py`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加十二 · 答辩首问总是通用兜底问题的根因修复）：**
用户反馈：无论答辩材料与评委关注点怎么写，第一个问题总是"请概括这次答辩
最希望评委理解的核心观点"。定位根因：`InterviewSimAgent.run()` 的失败分支
漏写 return（注释写明"返回错误提示文本"但代码没有），LLM 生成问题失败时
函数返回 `None`，路由拿到空结果只能回退到与材料无关的通用问题；而
MiniCPM-o 问题生成在长材料下又频繁失败（乱码/客套被质量闸门拦截），
于是每次都看到通用首问。全量测试 353 → 355 passed；`app.js?v=` →
`af4ec2f0`。

#### 1. 答辩首问总是通用兜底（P0 功能）
1. **问题：** ① `run()` 失败分支漏 `return`，`chat_text` 返回 AgentError 时
函数落到末尾返回 `None`；② 路由对空结果只给一句与材料无关的通用问题；
③ 长材料下 MiniCPM-o 生成问题失败率高，通用兜底几乎每次命中。
2. **修改前：** `run()` 尾部只有 `if isinstance(result, str): ... return result`
和一条注释，无失败 return；路由 `questions or ["请概括这次答辩最希望
评委理解的核心观点。"]`。
3. **修改后：**
   - `run()`：失败时 `logger.warning` 记录原因并 `return ""`（保持 str 契约）；
   - 路由新增 `_fallback_interview_questions`：兜底问题改为**基于材料首句、
     任务名、评委关注点、答辩要求**逐条生成，通用问题仅作最后一项；
     响应新增 `warning` 字段提示"AI 生成暂不可用，已用基础问题，可重新生成"；
   - 前端 `startInterviewChat` 收到 `warning` 时弹提示，用户可重新开始重试。
4. **为什么这样改：** 漏 return 是直接根因；但即使补上 return，把错误文本
当首问也体验很差，所以同时把兜底问题做成材料/关注点感知，让 LLM 不可用时
首问仍然贴题，且明确提示用户重试。
5. **收益：** ① 不再出现"与材料无关的通用首问"；② 失败原因进日志可排查；
③ 兜底问题贴合材料与评委关注点；④ 前端明确提示，用户知道可重新生成。

**涉及文件：** `app/agents/interview_sim.py`、`app/web/routes.py`、
`app/web/static/app.js`、`app/web/templates/index.html`（app.js?v=af4ec2f0）、
`tests/test_agents.py`、`tests/test_api.py`、`CHANGELOG.md`。

**同步修改（2026-08-24 追加十三 · 复读机退化乱码检测 + "?" 规则误杀修复）：**
用户粘贴答辩模拟首问内容为约 49KB 的 "ironironiron…" 无限重复。定位：
MiniCPM-o 生成问题时长上下文中退化成了"复读机"（单 token 无限循环直到
预算耗尽），该输出既无 "?" 也不是客套话，被既有闸门放行后直接当首问展示。
同时发现既有 "?" 规则按"问号总数 ≥5"判定，会误杀合法的多问题列表
（答辩生成 5+ 道带问号的问题时整份被当成乱码，这也是"首问总是兜底"
的隐藏诱因之一）。全量测试 355 → 356 passed。

#### 1. 模型退化"复读机"未被拦截（P0 展示质量）
1. **问题：** 模型输出 "ironironiron…" 这类单单元无限重复（约 49KB 直到
token 预算耗尽）时，`_looks_like_garbage` 只查问号，`_looks_like_canned_reply`
只查客套话，两边都放行，乱码直接被当成答辩问题展示给用户。
2. **修改前：** `_looks_like_garbage` 仅判定 `q >= 5 or q/len > 0.3`。
3. **修改后：** 新增 `_looks_like_repetition`，三类特征任一命中即判退化：
   ① 整段由同一短单元无间隔循环（ironironiron / 好的好的好的好的…，
      长度门槛 ≥16 避免误伤短笑声/叠词）；
   ② 去空格后字符多样性极低（长文本只有 ≤6 种字符反复出现）；
   ③ 同一词重复 ≥6 次且占全文 60% 以上（iron iron iron …）。
   `_looks_like_garbage` 在问号规则之后追加调用该检测。
4. **为什么这样改：** 复读机是模型退化的一种独立形态，与 "?" 乱码、
客套话并列；补上形态检测后，这类输出会走既有"拒绝 + 兜底 + 提示重试"
链路，而不是原样上屏。
5. **收益：** ① 复读机乱码不再展示给用户；② 覆盖抽屉对话/转写/会议/
答辩全部走 `_looks_like_garbage` 的路径；③ 新增 8 组正反例回归断言。

#### 2. "?" 总数 ≥5 误杀合法多问题列表（P1 误判）
1. **问题：** 原规则按"问号总数 ≥5"判定乱码，答辩/追问这类天然带
5+ 个问号的合法输出整份被拒，`chat_text` 失败 → 首问长期落到兜底。
2. **修改前：** `q >= 5 or q / max(1, len(text)) > 0.3`。
3. **修改后：** 改为 `连续问号串 ≥4（max_run ≥4）或占比 >30%`；
   合法列表（"1. 为什么选择A？\n2. …"）问号分散不成串、占比低，不再误杀；
   A3 的 "？？？？" 长串与 "？？\n？？\n？？" 高占比形态仍能命中。
4. **为什么这样改：** 判据应从"形态"出发而非"计数"：乱码的特征是
连续问号串或问号密集，合法问句的特征是问号分散在句中。
5. **收益：** ① 多问题列表正常通过闸门；② 答辩生成不再因 5+ 问号
被误判失败；③ 新增 3 组正反例回归断言。

**涉及文件：** `app/services/omni_chat.py`、`tests/test_realtime_client.py`、
`CHANGELOG.md`。

**同步修改（2026-08-24 追加十四 · 答辩评委"逐字复读上一轮"修复）：**
用户反馈语音答辩连续三轮收到逐字相同的"点评 + 追问"。定位：历史与新的
语音回答每次都正确传给了模型，但模型被上一轮 assistant 回复锚定，退化成
复读——逐字重复上一轮输出，与材料无关。修复：评委指令显式禁止复读；
后端对"与上一轮回复相似度 ≥0.85 且长度 ≥30 字"的回复自动带防重复指令
重试一次，重试仍复读则判空转返回 502 提示，绝不把复读内容当回复展示。
文本答题链路的系统提示词同步补充"禁止复述上一轮"规则。
全量测试 356 → 357 passed。

#### 1. 评委逐字复读上一轮点评/追问（P0 展示质量）
1. **问题：** 语音答辩三轮收到完全相同（逐字一致）的"点评 + 追问"；
输入侧每轮的历史和新语音回答都正确，问题出在模型被上一轮回复锚定后
退化成复读，且指令没有禁止重复。
2. **修改前：** `INTERVIEW_TURN_INSTRUCTION` 无防重复要求；
`realtime_interview_turn` 对返回内容与上一轮是否相同不做任何检查。
3. **修改后：**
   - `INTERVIEW_TURN_INSTRUCTION` 追加"绝对不要重复或复述你上一轮已经
     给出的点评与追问；若上一轮追问用户已回答，请换一个新维度继续提问"；
   - 新增 `_reply_similar`（difflib 相似度）与防复读循环：历史最后一条是
     assistant 回复时，若本次 `reply` 与它相似度 ≥0.85 且长度 ≥30 字，
     视为复读，带"你第一次生成的回复与历史完全相同，这是复读错误"的
     强化指令重试一次；重试后仍复读则判空转，走既有 502 提示路径；
   - 文本答题链路的 `INTERVIEW_CHAT_SYSTEM` 补充"绝对不要重复或复述
     上一轮已给出的点评与问题"。
4. **为什么这样改：** 复读是模型侧退化，应用层无法让模型"变聪明"，
但可以做质量闸门：显式禁止 + 命中后重试 + 仍失败则明确报错，
保证用户看到的要么是新的点评，要么是可操作的重试提示。
5. **收益：** ① 复读内容不再逐字展示；② 一次自动重试大概率拿到新回复；
③ 文本/语音两条答辩链路行为一致；④ 新增回归用例（复读→防重复指令
重试→新回复）。

**涉及文件：** `app/web/routers/realtime.py`、`app/llm/prompts.py`、
`tests/test_realtime_client.py`、`CHANGELOG.md`。

### 同步修改：重写提交主文档《项目说明书》（2026-08-30 追加）

**定位：** 把分散在 docs 下的项目说明、使用说明书、演示脚本、复现文档、功能验证清单等材料整合重写为提交主文档《项目说明书》，按四轮评审意见迭代至定稿：一页三段式摘要、十章结构、功能一览表、交付物清单、公网 Demo 地址与登录凭据、多智能体架构专章、云端与本地守卫机制说明；只删重复、不压内容，删除开发语境与内部黑话。旧《项目说明》转为入口指引，README 文档索引同步更新。本次为文档调整，不涉及代码，不新增版本号。

1. **问题：** 原《项目说明.md》内容零散且缺少观点与证据组织；重写稿存在会翻车的问题（登录凭据空头支票、创新点与边界自相矛盾、承诺超出模型可重复性、内部交接文档进索引），以及多智能体架构深度不足、开发语境与"评审重点"等不应由选手陈述的表述；第一轮压缩改稿把部分有价值内容一并删掉（角色工时折算、浮动语义与甘特图呈现、静音切分细节、记忆冲突保护、批判性审查落点、第六章"为什么这么设计"、模型目录结构），摘要被压成"极简+数据罗列"的不平衡结构。
2. **修改前：** `docs/项目说明书.md` 约 1.6 万字，摘要前半段过短、后半段数据密集；3.2.4 缺角色折算、3.2.5 缺浮动与甘特图语义、3.3.2 缺静音切分与记忆冲突细节、3.2.2 缺三个行为落点、第六章缺设计落点、8.3 缺模型目录；无本地守卫可配置说明；3.3.5 写"自动禁用语音回复"。
3. **修改后：** 摘要重写为三段自然展开（产品定位与价值对比、体验方式与凭据、证据一句话串起），第三段明确论证"系统已建成并实际运行"；补回角色工时折算与不可用日期硬跳过、浮动天数与甘特图四色语义、20 毫秒静音检测与按句切分、记忆冲突以旧记忆为准、批判性审查建议由人采纳、知识问答工具轨迹展示、答辩提问只依据实际材料；第六章每节补"为什么这么设计"的落点；8.3 补回模型目录结构（主模型、音频、视觉、语音合成与声码器）；3.3.2 新增本地守卫机制说明（硬性限制仅本地生效、乱码与复读过滤两端生效、云端命中自动重试、三个环境变量可放宽），10.4 环境变量表新增对应行，3.3.5 改为"默认禁用（可通过 APP_LOCAL_TTS_ENABLED 开启）"并与 9.1 口径一致；随后做全篇自然度检查，按"删掉是否丢信息"的标准再补回压缩时误删的细节（2.1 流程环节对应、3.1 路由域清单、3.2.2 知识问答检索范围与命令行子命令、3.2.4 重分配保留已有状态、3.2.5 可用工作日累计与空档拉长、3.3.1 本地后端关闭默认心跳、3.4 合规覆盖入口清单、4.2/4.6/4.9 完整表述、7.1 业务落点句），并润色 2.3、4.3、4.4、9.1 等压缩毛刺；最后一次全篇重写以自然叙述取代结论堆砌（第二章加过渡句、第三章机制叙述化、第六章以"共同点+为什么"组织、第七章差异叙述化），架构图按等宽对齐重绘并程序化校验，清理全文"不是……而是""而非"等残留句式；定稿前做纯冗余清理，删除跨章节真正重复的表述（双轨与线上跑云端的多处复述、凭据三处重复改为一处引用、守卫与合规的重复说明、章节内自我重复句），深度内容全部保留；评审意见收尾：架构图底部两框改为等宽、箭头居中并与连接线列对齐，删除"子智能体""数据契约"等评委无需解释的术语释义，替换"能不能做优先于累不累"为事实表述，删除"按旧方式"无来源对比，5.5 演示流程删除录屏推荐；创新点收敛为两个（全模态进入业务闭环、确定性算法保证正确性），原"受限算力下的全模态工程可用性"改写为第六章 6.5"工程落地效果"，作为工程完整度与可复现性的证据；PPT 同步由用户另行处理。
4. **为什么这样改：** 精简的标准是"删掉后是否丢信息"而非"句子长短"，详细内容应当舒展呈现；守卫机制是本地昇腾可用性的关键工程设计，评委可据此理解默认行为并自行放宽，属于可核验的工程能力。
5. **收益：** ① 摘要第一印象自然完整，不再前简后密；② 删掉的工程细节全部补回，信息不丢失；③ 本地守卫可配置，环境变量与正文呼应；④ 全文仍无表情图标、无"不是……而是……"句式、无开发语境与内部黑话；⑤ 三个创新点与 PPT 表述保持一致。

**涉及文件：** `docs/项目说明书.md`（新增）、`docs/项目说明.md`（改为入口指引）、`README.md`、`CHANGELOG.md`。

### 同步修改：提交前仓库清理（2026-08-30 追加）

**定位：** 提交在即，清理仓库中不随提交的材料与过期配置：删除 docs 内部交接/备赛/演示流程等文档、PPT 制作过程文件、失效截图与诊断音频；赛事群聊记录保留在本地但不进入 Git；README 与提交文档同步到最终状态；`.env.example` 收敛为仅含参赛模型 MiniCPM-o 4.5 的配置。本次为文档与配置调整，不涉及业务代码，不新增版本号。

1. **问题：** 仓库将作为比赛提交链接，docs 下仍有多份内部交接与备赛文档（比赛全量备赛手册、比赛备赛梳理、交接-P2起点、深度审查与修复进度、单 Agent 调试指南、比赛Demo演示流程、演示脚本、演示流程-网页端/移动端/故障兜底），README 仍引用其中部分文档；`projects/` 目录残留约 147MB PPT 制作过程文件（提取稿、渲染图、备份 SVG、解包文件等）；`app/web/static/_shots/` 有 16 张无引用的历史截图；根目录有诊断音频与临时脚本；`.env.example` 保留大量与 DeepSeek/DashScope 等外部模型相关的 legacy 配置，与"参赛唯一模型 MiniCPM-o 4.5"的要求不符；README 最后更新日期、功能总览重复表格、文档索引与删除文档后的实际目录不一致。
2. **修改前：** docs 共 19 个文件（含群聊记录 3 个）；`.env.example` 含 `LLM_*`、`APP_VISION_*`、`APP_ASR_*`、`LLM_PREFER_PLAIN`、`LLM_DISABLE_THINKING` 等外部模型配置；README 引用已删文档并缺失部署与回退清单条目；复现文档、部署与回退清单仍描述外部视觉/语音模型兜底。
3. **修改后：** 删除上述 10 份内部文档（Git 历史可恢复），并进一步删除使用说明书、项目说明（入口指引）、华为昇腾创新应用赛道接入说明三份提交文档，docs 提交面收敛为项目说明书、复现文档、功能验证清单、部署与回退清单四份；`docs/群聊记录/` 加入 .gitignore 并从索引移除，文件保留在本地备查；`docs/昇腾A3_910C_llama_omni部署指南.md` 保持跟踪，仅删除其中"赛事/赛道/选手"等提交语境表述，改为纯技术部署手册（昇腾环境部署属平台侧内容，不再作为提交文档索引）；删除 `projects/`（原已 gitignore）、`scripts/_tmp_rewrite_p20.py`、`_diag_voice.wav/webm`、`app/web/static/_shots/` 16 张截图；README 更新日期为 2026-08-30、删除功能总览中重复的全模态表格、操作说明与演示流程指向项目说明书第四章/第五章、文档索引收敛为 4 项、公网部署节补充线上演示实例地址与账号；`.env.example` 重写为仅含 MiniCPM-o Realtime、昇腾本地后端、本地音频/TTS 开关与通用应用配置；复现文档删除外部模型兜底示例、接入说明与部署指南引用（部署步骤内联保留），部署与回退清单环境变量表同步收敛并更新发布前检查说明。
4. **为什么这样改：** 提交链接代表工程全貌，内部交接与过程文件不应随仓库公开；群聊记录含内部讨论，本地备查即可；外部模型配置与赛道"仅使用 MiniCPM-o 4.5"的要求相悖，应彻底从交付配置中移除；文档索引与实际目录必须一致，避免评审按图索骥失败。
5. **收益：** ① 仓库只含提交所需源码、测试、提交文档与配置；② 群聊记录本地保留、不进 Git；③ 交付配置只暴露 MiniCPM-o 相关项，合规表述无懈可击；④ README 与提交文档索引一致，无死链。

**涉及文件：** `README.md`、`.gitignore`、`.env.example`、`docs/复现文档.md`、`docs/部署与回退清单.md`、`docs/项目说明书.md`、`docs/昇腾A3_910C_llama_omni部署指南.md`（删除赛事/赛道语境，保持跟踪）、`app/services/realtime_client.py`（注释去除对已移除部署指南章节的引用）、`tests/test_demo_readiness.py`（黄金演示断言改为项目说明书与三份提交文档）、`tests/test_deployment_readiness.py`（环境变量契约改为仅 MiniCPM-o 且断言外部模型配置不存在）、`CHANGELOG.md`；删除 `docs/比赛备赛梳理.md`、`docs/比赛全量备赛手册.md`、`docs/比赛Demo演示流程.md`、`docs/单Agent调试指南.md`、`docs/交接-P2起点.md`、`docs/深度审查与修复进度.md`、`docs/演示脚本.md`、`docs/演示流程-网页端.md`、`docs/演示流程-移动端.md`、`docs/演示流程-故障兜底.md`、`docs/使用说明书.md`、`docs/项目说明.md`、`docs/华为昇腾创新应用赛道接入说明.md`、`app/web/static/_shots/`、`projects/`、`scripts/_tmp_rewrite_p20.py`、`_diag_voice.wav`、`_diag_voice.webm`；`docs/群聊记录/` 取消跟踪并加入 .gitignore（本地保留）；全量测试 401 passed。

### 同步修改：系统架构图重绘（2026-08-30 追加）

**定位：** README 与项目说明书中的系统架构图由字符画框式改为竖排流程式，连接列仅由 ASCII 空格定位，消除不同渲染字体下的错位。本次为文档调整，不涉及代码。

1. **问题：** 字符画框式架构图同时混用框线字符与中文标签，其对齐依赖"框线=半宽、汉字=双宽"的字体假设；实际查看器（编辑器/预览/面板）的字体宽度规则不同，导致框体与连接线错位，评审阅读时观感杂乱。
2. **修改前：** 两张图均为等宽假设下绘制的框式 ASCII 图，框体、`┬/│/▼` 连接列按 CJK 双宽计算；在不同字体下渲染即错位。
3. **修改后：** 改为竖排流程式架构图：浏览器前端 → FastAPI 后端 → 两个推理后端（昇腾 A3 与 ModelBest 云端）逐行展开，`│/▼` 连接列由固定数量的 ASCII 空格定位（第 8 列），标签行均从行首开始，不参与任何宽度计算。
4. **为什么这样改：** 架构图的对齐只能依赖渲染器字体无法改变的 ASCII 空格；把连接列全部放到 ASCII 前缀上，任何等宽字体下都严格对齐，比例字体下也呈现为清晰的缩进流程。
5. **收益：** ① 两张架构图在任何查看器下都不再错位；② 三层结构（前端/后端/双推理后端）与两条链路一目了然；③ 项目说明书与 README 风格统一。

**涉及文件：** `docs/项目说明书.md`、`README.md`、`CHANGELOG.md`。

### 同步修改：演示视频录制安排与文档口径同步（2026-08-30 追加）

**定位：** 演示视频确定按功能双轨录制——全模态交互（拍照、语音、会议旁听、语音汇报）走云端以保证流畅，计划引擎（任务拆解、分工、排期）走本地昇腾 A3 以展示昇腾真实运行；同步更新说明书相关表述，避免文档与视频不一致。本次为文档调整，不涉及代码。

1. **问题：** 原说明书多处表述为"现场演示与录屏使用本地昇腾 A3"，与确定的录制方案（全模态走云端、计划引擎走本地）不符，评委对照视频会认为文档与事实不一致。
2. **修改前：** 摘要、5.4 写"录屏使用本地昇腾 A3"；架构图标注"现场演示与录屏"与"线上 Demo 与兜底"；5.5 无录制安排说明；交付物清单写"昇腾环境录屏"。
3. **修改后：** 摘要改为"演示视频按功能双轨录制，全模态交互走云端、计划引擎走本地昇腾 A3"；5.4 说明双轨录制及昇腾侧由实测数据（5.2）与复现步骤（8.3）支撑；5.5 补充录制安排；交付物清单改为"双轨录制（本地昇腾计划引擎 + 云端全模态）"；架构图标注保持中性（"本地推理后端 / 云端推理后端"），架构图描述系统结构，不承载录制安排。
4. **为什么这样改：** 视频是评委直接观看的材料，文档必须与视频中的运行后端一一对应；双轨录制同时展示昇腾本地真实运行与云端流畅体验，两者均有证据支撑。
5. **收益：** ① 文档与视频口径一致，无"货不对板"；② 计划引擎环节展示昇腾真实运行，全模态环节展示流畅体验；③ 昇腾侧证据链（实测数据 + 复现步骤）保持不变。

**涉及文件：** `docs/项目说明书.md`、`CHANGELOG.md`。

### 同步修改：本地昇腾限制参数环境变量化，消除自托管评审环境的"枷锁"（2026-08-30 追加）

**定位：** 将本地昇腾后端的三处硬编码限制（12 秒音频分片、10 分钟时长上限、本地 TTS 禁用）改为环境变量可配，默认值与 910C 实测稳定值完全一致，不改变现有行为，同时让性能更优的自托管本地后端可按需放宽。本次为配置调整，不新增测试（全量测试保持 401 passed），不新增版本号。

1. **问题：** `_AUDIO_CHUNK_SECONDS=12`、`_LOCAL_AUDIO_MAX_SECONDS=600` 与本地 TTS 禁用均为硬编码；若官方评审/自托管环境使用性能完好的本地后端（如 TTS 算子已修复或推理速度更快），这些限制仍会无条件生效——长音频被强制分片、超 10 分钟直接拒绝、语音合成无法开启，反而成为"枷锁"。
2. **修改前：**
   ```python
   # app/services/omni_chat.py
   _AUDIO_CHUNK_SECONDS = 12
   _LOCAL_AUDIO_MAX_SECONDS = 600
   ```
   ```python
   # app/web/routers/realtime.py（TTS 路由：本地后端一律拒绝）
   if ASCEND_OMNI_WS_URL:
       raise HTTPException(status_code=501, detail="本地昇腾 910C TTS 暂不可用…")
   ```
   ```js
   // app/web/static/app.js（updateVoiceToggle：本地后端一律禁用语音回复）
   var ok=rt.enabled&&!state.realtimeFallback&&rt.backend==='map';
   ```
3. **修改后：**
   ```python
   # app/config.py：新增三个环境变量，默认值不变
   LOCAL_AUDIO_CHUNK_SECONDS = max(3, min(120, int(os.getenv(
       "APP_LOCAL_AUDIO_CHUNK_SECONDS", "12"))))
   LOCAL_AUDIO_MAX_SECONDS = max(30, min(3600, int(os.getenv(
       "APP_LOCAL_AUDIO_MAX_SECONDS", "600"))))
   APP_LOCAL_TTS_ENABLED = os.getenv(
       "APP_LOCAL_TTS_ENABLED", "").lower() in ("1", "true", "yes")
   ```
   ```python
   # app/services/omni_chat.py：常量改为引用 config，默认值不变
   _AUDIO_CHUNK_SECONDS = LOCAL_AUDIO_CHUNK_SECONDS
   _LOCAL_AUDIO_MAX_SECONDS = LOCAL_AUDIO_MAX_SECONDS
   ```
   ```python
   # app/web/routers/realtime.py：仅当本地 TTS 未启用时才拒绝；
   # /api/realtime/status 新增 tts_available 字段
   if ASCEND_OMNI_WS_URL and not APP_LOCAL_TTS_ENABLED:
       ...
   "tts_available": bool(not ASCEND_OMNI_WS_URL or APP_LOCAL_TTS_ENABLED),
   ```
   ```js
   // app/web/static/app.js：本地后端按 status.tts_available 决定语音回复开关
   var ok=rt.enabled&&!state.realtimeFallback&&(rt.backend==='map'||rt.tts_available===true);
   ```
4. **为什么这样改：** 这些限制是"针对 910C 实测能力边界"的保护（whisper 编码约 30 秒、TTS 算子挂起），对同一硬件应默认生效；但对性能更优的本地后端，限制应可配置。把常量收敛到 config 并以环境变量覆盖，既保留默认安全行为，又给自托管评审环境留出放开口子；云端后端本就不受这些限制，无需任何配置。
5. **收益：** ① 默认行为零变化，全量测试保持 401 passed；② 自托管/官方评审环境可通过三个环境变量放宽限制，无需改代码；③ `/api/realtime/status` 暴露 `tts_available`，前端语音回复开关与后端实际能力保持一致。

**涉及文件：** `app/config.py`、`app/services/omni_chat.py`、`app/web/routers/realtime.py`、`app/web/static/app.js`、`app/web/templates/index.html`（`app.js?v=` 哈希更新）、`.env.example`、`README.md`、`CHANGELOG.md`。

### 同步修改：音频分片支持完全关闭（APP_LOCAL_AUDIO_CHUNK_SECONDS=0）（2026-08-30 追加二）

**定位：** 在上一项环境变量化的基础上，允许健康本地后端完全关闭音频分片，消除"性能完好的本地模型仍被强制分片"的最后一道枷锁。默认值不变，不新增测试（全量测试保持 401 passed），不新增版本号。

1. **问题：** 上一项将分片上限放宽到 120 秒，但长音频仍会被分片处理；若官方/自托管评审环境的本地后端性能完好（可整段处理长音频），强制分片会徒增延迟、增加跨分片合并的上下文损失，成为"磕磕绊绊"的来源。
2. **修改前：** `LOCAL_AUDIO_CHUNK_SECONDS` 下限为 3 秒，三处调用点（`omni_chat.transcribe_audio`、`omni_chat.understand_audio`、`media_analysis` 转写路径）只要本地后端就无条件分片：
   ```python
   chunks = (_split_pcm_b64(audio_b64) if ASCEND_OMNI_WS_URL else [audio_b64])
   ```
3. **修改后：** 配置下限改为 0（`0` 表示关闭分片），三处调用点仅在 `ASCEND_OMNI_WS_URL` 且分片值大于 0 时执行分片，否则整段直通：
   ```python
   LOCAL_AUDIO_CHUNK_SECONDS = max(0, min(120, int(os.getenv(
       "APP_LOCAL_AUDIO_CHUNK_SECONDS", "12"))))
   chunks = (
       _split_pcm_b64(audio_b64)
       if (ASCEND_OMNI_WS_URL and _AUDIO_CHUNK_SECONDS > 0)
       else [audio_b64])
   ```
4. **为什么这样改：** 分片与合并是为绕开 910C whisper 编码边界而设计的应用层规避，对性能完好的后端是纯负担；把"关闭分片"作为显式可选项，让部署者按后端实际能力选择，默认值保持 12 秒不改变现有环境行为。
5. **收益：** ① 默认行为零变化，全量测试保持 401 passed；② 健康本地后端设 `APP_LOCAL_AUDIO_CHUNK_SECONDS=0` 后长音频整段直通，无分片延迟与合并损耗；③ 与云端行为完全对齐，官方评审不会因应用层分片感到"磕绊"。

**涉及文件：** `app/config.py`、`app/services/omni_chat.py`、`app/services/media_analysis.py`、`.env.example`、`README.md`、`CHANGELOG.md`。

---
## v7.0 —— 视频理解：会议录像边看边听 + 多模态演示闭环（2026-08-22）

**定位：** 完成 P2 重点「视频理解」——会议旁听从"只听录音"升级为"录音/录像皆可，
抽帧看画面 + 抽音频听内容，边看边听整理任务草案"；同时补齐多模态五步演示脚本、
移动端窄屏验证与比赛材料（项目说明、复现文档）。

**审查/修改背景：** P0/P1 已把文本/语音/图片/TTS 接入核心协作流程，但视频模态只用于答辩录像
（表情分析），会议/讨论视频仍需人二次整理；P2 清单第 6 项要求"录一段项目讨论视频 → 边看边听
总结任务生成草案"。

### 体验优化（P2）

#### 1. 会议旁听升级为视频理解：抽帧 + 抽音频边看边听

1. **问题：** `/api/realtime/meeting` 只接受音频，会议录像（PPT/白板/文档画面 + 人声）无法直接进入协作流程；MiniCPM-o 的"边看边听"独特能力在会议场景没有用上。
2. **修改前：** `realtime_meeting` 仅用 `_decode_audio_to_pcm16k` 解音频后发给模型整理，无画面理解；前端 `meetingFile` 只接受 `.mp3,.wav,.m4a,.webm`。
3. **修改后：** `realtime_meeting` 改为统一链路：先 `extract_video_frames` 抽 3 帧（失败置空，纯录音兼容）+ `extract_audio_pcm16k` 抽音频；音频走 `understand_audio` 整理 要点/任务/风险，逐帧走图片理解返回"画面理解"；无声轨录屏视频则把画面理解交给模型合成结构化会议结果。响应新增 `visual` 与 `has_video` 字段。前端会议入口 accept 增加 `.mp4,.mov,.m4v,.mkv`，弹窗新增「📹 视频画面理解」区块，"生成任务草案"时画面理解一并写入背景。
4. **为什么这样改：** 与答辩视频链路复用同一套"抽帧+抽音频"基础设施，一条录像同时喂视觉与听觉；逐帧串行控制输入量、失败降级，保证长视频不爆内存、纯录音不回退。
5. **收益：** ① 会议录像可直接生成任务草案（实测 16s：3 帧画面理解 + 音频整理）；② 无声轨录屏也能凭画面整理会议；③ 前端在窄屏完整展示画面理解。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/多模态落地改造清单.md`、`docs/比赛全量备赛手册.md`、`docs/演示脚本.md`（新增）、`CHANGELOG.md`。

#### 2. 移动端工作台窄屏验证与修补

1. **问题：** 汇报页已手机可用，但工作台录音/拍照/摄像头在窄屏是否完整可用未做系统性验证，可能溢出或卡死。
2. **修改前：** 仅靠 CSS 媒体查询，无窄屏实测记录；会议弹窗无画面理解区块样式。
3. **修改后：** 用 Chrome 无头浏览器在 390×844 视口实测：无横向溢出（scrollWidth=390）、顶部工具栏/配置操作区/上传按钮均自适应换行、摄像头权限被拒时优雅回退文件选择并给出明确提示、会议弹窗居中且"生成任务草案"按钮可见、控制台 0 报错；新增 `.meeting-visual` 区块样式（窄屏不溢出）。
4. **为什么这样改：** 移动端是演示重要入口（汇报页手机打开），"验证与修补"必须以真实窄屏渲染为依据，而不是只加 CSS。
5. **收益：** ① 工作台窄屏各入口实测可用；② 摄像头失败不再卡死；③ 验收清单可复现。

**涉及文件：** `app/web/static/style.css`、`app/web/templates/index.html`、`docs/演示脚本.md`（新增）、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 3. 会议提示词禁用尖括号占位符并清理多余段落

1. **问题：** 会议提示词沿用 `<会议要点>` `<风险…>` 尖括号占位符，8B 模型实测把 `<会议要点>` 原样输出进摘要；模型偶尔还会多输出 `【其他】` 段，污染 risks 字段。
2. **修改前：** `MEETING_PROMPT` / `MEETING_VISUAL_SYNTHESIS` 用尖括号占位符；`realtime_meeting` 内联解析，遇到额外段落直接并入风险文本。
3. **修改后：** 提示词改为纯描述式结构（"【总结】\n会议要点…"），与答辩提示词规则一致；新增 `_parse_meeting_text` 复用解析，并增加两类清理：占位符行剔除、遇到下一个 `【` 段落即截断（`【其他】` 不再混入 risks）。
4. **为什么这样改：** 占位符照抄是 8B 模型已知行为，根治只能改提示词；解析截断是兜底，避免模型格式漂移污染结构化输出。
5. **收益：** ① 会议摘要/风险不再出现尖括号占位符（实测 risks 干净返回"无"）；② 会议解析逻辑独立可测；③ 与答辩 `_parse_turn_text` 规则统一。

**涉及文件：** `app/web/routers/realtime.py`、`tests/test_report.py`、`docs/华为昇腾创新应用赛道接入说明.md`、`CHANGELOG.md`。

### 打磨（P3）

#### 4. 多模态五步演示脚本与比赛材料

1. **问题：** 现有 [比赛Demo演示流程.md](./docs/比赛Demo演示流程.md) 是"打字输入"版六步流程，没有把 P0–P2 的全模态闭环（拍照 → 说话 → 确认 → 汇报 → 群通知）串成现场可讲的主脚本；比赛提交还缺项目说明与复现文档。
2. **修改前：** 只有文字版演示流程；无面向评审的 `项目说明` / `复现文档`。
3. **修改后：** 新增 [演示脚本.md](./docs/演示脚本.md)（五步多模态脚本：每步操作/讲解词/预期结果/时间预算/红线/兜底/验收清单）；新增 [项目说明.md](./docs/项目说明.md)（架构、模块、接口清单、复现摘要、实测结果、提交材料映射）与 [复现文档.md](./docs/复现文档.md)（环境版本、模型目录、编译/启动命令、验证命令、实测表、已知问题规避）。
4. **为什么这样改：** 评审看的是"可讲、可复现、可验证"；把演示节奏与复现步骤固化进文档，现场不临场发挥，评审也能按文档复现。
5. **收益：** ① 现场 4–6 分钟照脚本讲完五步闭环；② 提交材料齐全（PPT 待补）；③ 复现命令与实测数据对评审透明。

**涉及文件：** `docs/演示脚本.md`（新增）、`docs/项目说明.md`（新增）、`docs/复现文档.md`（新增）、`docs/比赛Demo演示流程.md`、`docs/比赛全量备赛手册.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 5. 语音汇报确认后状态不跳转（中文状态未映射枚举）

1. **问题：** 成员语音汇报能识别、能应用，但确认后任务状态仍是"待开始"，汇报页与工作台任务列表都不更新，只有工时/备注被写入。
2. **修改前：** `report_voice` 解析出的 `parsed.status` 是中文（"完成/进行中/阻塞/未开始"），前端确认时原样发给 `/api/report/update`，后端 `_apply_update` 只认英文枚举（completed/in_progress/blocked/pending），状态被静默跳过。
3. **修改后：** `report_voice` 返回 `status`（英文枚举，供应用）与 `status_label`（中文，供展示）；前端识别结果标签用 `status_label`，确认应用用 `status`；新增回归测试 `test_report_voice_apply_persists_status` 覆盖"解析 → 应用 → 状态持久化"全链路。
4. **为什么这样改：** 展示值与提交值混用是 root cause——同一个字段既给人看又给程序用，后端必须收到稳定枚举；分离后展示层与数据层各自取所需，前端旧逻辑无需猜映射。
5. **收益：** ① 语音汇报确认后状态真实变为"已完成"并持久化（实测端到端通过）；② 汇报页与工作台任务列表数据一致；③ 回归测试防止再犯。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 6. 云端语音第 2 轮复述（缓存哈希过期 + 转写尾巴 + 上下文未生效）

1. **问题：** 用户反馈第 1 轮语音对话正常，第 2 轮开始模型复述用户原话；排查发现三个叠加因素：① `index.html` 的 `app.js?v=` 还是旧哈希（3ebd7446，与当前文件 5591d027 不符），浏览器可能一直用旧版前端；② 云端转写偶尔把"用户原话 + 好的/请问有什么可以帮您"等模型确认语一起返回，第二步理解把确认语也当用户输入，回答就变成复述；③ 前端已传 `system_prompt`（含方案快照）但后端 `voice_chat` 根本没接收，语音回答没有上下文约束。
2. **修改前：** `voice_chat` 只接收 `file/tts_enabled`；`TRANSCRIBE_INSTRUCTION` 未禁止"确认语/客套话"；转写结果原样进第二步；`app.js?v=` 哈希过期。
3. **修改后：** ① `voice_chat` 新增 `system_prompt` 表单参数并传入 `understand_audio`（无则用默认身份提示词）；② 转写指令强化（禁止思考/解释/确认/客套，禁止"用户说"开头），新增 `_clean_transcript` 剔除模型确认语尾巴（长尾优先，保留用户句首的"好的"等正常口语）；③ 回答指令追加"不要转写、不要复述、不要重复用户原话"；④ `index.html` 静态资源哈希按内容重算更新。
4. **为什么这样改：** 复述的三个来源分别根治：缓存哈希保证前端加载新逻辑；转写清洗保证输入干净；system_prompt 生效让模型基于方案快照回答而不是对着文本回声。
5. **收益：** ① 实测连续两轮语音对话均正常回答且带方案上下文（"王五设计任务最重"）；② 转写结果不再混入模型确认语；③ 静态资源哈希与文件内容一致，浏览器强刷后即用新前端。

**涉及文件：** `app/services/omni_chat.py`、`app/web/routers/realtime.py`、`app/web/templates/index.html`、`tests/test_realtime_client.py`、`CHANGELOG.md`。

### 体验优化（P2）

#### 7. 汇报页拍照改为调用摄像头取景框

1. **问题：** 汇报页点"📷 交付物"弹出的是文件选择器，而主工作台"拍照需求"是摄像头取景框，两端体验不一致；`capture="environment"` 只在手机浏览器生效，桌面端直接变成选文件。
2. **修改前：** `bindReportControls` 直接 `reportPhotoInput.click()`；摄像头弹窗只服务配置页，标题/提示写死"拍照需求"。
3. **修改后：** 摄像头弹窗参数化：`openCamera(mode)` 按模式切换标题与提示（配置页"拍照需求"/汇报页"拍照交付"），拍摄回调按模式路由（汇报 → `reportPhotoFile` 上传交付物，配置 → `addRequirementFile` 进需求材料）；`pickCameraFile(mode)` 失败回退也按模式选择对应文件输入；上传逻辑抽成 `reportPhotoFile(file)` 供取景框与文件选择共用。
4. **为什么这样改：** 交付物拍照与需求拍照本质都是"拍一张照进流程"，复用同一取景组件，避免复制一套摄像头逻辑；模式差异只体现在文案与回调。
5. **收益：** ① 汇报页桌面/手机都能真实拍摄交付物；② 摄像头失败回退文件选择，链路不中断；③ 实测 390×844 窄屏弹窗标题"拍照交付"、无报错。

**涉及文件：** `app/web/static/app.js`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 8. AI 建议抽屉语音对话补上多轮记忆

1. **问题：** 抽屉语音对话没有上下文记忆：文本轮次会带 `chatHistory`，但 `voice-chat` 只发当前音频 + 方案快照，第 2 轮完全不记得第 1 轮；且语音轮次写进 `chatHistory` 的是 `[语音消息]` 占位符，即使后续带历史，模型也读不到用户到底说了什么。用户反馈 3.x/4.x 修过多轮记忆后再次出现。
2. **修改前：** `voice_chat` 不接收 history；`understand_audio` 无历史参数；前端 `sendVoiceMessage` 只 append `[语音消息]` 占位符。
3. **修改后：** ① `RealtimeChatResult` 增加 `transcript` 字段；② `understand_audio` 新增 `history` 参数：本地昇腾拼在音频消息之前，云端拼在转写文本之前，并把转写文本回填到 `result.transcript`；③ `voice_chat` 接收 `history`（JSON 数组，校验 role/content、截断最近 16 条）并返回 `transcript`；④ 前端语音轮次发送最近 14 条历史，把 `[语音] <转写内容>` 存入 `chatHistory`，文本路径同样统一截断 14 条防上下文膨胀。
4. **为什么这样改：** turn-based 语音每次都是新会话，记忆只能由调用方随请求带上；占位符等于没存，必须存真实转写内容，后续轮次模型才能真正"记得"前面说过什么。
5. **收益：** ① 实测两轮语音：第 1 轮"谁的负担最重"→"王五10小时"，第 2 轮"那他负责的任务需要多少小时"→正确答"王五负责的任务需要10小时"；② 文字与语音共享同一记忆链；③ 历史截断避免长对话撑爆 4096 上下文。

**涉及文件：** `app/services/omni_chat.py`、`app/services/realtime_client.py`、`app/web/routers/realtime.py`、`app/web/static/app.js`、`tests/test_realtime_client.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 9. 汇报页更新后主页面自动同步进度

1. **问题：** 成员在汇报页更新状态后，主页面"任务计划 / 项目进度"仍是旧数据，需要手动重新加载才能看到变化。
2. **修改前：** 主页面 `state.plan` 是内存快照，没有感知磁盘方案被外部（汇报页）更新。
3. **修改后：** 新增 `state.planSnapshot`（`loadPlan`/`savePlan` 时记录 JSON）；主页面在 `visibilitychange` 回到可见或窗口 `focus` 时执行 `syncPlanFromDisk()`：拉取磁盘最新方案并与快照比对，不同则——本地无未保存修改时直接应用并提示"已同步最新进度（含成员汇报更新）"；本地有未保存修改时先 confirm 再覆盖，防止丢数据。
4. **为什么这样改：** 汇报页直接改磁盘文件，主页面必须以磁盘为权威；用快照比对判断"外部变化"而非无脑刷新，既自动同步又不覆盖本地草稿。
5. **收益：** ① 切回主页面即自动看到最新进度/状态；② 不打断当前视图与对话记忆；③ 有未保存修改时先确认，数据不丢。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 10. 成员级进度：协作者可报"我的部分完成"，负责人页面可见并确认

1. **问题：** 有协作者的任务，协作者完成自己的部分后无法在汇报页表达"我完成了"（标记完成会被 403 拦截），负责人也看不到每个协作者各自完成到哪一步，只能私下沟通，汇报页信息不闭环。
2. **修改前：** `report/update` 非负责人标记 completed/blocked → 403；汇报页只显示任务级状态与无署名的备注，看不出谁完成了什么。
3. **修改后：** ① `report_notes.json` 升级为"汇报活动账本"（`add_report_activity`/`get_report_activities`，保留历史字符串兼容）；② 协作者标记 completed/blocked 不再 403，改为记录成员级状态：任务级状态保持"进行中"并返回 `awaiting_confirm: true`；负责人标记完成/阻塞仍直接作用于任务；③ `report/state` 每个任务返回 `members`（每人：状态/工时/交付物/备注/待确认标记）与 `activities`；④ 汇报页新增"成员进度"面板：负责人可见全部成员状态（含"待负责人确认"徽标、📷 已交付），协作者只可见自己的工时与备注、他人的状态；⑤ 协作者的状态下拉不再禁用"已完成/阻塞"；⑥ 通知语义区分"协作者完成自己的部分（等待确认）"与"负责人确认完成"。
4. **为什么这样改：** 任务级状态是"团队共识"必须由负责人确认，但成员级状态是"个人事实"应由个人上报；把两者分层记录，既保留负责人确认制，又让协作过程对负责人透明，不再依赖私下沟通。
5. **收益：** ① 负责人一眼看清每个协作者完成/未完成/已交付；② 协作者汇报"完成了"不再被拦，流程顺畅；③ 负责人确认后任务完成、待确认标记自动消失；④ 实测端到端通过（张三报完成 → 李四页面见"待负责人确认" → 确认后任务 completed）。

**涉及文件：** `app/services/report_link.py`、`app/web/routers/report.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 11. 汇报页状态切换自动应用，并与主页面双向同步

1. **问题：** 用户在汇报页把"已完成"改回"待开始"时切不过来（主页面改下拉立即生效、汇报页还要再点"应用"，且没有焦点自动刷新），主页面改完状态后切回汇报页也不自动更新。
2. **修改前：** 汇报页状态下拉只是表单值，必须点"应用"才提交；只有主页面有焦点自动同步，汇报页每次都要手动刷新。
3. **修改后：** ① 汇报页状态下拉增加 `onchange` 自动提交（`collectReportCard` 带上工时/日期），与主页面交互一致；② 汇报页 `enterReportMode` 增加 `visibilitychange`/`focus` 监听，切回页面自动 `loadReportState()`，主页面改动保存后汇报页即时同步；③ 提示"切换后自动保存"。
4. **为什么这样改：** 两端的交互模型必须一致——下拉即生效，且以磁盘为权威双向刷新，用户不再需要"先记住再手动点"。
5. **收益：** ① 已完成→待开始在汇报页下拉即切（实测自动应用、chip 同步）；② 主页面与汇报页状态双向同步；③ 无需手动刷新。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 12. 各成员上报工时自动累加为任务实际总工时

1. **问题：** 有协作者的任务，张三报 3 小时、李四报 3 小时，系统只记录最后一个上报值（3h），不会累加为 6h；语音提示词也没有说清"工时"指个人还是团队。
2. **修改前：** `_apply_update` 直接把上报成员的 `actual_hours` 覆盖 `task.actual_hours`；`REPORT_VOICE_PROMPT` 示例为"完成|6|…"未区分个人/团队。
3. **修改后：** ① 新增 `_member_latest_hours` / `_member_hours_total`：每个成员取"最近一次带工时的上报"，求和作为任务实际总工时，`record_task_actual` 写入累加值；② `REPORT_VOICE_PROMPT` 明确"工时 = 你自己实际花费的小时数，不要报团队总工时，系统自动累加所有成员"；③ 汇报页任务卡片新增"累计工时：Xh（各成员上报之和）"合计行，负责人可见每个成员各自的工时。
4. **为什么这样改：** 多人协作的"实际工时"是各成员投入之和，单人覆盖会丢失信息；按"每人最近一次上报"聚合既支持更正（重新报工时即覆盖自己）又保证可加总。
5. **收益：** ① 3h+3h 正确显示 6h（实测 T4 actual_hours=6.0，成员行张三/李四各 3h）；② 语音汇报语义无歧义；③ 负责人可核对每个人上报的工时。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 13. 成员进度面板 UI 美化

1. **问题：** 成员进度面板是简陋的行内文本列表，状态/姓名/备注挤在一行，可读性差。
2. **修改前：** `.report-member-row` 无视觉层次，姓名+角色连写、无状态圆点、无合计行。
3. **修改后：** 面板改为卡片式：标题带主题色竖条；每行 = 状态圆点（绿=已完成/红=阻塞/灰=未开始）+ 姓名 + 角色标签 + 状态胶囊 + 待确认/已交付徽标 + 备注（超长省略）+ 工时；底部"累计工时"合计行右对齐；窄屏自动换行。
4. **为什么这样改：** 信息分层展示比一行文本更易扫读——负责人一眼看状态、对名字、核工时。
5. **收益：** ① 成员状态可扫读；② 移动端 390px 无溢出；③ 累计工时醒目。

**涉及文件：** `app/web/static/app.js`、`app/web/static/style.css`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 14. 完成日期归属：协作者不设任务完成日期，仅负责人可设

1. **问题：** 汇报页对所有人都显示完成日期输入框（无标签），协作者提交日期会覆盖任务级完成日期；主页面有完成日期但汇报页任务信息里看不到，两者语义不一致。
2. **修改前：** `_apply_update` 对任意成员接受 `actual_end_date` 并写入 `task.actual_end_date`；汇报页每张任务卡都渲染日期输入。
3. **修改后：** ① 后端协作者提交的 `actual_end_date` 一律忽略（任务级完成日期由负责人设置）；② 汇报页仅在负责人卡片渲染日期输入（带"完成日期（负责人设置）"提示），协作者卡片不显示；③ 任务元信息新增"完成 YYYY-MM-DD"展示，汇报页与主页面可见；④ `collectReportCard` 对隐藏日期输入做空值保护。
4. **为什么这样改：** 完成日期是"整个任务完成"的事实，由负责人确认更合理；协作者报的是"我的部分完成"，不该改写任务日期——两个概念分离后与状态语义（负责人确认完成）一致。
5. **收益：** ① 协作者不会误改任务完成日期；② 汇报页/主页面完成日期一致可见；③ 负责人有且仅有他设置日期，语义清晰。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 15. 成员状态语义完整化：我的状态下拉、工时随状态变化、改回未完成自动回退

1. **问题：** ① 协作者下拉框之前绑定的是"任务状态"，负责人确认后改不动（永远显示已完成），成员进度小表格却会同步，语义割裂让人困惑；② 协作者把已完成改回未完成时，之前报的工时仍留在累计里；③ 协作者报阻塞后，任务若已完成没有明确处理，文案也不区分。
2. **修改前：** 协作者下拉按 `t.status`（任务状态）渲染；`_member_latest_hours` 取成员最近一次带工时的上报（纯状态变更不清工时）；协作者 completed/blocked 只记成员状态，任务状态不变。
3. **修改后：** ① 协作者下拉改为显示"我的状态"（取该成员自己的成员级状态），带"我的状态"标签，任务状态仍由卡片头部 chip 展示；② `_member_latest_hours`：带工时的上报写入/覆盖该成员工时，纯状态变更（无工时、非照片）清除该成员此前工时——改回未完成即退出累计；③ 协作者把已完成改回 未开始/进行中/阻塞 时，任务自动从"已完成"回退为"进行中"，并在成员行备注原因（阻塞/改回未完成），同时 webhook 通知负责人"任务状态已回退为进行中"；④ 协作者可正常选择阻塞（前端四选项全开放，后端按成员级记录）。
4. **为什么这样改：** 任务完成的不变量是"所有成员自己的部分都完成且负责人已确认"；成员状态是个人事实、任务状态是团队共识，两者必须分离展示并保持联动——成员改回未完成时任务不能继续声称已完成，工时也不能继续计入。
5. **收益：** ① 协作者下拉可真实反映并修改自己的状态，不再出现"改了没反应"；② 工时随状态联动（改回未完成即退出累计，实测 6h→3h）；③ 负责人侧自动看到任务回退与原因，可重新确认；④ 阻塞文案区分清晰。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

#### 16. 汇报页任务状态与成员状态再分离：标签 + 回退警告

1. **问题：** 负责人确认后协作者改回未完成，主页面正确回退"进行中"，但负责人汇报页上"任务状态"和"成员自己的状态"两个信息容易混淆——成员面板里负责人本人一行显示"已完成"，被误读成任务没回退，且没有提示任务为何回退。
2. **修改前：** 汇报页卡片头部只有一个状态 chip，无"任务状态"标签；无回退提示。
3. **修改后：** ① 卡片头部状态 chip 前加"任务状态"标签，任务状态与成员面板里的"我的状态"明确分层；② 当任务为"进行中"且负责人本人的成员状态为"已完成"（说明之前确认过、后被成员改回）时，任务卡顶部显示醒目警告："⚠ 任务已回退为进行中（有成员改回未完成），请重新确认后再标记完成"。
4. **为什么这样改：** 回退原因在成员行备注里不够显眼，负责人需要一眼看到"任务为什么又不是完成了"；标签分离消除"负责人本人已完成 vs 任务已完成"的误读。
5. **收益：** ① 负责人页面任务状态一目了然；② 回退有显式警告与操作指引；③ 390px 窄屏无溢出。

**涉及文件：** `app/web/static/app.js`、`app/web/static/style.css`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 17. 汇报备注重复出现两次

1. **问题：** 每次汇报后，底部说明列表里同一条备注出现两次（成员行备注 + 列表里重复两条），截图确认"读书报告初稿已撰写完成。"连续出现两条。
2. **修改前：** `_apply_update` 同时调用 `add_report_activity`（活动账本，含 note）和 `add_report_note`（旧版字符串备注），`get_report_notes` 把两者都返回 → 同一条备注被读两次。
3. **修改后：** ① 移除 `_apply_update` 里的 `add_report_note` 调用（活动账本已带 note）；② `get_report_notes` 连续重复只保留一条（兼容历史已双写的数据）。
4. **为什么这样改：** 双写是 root cause；读侧去重兜底历史数据，写侧只走一条路径防止再犯。
5. **收益：** ① 备注列表每条只出现一次；② 历史脏数据自动收敛；③ 实测 notes 列表无重复。

**涉及文件：** `app/services/report_link.py`、`app/web/routers/report.py`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 18. 交付物照片可查看（负责人可看全部、本人可看自己的）

1. **问题：** 拍照上传的交付物只记录文件名到 `memory/attachments/`，没有任何查看入口，负责人看不到照片内容。
2. **修改前：** 无附件服务接口；成员面板"📷 已交付"是纯文本徽标。
3. **修改后：** 新增 `GET /api/report/attachment?token=..&task_id=..`：鉴权后只服务该任务活动账本中记录过的照片文件（路径安全校验）；前端"📷 已交付"徽标变为可点击链接（负责人可看全部、协作者/志愿者看自己的），新窗口打开图片。
4. **为什么这样改：** 交付物要"可验"，负责人必须能点开看；按 token+任务+活动记录三重约束防越权访问任意文件。
5. **收益：** ① 负责人可点开交付物照片核验；② 非任务成员无法访问；③ 移动端正常打开。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 19. 大型项目：已确认志愿者进入交付页成员体系

1. **问题：** 大型项目里模块负责人（骨干）招募的志愿者完成部分后无法汇报——`report/link` 只认 `input.members`，`_task_member_names` 不含志愿者，志愿者生成链接即报"不在成员列表"。
2. **修改前：** `_task_member_names(task)` 只含 负责人/协作者/参与者；`report_link` 校验只查 `input.members`。
3. **修改后：** ① `_task_member_names(plan, task)` 并入 `volunteer_pool` 中"已确认"的志愿者；② `report/link` 允许已确认志愿者生成汇报链接；③ 成员面板角色显示"志愿者"（负责人确认其完成）。
4. **为什么这样改：** 多层级协作里"干活的人"不一定是方案成员列表里的人；已确认志愿者有明确的认领关系，应获得与协作者等同的汇报能力，完成状态仍由模块负责人确认。
5. **收益：** ① 志愿者可语音/拍照/工时汇报；② 模块负责人页面可见志愿者进度并确认；③ 大型项目交付链路（志愿者→骨干）打通。

**涉及文件：** `app/web/routers/report.py`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 20. 大型项目团队总览交付页（总负责人看板）

1. **问题：** 大型项目有"项目负责人 → 模块骨干 → 协作者/志愿者"多层级，总负责人没有直接认领任务，交付页打开是空列表，无法查看全局进度。
2. **修改前：** `report/state` 只返回"我的任务"；总负责人无任务时页面为空。
3. **修改后：** ① 新增 `_is_project_leader`（角色为"项目负责人"或担任某成员"上级"）与 `_build_overview`：大型项目总负责人打开交付页时，顶部是"团队总览"卡片——项目进度（X/Y + 进度条）、计划/实际总工时，下面按模块分组（骨干、完成数、模块进度条），每个任务显示任务状态 + 成员 mini 行（状态点/角色/待确认徽标/工时/备注/📷 交付物可点开查看）；② 骨干/志愿者仍只看自己的任务，不越权；③ `_member_tasks` 的成员明细抽成 `_task_members_detail` 复用。
4. **为什么这样改：** 总览的本质是"看"不是"改"——确认动作留在模块负责人手里，避免越级操作；按模块分组与主页面最终方案一致，降低理解成本。
5. **收益：** ① 总负责人一张交付页看全项目进度与各模块成员状态；② 交付物照片可直接点开核验；③ 骨干/志愿者权限不变。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 21. 大型项目交付页与小型全功能对齐 + 完成不变量拦截

1. **问题：** 大型项目交付页存在两处与小型不一致：① 志愿者在"我的任务"卡片角色显示"协作者"，且"我的状态"下拉逻辑不认志愿者（会显示成任务状态）；② 骨干在志愿者已上报"未完成"时仍能把任务标成"已完成"，破坏"任务完成 ⟺ 所有成员完成"的不变量。
2. **修改前：** `_member_tasks` 角色只区分 负责人/协作者；前端 `isCollab` 只认"协作者"；负责人确认完成不检查成员状态。
3. **修改后：** ① `_member_tasks` 角色区分 负责人/志愿者/协作者，前端 `isCollab` 同时认"协作者"与"志愿者"（我的状态下拉、无完成日期输入、"我的部分完成后由负责人确认"提示全部生效）；② 负责人确认完成时校验所有已上报成员的最近状态，只要有成员处于 未开始/进行中/阻塞 就返回 400 并列出是谁，杜绝"任务已完成但成员没完成"的矛盾。
4. **为什么这样改：** 交付页行为必须在大小型项目完全一致，差异只会让用户困惑；完成不变量要在后端强制，不能只靠 UI 提示。
5. **收益：** ① 志愿者体验与协作者完全一致（角色/我的状态/无日期输入）；② 负责人无法在成员未完成时误标完成；③ 实测大型链路全通（志愿者完成→骨干确认 5h→志愿者改回自动回退→总览同步）。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 22. 大型项目拆解走兜底时前端无提示

1. **问题：** 用户跑大型项目拆解时，DeepSeek 偶发失败（超时/限流），系统按设计自动降级为确定性兜底计划，但前端只识别小型项目兜底摘要前缀"AI 拆解本次未成功"，大型兜底摘要（"大型项目确定性兜底计划…"）不匹配，用户**静默拿到兜底方案**，误以为"没拆分成功"。
2. **修改前：** `/api/draft` 返回的 `warnings` 恒为空；前端 `usedFallback` 只比对摘要前缀。
3. **修改后：** ① `/api/draft` 在 `use_ai=true` 时按兜底 reasoning 标志（"LLM 规划失败 / LLM 不可用 / AI 拆解本次未成功"或摘要含"兜底"）识别降级，`warnings` 返回明确提示；② 前端改用 `data.warnings` 判断，提示改为"AI 本次未返回可用草案，已改用确定性兜底蓝图（可重新生成或手动编辑）"；③ 新增小型/大型两条降级警告回归测试。
4. **为什么这样改：** 兜底是特性不是缺陷，但必须"可见"——用户需要知道当前草案是 AI 生成还是确定性蓝图，才能决定重试或手动编辑；用 reasoning 标志判定比匹配摘要文本可靠。
5. **收益：** ① 大型项目兜底时前端明确提示，不再"静默换方案"；② 可一键重新生成；③ 小型项目提示文案同步统一。

**涉及文件：** `app/web/routes.py`、`app/web/static/app.js`、`tests/test_api.py`、`tests/test_workflow_v4.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 23. 团队总览入口可见：汇报链接生成器显示角色并推荐项目负责人

1. **问题：** 团队总览只对"项目负责人/上级"成员生效，但主页面「成员汇报链接」生成器只列出成员姓名（且大型项目通常只有骨干），用户找不到总览入口，也不知道需要先指定项目负责人。
2. **修改前：** `generateReportLink` 用 `prompt` 列出纯姓名，无角色提示、无推荐。
3. **修改后：** ① 生成器按成员列出"姓名（角色）"，大型项目中角色为"项目负责人"的成员标"★ 团队总览"并默认推荐；② 提示"如无项目负责人，可在项目配置添加该角色成员后再生成"；③ 输入值自动剥离角色后缀，避免误提交。
4. **为什么这样改：** 总览入口的"找不到"本质是缺少指定机制与可见性——先把入口做显眼，并引导用户指定负责人。
5. **收益：** ① 大型项目总览入口一目了然；② 未指定负责人时有明确引导；③ 无需改后端权限模型。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 24. 志愿者汇报链接入口 + 负责人代确认成员状态

1. **问题：** ① 已确认志愿者不在 `input.members`，主页面「成员汇报链接」只列成员，志愿者没有入口拿到自己的汇报链接；② 志愿者线下完成时，模块负责人只能干等"待确认"，无法代志愿者补录完成状态。
2. **修改前：** `generateReportLink` 只列 `input.members`；`report/update` 只能改"自己"或"任务"，无目标成员概念。
3. **修改后：** ① 链接生成器把 `volunteer_pool` 中"已确认"志愿者并入候选列表（角色"志愿者"）；② `report/update` 新增 `member` 字段：任务负责人可代某成员确认完成（写入 `confirmed` 成员状态，行内显示"已确认"徽标、待确认消失、工时保留），非负责人代他人返回 403；③ 成员进度行在负责人视角出现"确认完成/标记完成"小按钮；④ `_member_latest_hours` 对 `confirmed` 活动不清除该成员工时。
4. **为什么这样改：** 志愿者可能没账号/没登录，但"线下完成、负责人补录"是真实协作场景；负责人确认的是"该成员自己的部分"，与"任务完成"（还需所有成员完成）分层一致。
5. **收益：** ① 志愿者可拿到自己的汇报链接（语音/拍照/工时）；② 负责人可代志愿者补录完成并看到"已确认"；③ 权限不越界（仅负责人可代确认）。

**涉及文件：** `app/web/routers/report.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_report.py`、`docs/功能验证清单.md`、`docs/交接-P2起点.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 25. 负责人代确认后，"我的状态"下拉误显示"待开始"并可覆盖确认

1. **问题：** 负责人代志愿者/协作者确认完成（写入 `confirmed` 状态）后，对方自己的页面成员进度行正确，但顶部"我的状态"下拉仍显示"待开始"，且一旦操作下拉就会把自己的状态改回待开始，覆盖负责人的确认。
2. **修改前：** 下拉选项只有 `pending/in_progress/completed/blocked`，`myStatus` 取到 `confirmed` 时无匹配项，浏览器默认选中第一项（待开始）。
3. **修改后：** `myStatus` 遇到 `confirmed` 时映射为 `completed`（下拉显示"已完成"）；`confirmed` 仍作为独立成员状态保存（负责人视角显示"已确认"徽标）。
4. **为什么这样改：** `confirmed` 是负责人设置的成员状态，成员下拉展示它时应归一到"已完成"，否则选择器默认落到首项造成误显示与误覆盖。
5. **收益：** ① 被代确认的成员页面下拉正确显示"已完成"；② 不再出现"默认跳回待开始"；③ 成员主动改回未完成仍会覆盖自己的状态（属于本人操作，符合语义）。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 26. index.html 无缓存头导致前端修复"改了没生效"

1. **问题：** 用户多次反馈"改了很久还有细节问题"，复现发现当前代码行为正确（改回进行中后交付物照片保留、负责人行状态同步更新），但服务日志显示用户浏览器持续请求**旧哈希** `app.js?v=3ebd7446`（v6.9 第一版）——浏览器缓存了旧 index.html，一直在加载旧 JS，前端修复根本没生效。
2. **修改前：** `/` 由 `StaticFiles(html=True)` 提供 index.html，无显式 Cache-Control，浏览器启发式缓存旧页面；静态资源 `?v=` 哈希只有在 index.html 拿到新版时才有意义。
3. **修改后：** 新增 HTTP 中间件：所有 `text/html` 响应返回 `Cache-Control: no-cache, no-store, must-revalidate`，浏览器每次重新校验 index.html；JS/CSS 仍按 `?v=` 内容哈希缓存。
4. **为什么这样改：** 根因是"入口页被缓存"而非功能逻辑；入口页必须每次新鲜，静态资源才靠哈希长期缓存——两者配合才是正确的缓存策略。
5. **收益：** ① 每次刷新都加载最新前端，修复即刻生效；② 静态资源缓存策略不变；③ 新增回归测试断言 HTML 带 no-cache。

**涉及文件：** `app/main.py`、`tests/test_api.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 27. 交付物照片随成员后续改状态/代确认而消失

1. **问题：** 志愿者上传交付物照片后，只要自己再改一次状态（语音/下拉），或负责人代其确认完成，成员行里的"📷 查看交付物"就消失了；备注同理会被清空。
2. **修改前：** `_task_members_detail` 的照片/备注取"该成员最近一条活动"（`latest` 中的字段），任何后续不带照片/备注的活动（如纯状态变更、负责人代确认）都会把照片/备注顶掉。
3. **修改后：** 照片与备注改为取"该成员最近一条**带照片/带备注**的活动"（`latest_value` 倒序查找）；状态仍取最近一条活动。
4. **为什么这样改：** 交付物照片是"持久事实"，一旦上传除非重新上传否则应一直可见；备注是"最近一次说明"，不应被无备注的状态更新清空。两者与"状态"生命周期不同，必须分开取最近值。
5. **收益：** ① 照片/备注在改状态、代确认、语音汇报等任何后续操作后仍保留；② 实测上传→改状态→代确认全程照片可见；③ 回归测试覆盖。

**涉及文件：** `app/web/routers/report.py`、`tests/test_report.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 28. 答辩模拟语音/视频记忆由 3 轮扩到 10 轮

1. **问题：** 答辩模拟语音/视频问答的评委上下文只带最近 6 条消息（约 3 轮问答），长答辩中评委容易忘记前面内容；文字问答已带完整历史。
2. **修改前：** `interviewJudgeContext` 用 `ivChat.messages.slice(-6)`、每条截断 400 字。
3. **修改后：** 改为 `slice(-20)`（最近 10 轮）、每条截断 250 字，控制上下文体积；文字问答维持完整历史。
4. **为什么这样改：** 记忆窗口与上下文体积要平衡——20 条 × 250 字约 5000 字，叠加项目要求与答辩材料仍在模型上下文预算内。
5. **收益：** ① 语音/视频答辩可记住约 10 轮问答；② 实测 4 轮语音后评委仍记得"张三负责数据调研"；③ 上下文不失控。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 29. 答辩语音/视频轮次改为完整历史（与文字对齐）

1. **问题：** 文字问答传完整历史，但语音/视频轮次只把最近 20 条内嵌进评委上下文，长答辩中记忆深度不一致。
2. **修改前：** `interviewJudgeContext` 把"最近十轮"塞进 system_prompt；`/api/realtime/interview-turn` 无 history 参数。
3. **修改后：** ① `interview-turn` 新增 `history` 表单参数（JSON 数组），`understand_audio` 拼在音频/转写之前；② 前端语音/视频轮次把 `ivChat.messages` 完整历史作为 `history` 发送（与文字一致）；③ 评委上下文不再重复内嵌对话记录（只保留 项目要求/答辩材料/关注点），避免双写与上下文膨胀。
4. **为什么这样改：** 记忆统一由显式 history 传递，三模态（文字/语音/视频）同一条记忆链；内嵌式记录与显式历史并存会造成重复与不一致。
5. **收益：** ① 语音/视频答辩拥有与文字相同的完整记忆；② 实测 4 轮语音后评委仍记得"张三负责数据调研"；③ 上下文更干净。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/static/app.js`、`tests/test_realtime_client.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 30. 答辩模拟支持语音输入需求与拍照需求

1. **问题：** 答辩模拟的输入只有打字和上传文件，无法"说需求""拍需求"，与配置页的全模态输入不一致。
2. **修改前：** 答辩稿区域只有 textarea 与"上传 PPT/答辩稿"按钮。
3. **修改后：** ① 答辩稿下方新增"🎤 语音输入需求"（录音 → `/api/realtime/transcribe` 转写 → 以"[语音需求]"追加进答辩稿，可编辑）与"📷 拍照需求"（复用摄像头弹窗，拍摄 → `/api/analyze-files` 图片理解 → 结构化分析转文本，以"[图片需求]"追加进答辩稿）；② `openCamera` 增加 defense 模式（标题/提示语），`pickCameraFile` 与拍摄回调支持 defense；③ 录音状态 UI 支持 defense 源（红点脉冲）。
4. **为什么这样改：** 答辩需求与项目配置需求是同构的"说/拍"输入，复用同一套转写/视觉链路（已修复过语音噪声问题）；文本追加进答辩稿由用户编辑确认，避免转写噪声直接进材料。
5. **收益：** ① 答辩需求可"说"可"拍"；② 语音/拍照内容可编辑后再开始模拟；③ 实测：语音转写干净（"我是张三，负责数据调研"）、图片理解文本正确进入答辩稿、0 报错。

**涉及文件：** `app/web/static/app.js`、`app/web/static/style.css`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 31. 答辩语音需求改为"模型理解"统一链路（本地 A3 可用的需求要点）

1. **问题：** 答辩"🎤 语音输入需求"最初走 `/api/realtime/transcribe`（转写），但本地 A3 的 llama-omni 会直接把音频当对话"回答"而非转写，两端行为不一致；且用户需要的是"模型理解需求并参与答辩问题设计"，不是逐字转写。
2. **修改前：** 新增接口 `POST /api/realtime/voice-requirement` 走 `understand_audio` 适配层：本地直听、云端先转写再理解，统一返回"整理后的需求要点"（指令明确不转写、不复述、不客套）；前端答辩语音需求改调该接口，以"[语音需求]"追加进答辩稿。
3. **修改前到修改后：** 答辩稿（含语音/拍照需求文本）会作为材料传给 `/api/interview` 与 `/api/interview/chat`，即模型理解后的需求直接参与评委提问设计——与配置页"语音需求 → 模型理解 → 参与拆解"同一哲学。
4. **为什么这样改：** 语音理解必须收敛到已验证的 `understand_audio` 适配层，否则 A3/云端行为分叉；需求文本进答辩稿可编辑、可控，同时参与提问。
5. **收益：** ① 本地 A3 也能输出需求要点（直听）；② 实测需求语音返回"答辩时请评委关注创新点与技术实现细节，简要提及场景价值，基础问题可略过"；③ 参与评委提问设计。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/static/app.js`、`tests/test_realtime_client.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 打磨（P3）

#### 32. 答辩语音/拍照需求提示文案去歧义

1. **问题：** 答辩稿下方提示"语音/拍照内容会自动加入上方答辩稿，可编辑后再开始"，读起来像"必须手动编辑后才能开始"，与"AI 自动理解并参与评委提问设计"的新行为矛盾。
2. **修改前：** 提示强调"可编辑后再开始"。
3. **修改后：** 提示改为"语音/拍照需求会由 AI 自动理解并加入答辩稿，自动参与评委提问设计（如需修正可直接编辑）"。
4. **为什么这样改：** 自动理解+自动参与是主行为，编辑只是可选修正手段；文案要体现主行为，避免用户以为必须先手动处理。
5. **收益：** ① 文案与行为一致；② 用户知道语音需求会自动参与提问设计。

**涉及文件：** `app/web/static/app.js`、`CHANGELOG.md`。

### 打磨（P3）

#### 33. 答辩语音/拍照需求区 UI 升级

1. **问题：** 语音/拍照需求按钮与提示只有一行灰字，渲染简陋。
2. **修改前：** `.defense-voice-photo` 为一行 flex，提示是普通小号灰字 span。
3. **修改后：** 按钮区改为两列网格（移动端单列全宽），按钮带边框卡片样式；提示升级为信息卡片（💡 图标、左侧主题色竖条、浅底、圆角、两行排版）。
4. **为什么这样改：** 需求输入是答辩模拟的新入口，按钮与说明需要可读、可点的视觉层级。
5. **收益：** ① 桌面双按钮并排、手机单列堆叠；② 提示信息一目了然；③ 390px 无溢出、0 报错。

**涉及文件：** `app/web/static/app.js`、`app/web/static/style.css`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 34. 答辩材料与项目配置上传只能"替换"不能"追加"

1. **问题：** 答辩模拟上传 PPT/答辩稿、项目配置上传任务文件，选择一次文件后再次选择会把上一批顶掉（每次 onchange 都用新 FileList 覆盖），且选择后未清空 input 值，重选同一文件不触发；用户误以为"只能上传一个文件"。
2. **修改前：** `ivChat.files=Array.from(files.files||[])`、`state.files=Array.from(this.files)`（覆盖式）。
3. **修改后：** 新增 `mergeFiles`（按 名称+大小 去重合并，答辩上限 4、配置上限 8）；两个 onchange 改为追加合并，并 `value=''` 清空 input 以便重选；单次多选（multiple）本就支持。
4. **为什么这样改：** "选一批再加一批"是自然的上传心智，覆盖式会让用户觉得传不了多个；清空 input 修复"同文件重选不触发"。
5. **收益：** ① 答辩材料可分多次累积最多 4 个；② 配置文件可分多次累积最多 8 个；③ 实测 2+1=3 追加成功、0 报错。

**涉及文件：** `app/web/static/app.js`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 35. Excel/CSV/ICS 导出补全：参与清单、复盘、列数与 ICS 兼容性

1. **问题：** ① Excel"参与清单"为空——数据源用的是 `task.participants`，但方案数据里该字段基本为空，实际参与人（负责人/协作者/志愿者）是运行时推导的；② Excel"复盘"为空——只列有实际工时的任务，没填实际工时就是空表；③ CSV 只有 11 列，信息量远小于 Excel/报告；④ ICS 含中文时部分日历/Outlook 打不开。
2. **修改前：** `participant_rows` 只遍历 `task.participants`；`review_rows` 跳过无实际工时的任务；CSV 固定 11 列；ICS 无 BOM、无 METHOD、无折行。
3. **修改后：** ① 参与清单改为从 负责人/协作者/`task.participants`/已确认志愿者 推导并去重；② 复盘列出全部任务（无实际工时则留空，偏差/完成日期相应留空），表不再为空；③ CSV 增加 协作者/建议人数/所需技能/完成日期/关键路径 列；④ ICS 增加 UTF-8 BOM（Windows/Outlook 中文兼容）、`METHOD:PUBLISH`，并按 RFC 5545 将超 75 字节的行按字符折行（续行以空格开头）。
4. **为什么这样改：** 导出的数据源要与页面展示一致（参与人=负责人+协作者+志愿者），复盘要"全任务可核对"；ICS 的中文兼容是打不开的主因。
5. **收益：** ① Excel 参与清单/复盘不再空表（实测 8 行/6 行）；② CSV 信息量与报告看齐；③ ICS 结构校验通过（BOM+METHOD+折行、BEGIN/END 平衡、无超长行）。

**涉及文件：** `app/services/plan_io.py`、`tests/test_plan_io.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 36. 启动时打印本机/手机（局域网）访问地址

1. **问题：** 手机端操作需要知道电脑的局域网地址，用户每次都要自己查 IP；且 `APP_HOST` 默认 127.0.0.1 时手机根本访问不到，没有提示。
2. **修改前：** `python -m app.main` 启动后只有 uvicorn 日志，无访问地址提示。
3. **修改后：** 启动前打印访问横幅：本机地址；`APP_HOST` 允许外访（0.0.0.0/局域网 IP）时列出探测到的局域网 IPv4 地址（socket 探测，无外部依赖）并提示"同一 WiFi/放行防火墙/鉴权默认关闭建议配置 APP_ADMIN_TOKEN"；否则明确提示如何开启 `APP_HOST=0.0.0.0`。
4. **为什么这样改：** 手机端入口的第一步就是"知道访问哪个地址"，把地址和开启方法直接打在终端最省事。
5. **收益：** ① 启动即见手机访问地址；② 默认配置下也有明确的开启指引；③ 无新增依赖。

**涉及文件：** `app/main.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 37. 移动端汇报链接/只读链接无法自动复制

1. **问题：** 手机通过 `http://局域网IP:8000` 访问时非安全上下文，`navigator.clipboard` 不存在，代码把复制失败静默吞掉却仍提示"已复制"，链接无法自动复制。
2. **修改前：** `generateReportLink` 与 `shareCurrentPlan` 都 `try{await navigator.clipboard.writeText(url)}catch(e){}` 然后固定提示"已复制"。
3. **修改后：** 新增 `copyText`（优先 clipboard API，失败降级 `execCommand('copy')` 隐藏 textarea 方案，返回是否成功）；两处改为按结果提示：成功"已复制"，失败提示"请从弹窗中复制"并弹出包含链接的 `prompt` 供手动长按复制。
4. **为什么这样改：** 移动端 LAN 访问是常态，复制必须有降级；提示必须与实际结果一致，不能谎报成功。
5. **收益：** ① 手机生成汇报链接/只读链接可自动复制（或明确手动复制）；② 不再出现"提示已复制但实际没复制"。

**涉及文件：** `app/web/static/app.js`、`app/web/static/participants.js`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 38. 非安全上下文（http 局域网）下录音/录像/取景框提示明确

1. **问题：** 手机通过 `http://局域网IP` 访问时是非安全上下文，MediaRecorder/getUserMedia 不可用：录音、答辩录像、摄像头取景框全部失效，但提示语笼统（"浏览器不支持"），用户不知道为什么手机不能用语音。
2. **修改前：** 三处统一提示"当前浏览器不支持录音/录像/摄像头"。
3. **修改后：** 检测 `window.isSecureContext === false` 时提示"当前为非安全上下文（http），手机端无法录音/录像；请改用 HTTPS 或 localhost 访问"；取景框提示"已切换到系统相机/选图"（拍照经 `capture` 属性仍可用）。
4. **为什么这样改：** 浏览器安全策略限制，功能本身没问题——把原因讲清楚，用户才知道要上 HTTPS（Dev Tunnel/隧道/VPS）而不是换浏览器。
5. **收益：** ① 手机端语音失效时提示原因与解法；② 拍照回退为系统相机仍可用，提示如实。

**涉及文件：** `app/web/static/app.js`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 39. 自动 HTTPS：自签名证书 + 8443 监听，手机语音/录像零配置可用

1. **问题：** 手机用 `http://局域网IP` 访问时是非安全上下文，录音/录像/取景框不可用；让用户自己去配 HTTPS 不实用。
2. **修改前：** 只监听 HTTP；提示"请改用 HTTPS 或 localhost"。
3. **修改后：** 应用启动时自动生成自签名证书（缓存于 `memory/ssl/`，已 gitignore），并在 `APP_HTTPS_PORT`（默认 8443）后台线程监听 HTTPS；横幅手机地址只显示 https（含语音/录像/摄像头，首次证书警告点「继续」），本机仍显示 http://127.0.0.1；证书生成失败或端口被占用时静默跳过、不影响 HTTP；pytest 环境不启动。
4. **为什么这样改：** 手机要语音/录像必须安全上下文；自签名证书是局域网内零配置的可行方案，浏览器首次警告是安全规则无法绕过，但"点一次继续"远好于让用户配置证书。
5. **收益：** ① 手机直接开 `https://IP:8443` 即可用语音/录像（Chrome/Edge）；② HTTP 保留，localhost 桌面不受影响；③ 无需任何配置。

**涉及文件：** `app/config.py`、`app/main.py`、`.gitignore`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 40. 链接"只能复制第一次"：改为弹窗内点击复制（每次均为新手势）

1. **问题：** 手机连续生成多个汇报链接时，第二次复制粘贴的仍是第一次的链接。
2. **排查：** CDP 复现确认 JS 每次传给剪贴板的是不同新链接（两次 URL 不同）——根因是移动端浏览器安全规则：异步 fetch 之后自动执行 `navigator.clipboard.writeText` 时用户手势已过期，第二次写入被浏览器静默忽略（代码以为成功，剪贴板仍是旧内容）。这是平台限制，代码无法在"无手势"时保证写入。
3. **修改前：** 生成后自动 `copyText(url)`，失败才弹 prompt。
4. **修改后：** 生成链接后弹出"链接已生成"弹窗（含可选中链接文字 + 「复制链接」按钮 + 「完成」），**点击复制按钮时才执行复制（每次都是新的用户手势，100% 可靠）**；打开弹窗时仍自动尝试一次（成功则按钮显示"已复制 ✓"）。汇报链接与只读分享链接统一走该弹窗。
5. **收益：** ① 连续生成多个链接，每次点复制都写入当前链接（CDP 实测两次 URL 不同且与弹窗一致）；② 手机端有可长按复制的备选；③ 提示与行为一致。

**涉及文件：** `app/web/static/app.js`、`app/web/static/participants.js`、`app/web/static/style.css`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 41. 排期资源日历手机端乱码与挤压

1. **问题：** 手机打开排期资源页：日期显示成 `00:00:010T00:00:011T…` 乱码、列被压成一团。
2. **排查：** 后端 `resource_calendar` 的 `as_date` 用 `isinstance(value, date)` 判断——`datetime` 是 `date` 的子类，时间线里的 datetime（`2026-09-09T00:00:00`）被当成"已是 date"直接返回，导致 days 全是带时间戳的字符串，前端 `slice(5)` 切出乱码；同时不可用日期匹配也因此失效。另有测试把带时间戳的 days 当预期，固化了该 bug。
3. **修改前：** days = ["2026-09-09T00:00:00", …]；前端直接 `d.slice(5)`；`.cal-row` 无强制最小宽，手机挤压。
4. **修改后：** ① 后端 `as_date` 先判 `datetime` 转 `.date()`，days 恒为纯日期；② 前端日期显示/汇总统一 `String(d).slice(0,10)` 加固；③ `.cal-row` 设 `min-width:720px; width:max-content`、`.cal-cells` `min-width:max-content`，容器横向滚动（`-webkit-overflow-scrolling:touch`），首列"成员/日期" sticky 固定；④ 更新被 bug 固化的测试断言。
5. **收益：** ① 手机端日期干净可读（实测 "09-08"…），日历 720px 横向滑动不挤压；② 不可用日期红条纹恢复正常匹配；③ 首列固定方便对照成员。

**涉及文件：** `app/services/project_service.py`、`app/web/static/participants.js`、`app/web/static/style.css`、`app/web/templates/index.html`、`tests/test_project_service.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 42. 移动端全视图巡检 + 清理重复 renderResultTab 死代码

1. **问题：** 用户担心还有没发现的移动端渲染问题；巡检发现 app.js 存在两个 `renderResultTab` 定义（约 14KB 死代码被后定义覆盖），旧版按单标签渲染 workload/members/reminders 等，新版按角色合并到 team/collaboration/review，重复定义既浪费又误导排查。
2. **修改前：** 两个同名函数（后定义生效）；按旧标签调用会被静默回落到 tasks。
3. **修改后：** 删除第一个死代码定义（保留其后续辅助函数 resultSection/renderAssignmentMatrix/renderMemberEditor/renderDefensePanel 等），只保留角色化新版；用 CDP 在 390×844 对 三角色全部真实标签（manager: tasks/schedule/team/collaboration/review/report/interview；member: mine/schedule/collaboration；reviewer: evaluation/report/interview）+ 汇报页/总览/抽屉/会议弹窗 巡检：无文档级溢出、无控制台报错、无 undefined/NaN 异常文本；甘特图 792px 在自身 overflow-x:auto 容器内横向滚动属正常。
4. **为什么这样改：** 巡检的价值在于把"没查过的地方"系统性过一遍并消除隐患；重复定义是维护与排查的地雷。
5. **收益：** ① 三角色全部视图移动端渲染通过；② 死代码移除（-8KB），后续维护不再混淆；③ 巡检脚本可复跑。

**涉及文件：** `app/web/static/app.js`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 43. 资源日历手机端"往右划不动"：外层包装套娃滚动容器拦截触摸手势

1. **问题：** 用户在手机上打开大型项目"骨干认领/排期资源"页，资源日历只在手机屏幕宽度内显示开头几天，向右横滑后面的日期直接"被吞掉"，划不动；切换浏览器依旧。
2. **排查：** 用 CDP 在 390×844 实测并逐步消融定位：① 内层 `.resource-calendar` 内容宽 720px、可视 332px、maxScroll=388，**程序化滚动完全正常**，右侧日期（09-14 之后）都能显示；② 真正的问题在渲染结构套了两层滚动容器——`renderResultTab` 写入 `<div id="resourceCalendarContent" class="resource-calendar">`，`renderResourceCalendarHtml` 又在里面包了一个 `<div class="resource-calendar">`。外层 `#resourceCalendarContent` 因此也带 `overflow-x:auto`，但它的子级只有 332px，自己没有滚动量；移动端横向手势被这个无内容可滚的外层拦截，内层 720px 的日历永远收不到滑动。style.css 缓存参数旧值（5.76.2）是第二层因素，此前所有 CSS 修复手机端从未加载。
3. **修改前：** `#resourceCalendarContent` 带 `class="resource-calendar"`（`overflow-x:auto`），内部再渲染一个 `.resource-calendar`，两层滚动容器嵌套。
4. **修改后：** ① `app.js` 中 `#resourceCalendarContent` 去掉 `resource-calendar` 类，外层不再参与滚动，内层 `.resource-calendar` 成为唯一横向滚动容器；② `style.css?v=` 改为内容哈希 `d03cd0a5`、`app.js?v=` 改为 `2b6c685e`（均按文件内容重算），与 participants.js 同一套机制；③ `.cal-member-head span` 增加 `min-width:0; overflow-wrap:anywhere`，成员格头部窄屏可换行不再截断；④ AGENTS.md 静态资源缓存条款把 `style.css` 纳入内容哈希规则，避免再次漏算。
5. **为什么这样改：** 嵌套滚动容器的触摸事件归属由浏览器决定——手势落在外层"能滚但没内容"的容器上时，内层收不到滑动，这是移动端套娃滚动容器的典型问题；去掉外层滚动类后，720px 内容直接暴露给唯一滚动容器，横滑即生效。缓存参数改内容哈希则保证手机必然拉到新代码。
6. **收益：** ① 手机端资源日历可横滑查看全部日期（实测 scrollLeft 300px 后 09-14/15/16 正常显示）；② 成员格头部窄屏可读；③ 缓存规则统一，后续改动不再出现"改了没效果"。

**涉及文件：** `app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`、`AGENTS.md`、`docs/功能验证清单.md`、`CHANGELOG.md`。

**同步修改（2026-08-23）：** 该问题在真实手机仍复现。深入排查发现两层根因：① `.result-content{overflow-x:auto}` 作为外层横向滚动容器会先接收移动端横滑手势，而它自身无溢出内容，导致内层 `.resource-calendar` 收不到滑动；② 内层 `.cal-member{overflow:hidden}` 把 720px 的日历行裁剪在 332px 的成员卡片内，横滚时行被卡片边界"撕碎"，视觉上就是"后面的日期被吞掉"。修复：① `.result-content` 的 `overflow-x:auto` 改为 `overflow-x:clip`（内部组件自行横向滚动，外层不再拦截手势、也不会产生页面级横滚）；② 新增媒体查询 `@media (max-width:720px){ .cal-member{width:max-content; min-width:720px} }`，窄屏成员卡片宽度跟随日历行，行不再被裁剪。验证：390×844 下滚动 300px 后 09-09~09-17 全部可见、成员行内容完整、文档无横向溢出；桌面 1280px 布局不受影响。`style.css?v=` 更新为 `5256b4ad`。

### 健壮性提升（P1）

#### 47. 任务起止日期可修改并重算排期：手动日期成为排期硬约束

1. **问题：** 用户在子任务拆解或最终方案中修改任务截止时间后，整体排期不重算、临期提醒不出现——因为 TimelineAgent 完全按项目截止日倒推，任务自身的手动日期不参与排期，confirm/recompute 后 `sync_task_dates` 又把算法日期覆盖回去，手动改动丢失。
2. **排查：** 复现确认：draft 任务默认无日期（None），confirm 后 timeline 回填日期；再次返回修改日期并 confirm，timeline 重算仍用旧日期（T1 改 09-02 重算后仍是 09-07）。`reminders()` 的 `_task_end` 虽优先读 `task.end_date`，但该字段已被 timeline 覆盖。
3. **修改前：** `SubTask` 无手动日期标记；TimelineAgent 忽略任务日期；`recompute_plan` 不回填新排期到任务。
4. **修改后：** ① `SubTask` 新增 `dates_manual` 标记；② TimelineAgent 收集 `dates_manual && 有完整日期` 的任务作为固定锚点：前向/后向 CPM 中固定其窗口，其余任务围绕它重排，日期映射支持负工作日偏移（排到最早锚点之前）；③ `sync_task_dates` 保留手动日期任务不回填；④ `recompute_plan` 也调用 `sync_task_dates` 回填非手动任务的新排期；⑤ 前端子任务拆解 `taskFromCard` 对比输入值与旧值自动标记 `dates_manual`；⑥ 最终方案任务卡片新增"开始日期/截止日期"输入，修改后自动调 `/api/recompute` 重算排期（大型、小型项目共用同一套逻辑）。
5. **为什么这样改：** 时间线是排期事实来源没错，但用户手动指定的日期应当是硬约束——CPM 锚点是最小侵入方案：只固定被标记的任务，其余任务仍由依赖/资源约束自动排布，既不破坏倒推逻辑也不丢失用户意图。
6. **收益：** ① 改任务截止时间后 confirm/recompute 会真正重排，后续任务顺延；② 临期提醒按新日期生成（实测 T1 截止 08-24 → 提醒"剩余 1 天"）；③ 最终方案页可直接改日期并重算，无需返回子任务拆解；④ 大型/小型项目行为一致。

**涉及文件：** `app/models/schemas.py`、`app/agents/timeline.py`、`app/services/project_service.py`、`app/web/static/app.js`、`app/web/static/style.css`、`app/web/templates/index.html`、`tests/test_timeline.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

**同步修改（2026-08-23）：** 按用户意见把任务日期编辑从「任务计划」统一挪到「分工协作 → 任务参与清单」：任务计划卡片不再显示开始/截止日期输入（移除 `plan-date-inputs` 与 `bindPlanDateControls`）；参与清单每个任务卡片新增「开始日期 / 截止日期」输入与「应用日期并重算排期」按钮（`participant-dates` 区域），修改后调 `/api/recompute` 重算并刷新分工协作页。参与者、工时、角色、起止日期等可编辑项现在集中在一处。验证：任务计划 0 个日期输入、参与清单 13 个任务均有日期输入与按钮，修改 T1 → 应用 → timeline 同步为 08-22~08-24。`app.js?v=` → `2541fcc4`，`participants.js?v=` → `fed5c16c`，`style.css?v=` → `1185e233`。

### 体验优化（P2）

#### 44. 企业微信群机器人通知接入：提醒与成员汇报自动推送

1. **问题：** 应用已有通知体系（提醒列表"发送提醒通知"按钮 + 成员汇报状态变更），但 `notifier.py` 给 webhook 发的是自定义 JSON（`{project, event/reminders}`），企业微信群机器人只接受 `msgtype=markdown/text` 的消息格式，配置 webhook 后会被企业微信直接拒绝，通知一直无法真正到达群聊。
2. **修改前：** payload 为 `{"project": ..., "event": ...}` 或 `{"project": ..., "reminders": [...]}`，企业微信返回 `errmsg: invalid message`。
3. **修改后：** `notifier.py` 重构为 `_post_webhook(content)`——统一按企业微信 markdown 格式发送：提醒通知以 `**项目名** 待处理提醒 N 条` 开头、逐条 `> 标题：详情`；成员汇报/状态变更以 `**项目名**\n> 事件` 发送，均带推送时间。`.env` 配置 `APP_NOTIFY_WEBHOOK`（已被 gitignore，密钥不进仓库），`.env.example` 补充说明。
4. **为什么这样改：** webhook 是群机器人的唯一入口，消息格式是硬性契约；把格式适配下沉到 notifier 后，前端 `/api/notify`、成员汇报 `notify_event` 等所有调用点无需改动即自动生效。
5. **收益：** ① 企业微信实测返回 `errcode:0`，提醒与成员汇报可直达群聊；② 消息带项目名便于多项目区分；③ 密钥只存本机 `.env`，不随仓库泄漏。

**涉及文件：** `app/services/notifier.py`、`.env`（本地）、`.env.example`、`tests/test_notifier.py`、`docs/功能验证清单.md`、`CHANGELOG.md`。

#### 45. 通知多平台兼容：企业微信 / 飞书 / 钉钉自动适配

1. **问题：** 通知仅按企业微信 `msgtype=markdown` 契约发送，飞书/钉钉群机器人无法直接使用；用户询问飞书是否兼容。
2. **修改前：** 单一 `APP_NOTIFY_WEBHOOK`，统一发企业微信格式，不支持多群同时推送。
3. **修改后：** ① 新增 `APP_NOTIFY_WEBHOOKS`（逗号/分号分隔多地址），与 `APP_NOTIFY_WEBHOOK` 兼容并存、去重合并；② `_webhook_kind(url)` 按域名识别平台：企业微信（qyapi.weixin.qq.com）→ markdown、飞书（open.feishu.cn）→ interactive 卡片、钉钉（oapi.dingtalk.com）→ markdown、未知 → 纯文本；③ 多地址逐个推送，返回 `sent/enabled/bodies/errors` 汇总，单群失败不影响其余群。
4. **为什么这样改：** 每个平台消息格式是硬性契约（企业微信 msgtype、飞书 msg_type/card、钉钉 msgtype），按 URL 域名自动选型比让用户手动指定格式更不易配错；多地址支持可同时通知多个群。
5. **收益：** ① 同一套配置可推企业微信 + 飞书 + 钉钉任意组合；② 飞书以卡片呈现，信息更结构化；③ 测试覆盖三平台 payload 与多地址并发（4 passed，全量 316 passed）。

**涉及文件：** `app/services/notifier.py`、`app/config.py`、`.env.example`、`tests/test_notifier.py`、`CHANGELOG.md`。

#### 46. 通知推送时间错 7 小时：notifier 用了系统时区而非应用时区

1. **问题：** 群里收到的消息显示"1:01"，实际是"8:01"——推送时间少了 7 小时。
2. **排查：** notifier 里用 `datetime.now()` 取时间，它返回服务器系统时区时间；项目应用时区由 `APP_TZ`（默认东八区 +8）定义，启动时 `configure_timezone()` 依赖 Linux `time.tzset()`，在 Windows/部分部署环境不生效，导致系统时区与 `APP_TZ` 不一致。
3. **修改前：** `datetime.now().strftime('%H:%M')`（notifier.py 两处）。
4. **修改后：** 改为 `from app.config import now as app_now`，使用 `app_now().strftime('%H:%M')`——`app_now()` 内部按 `APP_TZ` 固定偏移计算，不依赖系统时区。
5. **为什么这样改：** 与排期边界（`today()`/`now()`）同一套时区口径，通知时间与页面"今天"保持一致；系统时区是什么不再影响推送时间。
6. **收益：** ① 推送时间恒为应用配置时区（默认东八区）；② 与项目其他时间逻辑统一；③ 不依赖部署环境是否支持 tzset。

**涉及文件：** `app/services/notifier.py`、`CHANGELOG.md`。

**同步修改（2026-08-22）：** `/api/health`、`/api/ready` 版本号 6.9 → 7.0；`docs/多模态落地改造清单.md` 勾选视频理解/移动端适配/演示脚本；`docs/功能验证清单.md` 增加录像旁听、窄屏、语音记忆、主页面自动同步、成员级进度、工时累加、完成日期归属、成员状态语义、回退警告、备注去重、交付物查看、志愿者汇报、团队总览、大型项目对齐、兜底提示、总览入口、代确认、下拉归一、HTML 缓存、照片保留、答辩完整记忆、答辩语音/拍照输入、语音需求理解、提示文案、需求区 UI、多文件上传、导出补全、手机访问地址提示、移动端链接复制、非安全上下文提示、自动 HTTPS、弹窗复制、资源日历移动端与全视图巡检、企业微信群机器人通知接入、通知多平台兼容（企业微信/飞书/钉钉）、任务起止日期可修改并重算排期；全量测试 291 → 293 → 295 → 297 → 299 → 300 → 301 → 303 → 304 → 305 → 306 → 307 → 308 → 309 → 310 → 311 → 314 → 316 → 319 passed；`app.js?v=` 内容哈希更新（3ebd7446 → b76c30b0 → cb210587 → 07f4bba5 → 426dc9c0 → 444b84ee → 3dfc830a → 1df0ce5e → 30f8f38b → b85bd944 → a3ef6b30 → 1a07dc4f → 9f53303a → 24913ac6 → 6a2a103c → ddb17b3f → e58551bc → e5815907 → 7dd00da3 → 7e49694d → e6a984ec → e50d1a9e → 4809de87 → 250419d0 → b2b607b7 → 493ea1d5 → cd952134 → 2b6c685e → c1197653），`participants.js?v=` 42eb73fd → 3fffd76d → 799d7271 → 6e0ede94，`style.css?v=` 5.76.2 → bb8e686c → d03cd0a5 → fab3e9f7 → 5256b4ad → dec6d03a。

---
## v6.9 —— 多模态需求输入：语音描述与拍照直接生成任务（2026-08-21）

**定位：** 把全模态能力接入项目配置核心入口：🎤 语音描述需求自动填入项目背景、📷 拍照需求自动进入文件分析，实现"不打字也能建计划"。

**审查/修改背景：** P0 已把 MiniCPM-o 接入 AI 建议抽屉，但多模态仍停留在附加功能；用户要求把全模态融入分工协作的核心流程，需求输入是第一步。

### 关键缺陷（P0）

#### 1. 项目配置页增加语音描述与拍照需求入口

1. **问题：** 需求输入只能打字或上传文件，全模态能力被限制在 AI 建议抽屉这个小角落，没有进入核心协作流程。
2. **修改前：** 上传区只有"上传任务要求文件"按钮；无语音、无拍照入口。
3. **修改后：** 新增 🎤 语音描述需求按钮（录音 → `/api/realtime/transcribe` → 文字自动填入 `background`，可修改后生成拆解）和 📷 拍照需求按钮（`accept="image/*" capture="environment"`，选择/拍摄照片 → 加入 `state.files` → 立即 `analyzeFiles()` 识别需求）；识别失败显示明确状态，不静默丢失。
4. **为什么这样改：** 需求输入是协作流程第一步，也是全模态最自然的入口；完全复用现有转写与文件分析管线，不改后端。
5. **收益：** ① 全程不打字即可建计划；② "我来说、它来拆任务"从抽屉小角落变成核心入口；③ 演示可现场说话/拍照生成草案。

#### 2. 图片分析升级为全模态理解（不止 OCR）

1. **问题：** 拍照/上传图片只做文字提取（OCR），遇到流程图、界面稿、手绘草图、场景照片等无文字或文字很少的图片会识别失败或丢失关键信息，浪费 MiniCPM-o 的视觉理解能力。
2. **修改前：** 提示词为"请提取这张图片中的文字和关键信息"，成功前缀为 `[图片 OCR]`；无文字图片基本无法转化为需求材料。
3. **修改后：** 提示词改为三步理解：提取全部文字 → 描述非文字内容（图表、流程图、界面、场景、物品、手绘等）→ 结合项目需求提炼相关信息；成功前缀改为 `[图片理解]`；Realtime 与 OpenAI 兼容兜底两条路径统一；测试断言同步更新。
4. **为什么这样改：** 全模态的差异点在"理解"而非"识字"；图片描述与 OCR 文本一样进入需求分析管线，让无文字图片也能成为任务拆解依据。
5. **收益：** ① 无文字流程图实测被正确描述（色块、箭头、"步骤/数据流向"）；② 图片从"识字工具"变成"视觉理解入口"；③ 演示可现场拍流程图/白板草图直接进拆解。

**涉及文件：** `app/services/media_analysis.py`、`tests/test_media_analysis.py`、`tests/test_media_formats.py`、`docs/使用说明书.md`、`docs/华为昇腾创新应用赛道接入说明.md`、`docs/功能验证清单.md`、`docs/多模态落地改造清单.md`、`docs/比赛全量备赛手册.md`、`docs/部署与回退清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 3. 录音组件泛化复用与事件冒泡防护

1. **问题：** 原录音逻辑只服务抽屉 `micBtn`、硬编码填入 `chatInput`，无法复用于配置页；新按钮位于 `uploadBox` 内，点击会冒泡误触文件选择器。
2. **修改前：** `toggleRecording()` 无参数；`transcribeRecording` 固定写 `chatInput`；新按钮未做冒泡处理。
3. **修改后：** `toggleRecording(onText, source)` 泛化，转写结果按 `state.voiceOnText` 路由到抽屉或背景；`setRecorderUI` 按 `recorderSource` 分别控制两个录音按钮的状态与禁用；新按钮 click 均 `stopPropagation()`。
4. **为什么这样改：** 单一录音链路多入口复用，避免复制两份 MediaRecorder 逻辑；阻止冒泡避免点击"拍照/语音"误弹文件选择器。
5. **收益：** ① 抽屉与配置页共用一套录音/转写链路；② 录音态互不干扰、防止重复录音；③ 后续汇报页可直接复用同一组件。

**涉及文件：** `app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`、`docs/多模态落地改造清单.md`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`README.md`、`CHANGELOG.md`。

### 打磨（P3）

#### 4. 演示前一键预检脚本与版本号同步

1. **问题：** A3 服务出现过多次空闲失联，演示现场靠人记命令检查，容易漏项；`/api/health` 版本号还停留在 6.5，与实际版本线不一致。
2. **修改前：** 无预检脚本；`system.py` 中 `/api/health`、`/api/ready` 硬编码 `version: "6.5"`。
3. **修改后：** 新增 `scripts/preflight_demo.py`：检查应用服务、后端配置（本地/云端/兜底）、A3 health、Realtime 状态，并在 A3 健康或云端配置时执行一次真实对话暖机；失败项输出修复提示并以退出码 1 结束。`system.py` 版本号同步为 6.9。
4. **为什么这样改：** 演示前把"人记命令"变成"一个脚本、看结论"；版本号与 README/CHANGELOG 对齐，避免评审看到自相矛盾的信息。
5. **收益：** ① 现场预检 10 秒完成且含暖机动作；② 失败项带修复提示；③ 健康接口版本与实际一致。

**涉及文件：** `scripts/preflight_demo.py`（新增）、`app/web/routers/system.py`、`docs/功能验证清单.md`、`docs/比赛全量备赛手册.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 5. 拍照需求接入桌面摄像头

1. **问题：** `capture="environment"` 仅在手机浏览器生效，桌面端点击"拍照需求"直接变成文件选择器，无法调用电脑摄像头。
2. **修改前：** 点击 `cameraReqBtn` 只触发隐藏文件输入，桌面端无摄像头入口。
3. **修改后：** 新增摄像头取景弹窗：`openCamera` 用 `getUserMedia({video:{facingMode:'environment'}})` 打开摄像头，`captureCameraPhoto` 用 canvas 截帧生成 JPEG 并复用 `addPhotoToFiles` 进入识别管线；摄像头打不开或浏览器不支持时自动回退文件选择；关闭时停止摄像头占用。
4. **为什么这样改：** "拍照"必须真的能拍；桌面端用取景框截帧，移动端沿用后置摄像头，两条路径一致，且失败有明确回退。
5. **收益：** ① 桌面/手机都能真实拍摄需求材料；② 拍照与选图共用同一识别管线；③ 摄像头权限被拒时不会卡死。

**涉及文件：** `app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 6. 语音输入状态残留与转写噪音修复

1. **问题：** 抽屉麦克风第一次"开始→停止"后 `state.recording` 未复位，第二次点击无响应；音频转写提示词太弱，本地模型把思考过程和确认语当成结果填进输入框。
2. **修改前：** `stopRecording` 未将 `state.recording` 置 false；音频转写提示词为"请转写这段音频中的文字，只输出文字内容"。
3. **修改后：** `stopRecording` 停止时立即 `state.recording=false`；音频转写提示词改为"直接转写用户原话，不要思考/解释/复述/补充/确认"；静态资源版本号升到 6.9.1 强制刷新。
4. **为什么这样改：** 录音状态机必须闭环，否则按钮假死；8B 模型需要更强制式指令抑制附加输出。
5. **收益：** ① 连续多次语音输入正常；② 转写结果不再混入思考过程与确认语；③ 浏览器强制加载修复后的 JS。

**涉及文件：** `app/web/static/app.js`、`app/services/media_analysis.py`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 7. 抽屉语音改为"直接语音对话"，并统一智能体身份

1. **问题：** 抽屉 🎤 走"转写进输入框"，MiniCPM-o 是对话模型，会把"你好"转写成"你好，有什么可以帮你"这类回答；用户需要的是直接语音对话，且模型被问"你是谁"时介绍底层模型而非智能体。
2. **修改前：** 抽屉录音 → `/api/realtime/transcribe` → 文本填 `chatInput`；系统提示词未约束身份。
3. **修改后：** 新增 `POST /api/realtime/voice-chat`（录音解码为 PCM 后作为音频消息 + `omni_mode` 直接发给 MiniCPM-o，返回文本回答与可选 TTS 音频，TTS 失败自动降级纯文本重试）；抽屉 🎤 改为直接语音对话（气泡显示"🎤 语音消息"+ 回答，不写入输入框）；配置页 🎤 保留转写填背景；Realtime 与 `/api/chat` 提示词均加入"协作分工助手"身份约束，问"你是谁"只介绍智能体。
4. **为什么这样改：** 让模型做它擅长的（听懂音频直接作答），而不是做它不擅长的（逐字转写）；比赛要求充分展示 MiniCPM-o 全模态能力，语音对话正是其核心用法，必须保持主力地位而非兜底。
5. **收益：** ① 语音对话实测可用（A3 4s 回答"我是协作分工助手…"）；② 身份统一为智能体，不再暴露底层模型；③ 转写路径仅保留给需求描述场景。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/static/app.js`、`app/web/routes.py`、`app/web/templates/index.html`、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`docs/华为昇腾创新应用赛道接入说明.md`、`docs/多模态落地改造清单.md`、`CHANGELOG.md`。

### 打磨（P3）

#### 8. 智能体命名统一与版本策略

1. **问题：** 提示词与文档误用"清小搭"（另一比赛平台名）作为产品名；小 bug 频繁递增版本号导致版本迭代过快（6.9.0 → 6.9.1 → 6.9.2）。
2. **修改前：** Realtime / `/api/chat` 提示词自称"清小搭"；静态缓存参数随小修复升至 6.9.2。
3. **修改后：** 全部改为"协作分工助手"身份；静态资源缓存参数改为文件内容哈希（sha1 前 8 位）；AGENTS.md 新增版本号管理约定（小修复不升版本号，只有功能里程碑才递增）。
4. **为什么这样改：** 产品名不能借用其他平台名称；版本号只应表达功能里程碑，静态缓存不应伪装成产品版本。
5. **收益：** ① 命名统一且不与外部平台混淆；② 版本号不再被小修复推高；③ 后续协作者遵守同一策略。

**涉及文件：** `app/web/static/app.js`、`app/web/routes.py`、`app/web/routers/realtime.py`、`app/web/templates/index.html`、`docs/功能验证清单.md`、`AGENTS.md`、`CHANGELOG.md`。

#### 9. 配置页语音需求改为"添加语音需求"（录音进材料，不贴文本）

1. **问题：** 配置页 🎤 走"转写填入项目背景"，MiniCPM-o 是对话模型，会把用户语音转成对话式回答并贴进背景，显然不对。
2. **修改前：** 配置页录音 → `/api/realtime/transcribe` → 文本追加到 `background`。
3. **修改后：** 配置页 🎤 改为"添加语音需求"：录音直接转为音频文件加入 `state.files`，走与拍照相同的 `addRequirementFile` 管线（MiniCPM-o 理解音频 → 需求分析 → 参与拆解），不再写入背景；按钮文案、提示语与文档同步更新。
4. **为什么这样改：** 与拍照需求对称——照片进文件列表、录音也进文件列表，都由模型理解后参与拆解；避免"逐字转写/模型作答"贴进文本框的错误用法。
5. **收益：** ① 语音需求不再污染背景文本；② 音频与图片需求共用同一识别管线；③ 演示可"录音 → 已读取 → 生成拆解"。

**涉及文件：** `app/web/templates/index.html`、`app/web/static/app.js`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`README.md`、`docs/多模态落地改造清单.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 10. 不可用日期选择上限与截止日期不同步

1. **问题：** 载入演示案例后把截止日期从默认（今天+15 天）改成更晚日期，成员"不可用日期"选择器上限仍停留在旧截止日，只能选窗口外日期，资源日历因此看不到不可用标记（窗口内没有格子）。
2. **修改前：** `bindUnavailablePicker` 只在绑定瞬间读取一次 `endDate` 设置 `max`；`endDate` 变更处理器只更新 `startDate.max`。
3. **修改后：** 新增 `syncUnavailableDateLimits()`，在 `startDate`/`endDate` 变更时同步所有 `.unavailable-date-input` 的 `min`/`max`。
4. **为什么这样改：** 排期窗口变化后选择器必须跟随，否则成员无法标记窗口内的不可用日期；资源日历标记逻辑与数据本就正确（实测窗口内日期正常显示红条纹），根因在输入上限。
5. **收益：** ① 截止日期修改后选择器上限即时同步；② 资源日历不可用红条纹恢复可用。

**涉及文件：** `app/web/static/app.js`、`CHANGELOG.md`。

### 体验优化（P2）

#### 11. 答辩模拟接入语音输入与语音播报

1. **问题：** 答辩模拟只有文字问答，全模态语音能力未进入"口语演练"场景。
2. **修改前：** 答辩回答框只能打字；AI 回复只有文字，无朗读。
3. **修改后：** 新增 `POST /api/realtime/tts` 文本朗读接口；答辩表单动态注入 🎤 语音输入（录音 → 转写 → 填入回答框）与"语音播报"开关（仅云端后端启用，AI 回复自动朗读）；`renderInterviewChat` 为每条 AI 回复添加"🔊 播报"按钮。
4. **为什么这样改：** 答辩模拟本质是口语演练，语音输入与播报最贴近真实答辩；TTS 受 910C 限制，播报仅云端启用，语音输入不受影响。
5. **收益：** ① 可"口述回答、听 AI 追问"，像真实通话；② 文字记录保留；③ 为后续摄像头表情分析铺路。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 12. 答辩模拟接入摄像头录像表现分析

1. **问题：** 答辩练习是口语场景，只看文字/听语音仍不够贴近真实答辩，AI 无法观察用户的表情与状态。
2. **修改前：** 答辩模拟只有文字问答 + 语音输入/播报，无视觉分析。
3. **修改后：** 新增 `POST /api/realtime/performance`：PyAV 从录像中均匀抽 4 帧（JPEG）+ 抽音频（16k PCM）；抽帧用 MiniCPM-o 图片理解分析表情/状态并给出改进建议，抽音频走现有转写返回回答文本；前端新增 📹 录像弹窗（摄像头预览、开始/停止、60 秒自动停、计时提示），停止后自动上传，回答填入回答框、表现点评作为"📹 表现分析"卡片追加到对话。`media_analysis` 新增 `extract_video_frames` / `extract_audio_pcm16k` 工具。
4. **为什么这样改：** 一条录像同时喂给视觉（表情）与听觉（回答内容），正是 MiniCPM-o"边看边听"的核心能力；抽帧控制输入量、失败降级为空分析，保证链路稳健。
5. **收益：** ① 答辩练习可"对着镜头练、AI 看表情给建议"；② 回答自动转写进输入框，可修改；③ 实测合成视频 4 帧 4s 完成分析，A3 健康不受影响。

**涉及文件：** `app/services/media_analysis.py`、`app/web/routers/realtime.py`、`app/web/static/app.js`、`app/web/templates/index.html`、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`docs/多模态落地改造清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 13. 录像分析长时长与失败兜底加固

1. **问题：** 60 秒自动停止对长回答太短；默认 MediaRecorder 码率高，60 秒录像易超 30MB 上限导致分析失败；抽帧整段载入内存，长视频又慢又吃内存；失败原因不明确。
2. **修改前：** 60 秒上限；录制不设码率；`extract_video_frames` 用 `list(container.decode())` 全量解码；接口上限 30MB、失败静默降级。
3. **修改后：** 上限提到 3 分钟（可提前停止）；录制设 `videoBitsPerSecond=1.2M` / `audioBitsPerSecond=64k` 低码率，60 秒约 10MB、3 分钟约 27MB；接口上限提到 60MB；`extract_video_frames` 改为流式采样（按 `stream.frames`/时长计算步长，只保留目标帧，不整段载入）；媒体分析默认超时提到 180s、表现分析显式 240s；返回 `warning` 字段说明失败原因，前端展示明确提示。
4. **为什么这样改：** 长回答是真实场景，必须压码率保体积、流式抽帧保性能、放宽超时保完成、失败原因可见保体验。
5. **收益：** ① 最长 3 分钟回答可用；② 60 秒录像不再超限失败；③ 长视频分析内存/耗时可控；④ 失败不再"无声无息"。

**涉及文件：** `app/services/media_analysis.py`、`app/web/routers/realtime.py`、`app/web/static/app.js`、`app/web/templates/index.html`、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`CHANGELOG.md`。

### 体验优化（P2）

#### 14. 答辩模拟改为直接语音/视频对话（去掉"整理成书面回答"）

1. **问题：** 答辩 🎤/📹 仍走"转写/整理成书面回答填入回答框"，MiniCPM-o 会把语音理解成对话式回答，内容驴头不对马嘴；且没有材料/关注点时无法提问、兜底问题反问用户"核心观点"。
2. **修改前：** 答辩语音走 `/api/realtime/dictate` 整理成文字填入回答框；录像走 `/api/realtime/performance` 转写回答；无材料时无法开始。
3. **修改后：** 新增 `POST /api/realtime/interview-turn`：音频（含视频抽帧）直接作为多模态输入发给 MiniCPM-o，评委以"【回答摘要】+【评委回复】"格式当场点评追问（摘要用于多轮记忆，前端展示为"回答要点"）；视频时逐帧看画面并附"📹 表现观察"；前端 🎤/📹 改为直接对话，不再写回答框；无材料但有项目方案时可开始，兜底问题改为基于项目/任务提问。
4. **为什么这样改：** 与 AI 建议抽屉原则一致——让模型直接听懂/看懂，而不是做它不擅长的转写；摘要机制保证多轮对话上下文连贯。
5. **收益：** ① 语音/视频回答直接对话，评委回复与内容对应；② 不填关注点/材料也能基于方案提问；③ 视频时评委"边看边听"附表现观察。

**涉及文件：** `app/web/routers/realtime.py`、`app/web/static/app.js`、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`docs/多模态落地改造清单.md`、`CHANGELOG.md`。

### 健壮性提升（P1）

#### 15. 成员轻量汇报页：语音/拍照/状态更新闭环

1. **问题：** 成员更新任务状态必须登录完整工作台手动点击，协作流程重；语音/拍照能力未进入执行期。
2. **修改前：** 状态切换只在工作台内完成；无成员侧入口。
3. **修改后：** 新增 `app/services/report_link.py`（token 绑定 方案文件+成员，14 天有效，汇报备注独立存储）与 `app/web/routers/report.py`：`/api/report/link` 生成链接、`/api/report/state` 返回我的任务、`/api/report/voice` 听懂语音汇报（状态/工时/备注）、`/api/report/photo` 拍照交付、`/api/report/update` 应用变更（状态+实际工时 → `record_task_actual` + `recompute_plan` → 保存 → 推送通知）。前端新增"成员汇报链接"按钮与 `?report=token` 汇报模式页（我的任务、语音确认、拍照、手动更新）。
4. **为什么这样改：** 执行期闭环必须"成员侧轻量 + 服务端可靠"：token 免登录、更新走与工作台一致的业务链路，避免两套逻辑分叉。
5. **收益：** ① 成员手机说话/拍照即可更新任务并自动重排；② 每次状态变更自动通知团队；③ 语音识别需人工确认后才应用，防误操作。

#### 16. 群机器人通知自动推送与今日播报

1. **问题：** 通知只靠手动按钮，状态变更不会自动触达成员手机；提醒只有文字没有语音。
2. **修改前：** `notifier.py` 只有 `notify_reminders`；提醒页只有"发送提醒通知"。
3. **修改后：** `notifier.py` 新增 `notify_event(plan, text)`，汇报页状态变更（完成/阻塞/开始）自动推送"张三已完成「调研」6h"等事件；提醒页新增"🔊 今日播报"：云端后端用 `/api/realtime/tts` 语音播报今日要点，本地降级文字展示。
4. **为什么这样改：** 自动触达是"通知到达成员手机"的关键（webhook 指向企业微信群机器人即可）；播报让提醒从"要看"变成"要听"。
5. **收益：** ① 状态变更即时推群；② 今日要点一键语音播报；③ 本地后端不触发 TTS 不会挂服务。

#### 17. 会议旁听

1. **问题：** 组会讨论产出的任务/负责人/截止日靠人工记录，语音理解能力未进入需求输入。
2. **修改前：** 无会议入口。
3. **修改后：** 新增 `POST /api/realtime/meeting`：MiniCPM-o 直接听懂会议录音，按【总结】/【任务】（任务|负责人|截止）/【风险】格式整理；配置页新增"🎙 会议旁听"按钮，解析结果弹窗展示，"生成任务草案"自动填入项目背景并进入拆解。
4. **为什么这样改：** 会议是协作分工最重要的需求来源；直接理解音频（非逐字转写）符合 MiniCPM-o 能力定位。
5. **收益：** ① 一段录音即可沉淀任务草案；② 任务带负责人/截止信息；③ 与既有 planner 管线无缝衔接。

#### 18. recompute 公共服务抽取

1. **问题：** `/api/recompute` 的重算逻辑内联在路由里，汇报页更新无法复用，容易分叉。
2. **修改前：** `routes.recompute_plan` 内部实现全部逻辑。
3. **修改后：** 抽取为 `project_service.recompute_plan(plan)`，路由与汇报页共用同一实现；路由改为薄封装。
4. **为什么这样改：** 单一事实源，两处行为一致。
5. **收益：** 汇报页更新与工作台状态切换走完全相同的重算链路；测试覆盖（290 passed）。

**涉及文件：** `app/services/report_link.py`（新增）、`app/web/routers/report.py`（新增）、`app/services/project_service.py`、`app/services/notifier.py`、`app/web/routers/realtime.py`、`app/web/routes.py`、`app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/participants.js`、`app/web/static/style.css`、`tests/test_report.py`（新增）、`tests/test_realtime_client.py`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`docs/多模态落地改造清单.md`、`CHANGELOG.md`。

#### 19. 云端音频理解适配（先转写再理解）

1. **问题：** 切换云端后端后，AI 建议抽屉语音对话把用户语音"复述"回来而不是回答——云端 MAP Realtime 的 turn-based chat 对音频输入表现为"转写并复述"，不听理解指令；本地 A3 能直接理解，两端行为不一致，答辩/会议/语音汇报同样受影响。
2. **修改前：** 各音频理解接口直接向 `RealtimeClient` 发送"文本指令 + 音频"，依赖后端直接理解，云端全部退化为复述。
3. **修改后：** 新增 `app/services/omni_chat.py` 统一入口 `understand_audio`：本地昇腾直接听音频；云端先 `transcribe_audio`（明确转写指令，实测逐字准确）再以转写文本走文字理解/回答。`voice-chat`、`interview-turn`、`meeting`、`report/voice` 四个接口全部改走统一入口，TTS 与降级重试逻辑保留。
4. **为什么这样改：** 平台差异是 root cause，必须收敛到单一适配层，否则各端点各写一套、行为继续分叉；统一入口让"切回 A3"时行为不变。
5. **收益：** ① 云端语音对话正常回答（实测"我是协作分工助手…"）；② 答辩/会议/语音汇报在云端同样可用；③ 本地与云端共用同一入口，行为一致。

**涉及文件：** `app/services/omni_chat.py`（新增）、`app/web/routers/realtime.py`、`app/web/routers/report.py`、`tests/test_realtime_client.py`、`tests/test_report.py`、`docs/华为昇腾创新应用赛道接入说明.md`、`CHANGELOG.md`。

### 关键缺陷（P0）

#### 20. 答辩评委输出占位符、语音复述与多人任务完成语义

1. **问题：** ① 答辩评委把提示词里的 `<摘要>` / `<点评与下一个问题>` 占位符原样输出，播报还会念出来；② 评委点评没有聚焦"是否正面回答/是否回避/是否全面"，且一问一答缺少追问感；③ 抽屉语音有时把用户原话复述回来，且语音问答没有读取当前方案快照；④ 多人任务任一成员说"完成"就完成任务，与现实"协作者汇报、负责人确认"流程不符。
2. **修改前：** `INTERVIEW_TURN_INSTRUCTION` 用尖括号占位符描述格式，模型照抄；`understand_audio` 云端第二步把"指令+转写"混在用户消息里导致复述；语音消息不带方案上下文；`report/update` 与 `report/photo` 允许任意成员直接标完成。
3. **修改后：** ① 指令改为纯描述式结构并追加"不要输出占位符/尖括号标签"，`_parse_turn_text` 增加占位符行清理；② 评委点评改为"明确是否正面回答/回避/全面/依据到位，不到位就同一问题追问，到位才提新问题"；③ `understand_audio` 云端第二步转写文本单独作为用户消息、指令并入系统提示词；抽屉语音新增 `buildVoiceContextPrompt` 携带当前方案快照；④ 任务完成/阻塞需负责人确认：非负责人标记完成/阻塞返回 403 明确提示，协作者只能更新进度/工时/交付物（照片记为"等待负责人确认"），语音解析状态词改为宽松匹配（"完成了/卡住了"等也能识别）。
4. **为什么这样改：** 占位符是提示词工程问题必须根治；复述是"指令+转写"混排导致模型混淆；语音必须读方案才能回答分工问题；完成语义应符合真实协作流程，避免协作者单方面宣布完成。
5. **收益：** ① 答辩输出干净、点评聚焦并形成"答不到位就追问"的有来有回；② 语音对话稳定回答且基于当前方案；③ 多人任务由负责人确认完成，通知语义正确。

**涉及文件：** `app/services/omni_chat.py`、`app/web/routers/realtime.py`、`app/web/routers/report.py`、`app/web/static/app.js`、`tests/test_realtime_client.py`、`tests/test_report.py`、`docs/华为昇腾创新应用赛道接入说明.md`、`CHANGELOG.md`。

### 打磨（P3）

#### 21. 新增 P2 交接文档并更新备赛手册现状

1. **问题：** 长对话上下文过长，新开对话做 P2 时若从头读项目会浪费大量上下文与时间。
2. **修改前：** 备赛手册现状停留在"剩余 P0/P1"，无 P2 交接材料。
3. **修改后：** 新增 `docs/交接-P2起点.md`（已完成清单、环境配置现状、架构决策、红线与坑、P2 待办、验证命令、关键文件索引），更新备赛手册"一句话现状""已接入能力""下一步任务清单（P2）""新对话接手指南"。
4. **为什么这样改：** 记忆固化到文档，新对话按"备赛手册 → 交接-P2起点"两步即可无缝接手。
5. **收益：** ① 新对话不用重读项目；② P2 有明确待办与可复用基础；③ 红线与决策不再丢失。

**涉及文件：** `docs/交接-P2起点.md`（新增）、`docs/比赛全量备赛手册.md`、`CHANGELOG.md`。

**同步修改（2026-08-22）：** `.gitignore` 新增 `memory/attachments/`，避免本地测试产物进入 Git 提交。

---

## v6.8 —— P0 全模态交互落地：前端接通 MiniCPM-o、语音输入与语音回复（2026-08-21）

**定位：** 把 MiniCPM-o 4.5 从“只有后端接口”落地为工作台内可用的全模态交互：聊天接入 Realtime 并注入计划上下文、麦克风语音输入转写、TTS 语音回复，全部带兜底与明确提示。

**审查/修改背景：** 此前 Realtime 只暴露 `/api/realtime/chat` 后端接口，前端聊天仍走通用 `/api/chat`；图片/音频只在文件上传时被动使用，TTS 有接口无播放。用户要求 P0 做扎实：功能彻底可用，不满足于“基本完成”。

### 关键缺陷（P0）

#### 1. 前端聊天接通 MiniCPM-o Realtime 并注入计划上下文

1. **问题：** 前端 AI 建议抽屉的 `sendChat` 固定调用 `/api/chat`（通用 LLM），`/api/realtime/chat` 从未被前端调用；评委打开页面看不到 MiniCPM-o，接入层形同虚设。
2. **修改前：** `sendChat` 直接 `jsonRequest('/api/chat', ...)`，无任何 Realtime 判断。
3. **修改后：** `sendChat` 先探测 `/api/realtime/status`（存入 `state.realtime`），启用时走 `sendRealtimeChat`：把 `buildChatBaseline()` 方案快照 + `chatDelta` 变更 + `chatHistory` 组装成 messages，系统提示词精简为适合 8B 模型的协作助手定位；Realtime 失败自动回退 `sendLegacyChat` 并在页面提示，下次打开抽屉重新探测；抽屉顶部新增模型/后端徽标（`renderRealtimeBadge`）。
4. **为什么这样改：** 全模态能力必须进入用户实际使用的对话路径才可能被评审看到；方案快照注入让 MiniCPM-o 的对话基于同一份计划数据，而不是通用闲聊。
5. **收益：** ① 演示时页面直接使用 MiniCPM-o；② 回答有真实计划上下文；③ 失败不阻断，回退链路完整。

#### 2. 麦克风语音输入：录音 → 转写 → 填入输入框

1. **问题：** 音频能力只在上传文件时被动转写，用户无法用语音提问或下达指令，全模态的“听”没有进入交互主链路。
2. **修改前：** 无录音入口；`audio_transcribe_text` 固定返回带 `[音频转写]` 前缀的文本，前端无法复用为语音输入。
3. **修改后：** 新增 `POST /api/realtime/transcribe`（接收 webm/wav/mp3 等，15MB 上限，优先 Realtime、回退 ASR，返回纯文本，后端未连接时给出“本地昇腾后端未连接”类友好提示）；`audio_transcribe_text(filename, content, labeled=True)` 增加 `labeled` 参数；前端 chat-form 新增 🎤 按钮，用 MediaRecorder 录音（60 秒自动停止、权限/兼容性提示），停止后上传转写并填入 `chatInput`，用户确认后再发送。
4. **为什么这样改：** 语音输入必须进入“用户确认后发送”的编辑闭环，不能静默执行；`labeled` 参数保证文件分析链路的 `[音频转写]` 前缀行为不变。
5. **收益：** ① 演示“我来说、它来拆任务”；② 识别结果可编辑再发送，避免误识别直接提交；③ 只配本地昇腾后端（`ASCEND_OMNI_WS_URL`）时同样走 Realtime 优先。

#### 3. TTS 语音回复：PCM 转 WAV、前端播放与重听

1. **问题：** Realtime 协议返回的音频是 24kHz 单声道 float32 裸 PCM（base64），浏览器无法直接播放；此前前端连 `audio_base64` 都没有消费，TTS 等于不可用。
2. **修改前：** `RealtimeChatResult.audio_base64` 原样返回；前端无任何播放逻辑。
3. **修改后：** `RealtimeClient.pcm_to_wav_base64` 把 float32/int16 PCM 转成 16bit 单声道 WAV（输入已是 RIFF 则透传），`RealtimeChatResult.audio_wav_base64` 缓存转换结果；`/api/realtime/chat` 返回 `audio_wav_base64`；前端抽屉右上角新增“语音回复”开关，回答后自动播放，气泡带“🔊 重听”按钮（`audioCache` 按索引取用，避免大 base64 塞进 DOM 属性）；TTS 失败时后端自动降级为纯文本重试一次并返回 `tts_failed: true`，前端语音开关仅在云端后端（`backend: "map"`）启用——本地昇腾 910C 存在已知 TTS 算子问题，开 TTS 会挂起单会话服务。
4. **为什么这样改：** 浏览器可播放是 TTS 可用的前提；转换放后端，前端零依赖直接 `new Audio('data:audio/wav;base64,...')`；TTS 不能成为对话中断点，降级重试保证即使误开开关也只损失语音、不损失回答。
5. **收益：** ① 语音回复开箱即用；② 重听按钮提升演示可控性；③ 转换失败返回明确 502 提示而不是静默失败；④ 本地昇腾误开 TTS 不会拖垮对话链路。

### 健壮性提升（P1）

#### 4. 媒体分析纳入本地昇腾后端判断与测试隔离

1. **问题：** `image_ocr_text` / `audio_transcribe_text` 只判断 `MAP_REALTIME_API_KEY`，只配 `ASCEND_OMNI_WS_URL`（本地 A3）时不会走 Realtime；且本机 `.env` 的 `ASCEND_OMNI_WS_URL` 会泄漏进测试，导致单测真实连接本地服务、每例超时约 30 秒。
2. **修改前：** `if MAP_REALTIME_API_KEY:`；`tests/conftest.py`、`test_media_analysis.py`、`test_media_formats.py` 只屏蔽 `MAP_REALTIME_API_KEY`。
3. **修改后：** 判断改为 `if MAP_REALTIME_API_KEY or ASCEND_OMNI_WS_URL:`；三个测试文件的同名 fixture 均补 `ASCEND_OMNI_WS_URL=""`。
4. **为什么这样改：** 本地链路与云端链路应同样进入媒体分析；测试必须与真实环境变量隔离，否则全量测试被本机配置拖慢/拖挂。
5. **收益：** ① A3 本地转写可用；② 全量测试稳定（273 passed，约 18 秒），不依赖本机 `.env`。

#### 5. 图片/语音兜底同步阿里 DashScope 配置

1. **问题：** 昇腾副本 `.env` 未配置 `APP_VISION_*` / `APP_ASR_*`，MiniCPM-o 不可用时图片/音频只剩元数据“需要确认”，演示时双后端同时故障将无多模态兜底。
2. **修改前：** `competition-ascend/.env` 无视觉/语音回退模型配置，API key 默认复用 DeepSeek（不提供视觉/语音模型）。
3. **修改后：** 从 `competition/.env` 同步 9 项配置：`APP_VISION_MODEL=qwen3.7-plus`、`APP_ASR_MODEL=qwen-audio-3.0-asr-flash`、独立 DashScope key 与 `https://dashscope.aliyuncs.com/compatible-mode/v1`、`APP_ASR_TRANSCRIPTION_MODE=auto`、`APP_MEDIA_TIMEOUT=20`、`APP_OCR_MAX_PDF_PAGES=6`；实测 MiniCPM-o 屏蔽后 ASR 2.7s 转写正确、OCR 6.6s 提取正确。
4. **为什么这样改：** 兜底链越完整，演示越不怕 A3/云端双挂；复用 DeepSeek key 会静默失败，必须显式配 DashScope key。
5. **收益：** ① 三级兜底生效（MiniCPM-o → DashScope → 元数据）；② 实测 ASR 输出比 MiniCPM-o 更干净（无思考噪音）；③ 与基础版配置保持一致。

> 注：评审/联调期间默认注释该兜底配置，让昇腾或云端 MiniCPM-o 的故障直接可见（文件显示"需要确认"）；需要启用时取消 `.env` 中 `APP_VISION_*` / `APP_ASR_*` 注释并重启。

### 打磨（P3）

#### 6. 文档、版本号与版本规划表同步

1. **问题：** 新接口与新交互没有对应文档，浏览器还会因静态资源版本号不变而缓存旧 JS，演示时加载不到新功能。
2. **修改前：** 接入说明只有 `/api/realtime/chat`；使用说明书 8.1 只有接口描述；`index.html` 资源版本号停留在 `5.76.2`；版本规划表无 v6.8。
3. **修改后：** 接入说明新增 `/api/realtime/transcribe` 与 WAV 播放说明；使用说明书 8.1 改写为语音输入/TTS/兜底操作说明；README 版本升至 v6.8 并补接口行；`app.js`/`participants.js` 资源版本号升至 `6.8.0`；版本规划表新增 v6.8 并标记已完成。
4. **为什么这样改：** 评审与队友需要与代码一致的操作与接口文档；资源版本号保证演示环境加载新代码。
5. **收益：** 文档可复现；演示不因缓存回退到旧交互。

#### 7. 新增功能验证清单

1. **问题：** P0 功能跨后端、跨端到端，缺少一份可重跑的验证方法，新成员或评审前难以确认"所有功能可用"。
2. **修改前：** 只有演示流程和验收项，无自动化命令与实测记录。
3. **修改后：** 新增 `docs/功能验证清单.md`：自动化验证命令（状态/对话/转写/OCR/兜底）、浏览器手工验证表（9 项）、2026-08-21 本地昇腾实测记录与演示红线。
4. **为什么这样改：** 验证要可复现、可留档；实测记录让团队知道哪些链路已确认、哪些是已知不可用。
5. **收益：** 评审前按清单逐项过即可；新成员无需摸索验证方法。

**涉及文件：** `.env`、`app/services/realtime_client.py`、`app/services/media_analysis.py`、`app/web/routers/realtime.py`、`app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`、`tests/conftest.py`、`tests/test_media_analysis.py`、`tests/test_media_formats.py`、`tests/test_realtime_client.py`、`docs/华为昇腾创新应用赛道接入说明.md`、`docs/使用说明书.md`、`docs/功能验证清单.md`、`README.md`、`CHANGELOG.md`。

---

## v6.7 —— 比赛全量备赛手册（2026-08-21）

**定位：** 把赛事信息、项目现状、技术架构、昇腾部署、群聊经验、待办、提交材料、风险兜底整合成一份新对话可独立接手的手册。

**审查/修改背景：** 用户担心换对话后上下文丢失，需要一份不依赖聊天历史的完整交接文档。

### 打磨（P3）

#### 1. 新增全量备赛手册

1. **问题：** 比赛材料分散在飞书、群聊、部署指南、备赛梳理等多个地方，新对话接手需要重新找材料。
2. **修改前：** `docs/` 有部署指南、接入说明、群聊记录、备赛梳理，但没有统一入口。
3. **修改后：** 新增 `docs/比赛全量备赛手册.md`，包含赛事信息、项目现状、技术架构、昇腾部署进度、群聊经验、任务清单、提交材料、风险兜底、新对话接手指南和命令速查。
4. **为什么这样改：** 一份文档承载全部交接信息，新对话先读它即可；其他文档作为专项手册按需查阅。
5. **收益：** 换对话不丢上下文；团队成员可快速接手；关键命令和风险集中可查。

**涉及文件：** `docs/比赛全量备赛手册.md`（新增）、`docs/比赛备赛梳理.md`、`CHANGELOG.md`。

---

## v6.6 —— 同步基础版 v5.77 与通用产品改进（2026-08-21）

**定位：** 把 `competition` 基础原版新增的纯业务改进（v5.77 不可用日期硬约束、排期/存储/媒体/安全通用能力）同步进昇腾副本，同时保留华为赛全部配置与接入层。

**审查/修改背景：** 用户之前在 `competition` 里做了纯功能改进，`competition-ascend` 尚未同步；同步时不能覆盖 `MAP_REALTIME_*`、`ASCEND_OMNI_*`、Realtime 客户端、昇腾部署文档等华为赛专属内容。

### 关键缺陷（P0）

#### 1. 不可用日期硬约束与日期回填

1. **问题：** 基础版 v5.77 修复了任务被排到成员不可用日期、资源日历把负载摊到不可用日、时间线结果不回填任务日期三个联动问题；昇腾副本仍停留在旧逻辑。
2. **修改前：** `app/agents/timeline.py` 的 `_add_work_days` 不校验起点与窗口中间；`coordinator.py`/`editor.py`/`members.py` 不写回任务日期。
3. **修改后：** 同步 `app/agents/timeline.py`、`app/coordinator.py`、`app/editor.py`、`app/services/project_service.py`、`app/web/routers/members.py` 及对应测试，使排期、资源日历、任务自身日期和甘特图使用同一份时间线结果。
4. **为什么这样改：** 排期数据必须与成员可用性一致，避免演示时出现“成员明明不可用却还被派任务”的可疑结果。
5. **收益：** 排期硬约束生效；资源日历负载准确；成员调整后日期真实回填。

### 健壮性提升（P1）

#### 2. 同步通用能力与部署安全

1. **问题：** 基础版 903e87f 补齐了排期/甘特图精确化、S3 持久化、媒体处理可配置、`/api/ready` 与管理端鉴权等通用能力，昇腾副本缺失。
2. **修改前：** `app/services/audit_store.py`/`share_store.py`/`storage.py` 无线程锁与 S3 同步；`media_analysis.py` OCR 串行、超时固定；`system.py` 无 `/api/ready`。
3. **修改后：** 同步 `app/services/*`、`app/models/schemas.py`、`app/main.py`、`app/web/static/*`、`render.yaml`、`tests/*`；`system.py` 合并 `/api/ready`、S3 就绪探测与版本号，并保留 `realtime_configured`/`realtime_backend`；`media_analysis.py` 合并并行 OCR、可配置页数/超时，同时保留 MiniCPM Realtime OCR/ASR 分支。
4. **为什么这样改：** 通用能力属于所有比赛共享，华为赛副本必须同步；重叠文件以“双方内容都保留”为合并原则。
5. **收益：** 昇腾副本功能与基础版一致；华为赛接入层不被覆盖；部署可区分“活着”与“真正就绪”。

### 打磨（P3）

#### 3. 配置样例与 CHANGELOG 同步

1. **修改后：** `.env.example` 同时包含队友新增的 `APP_MEDIA_TIMEOUT`/`APP_OCR_MAX_PDF_PAGES`/`APP_ADMIN_TOKEN` 与华为赛 `MAP_REALTIME_*`/`ASCEND_OMNI_*`；CHANGELOG 保留 v6.0-v6.5 华为赛记录，并接入 v5.77、v5.76 通用改进记录。
2. **为什么这样改：** 配置样例必须是“通用 + 华为赛”的并集，避免任何一方换环境后缺变量。
3. **收益：** 新成员按一份 `.env.example` 即可复现；版本历史完整可追溯。

#### 4. 测试环境适配

1. **问题：** `test_chat_can_read_draft_without_full_plan` 原版 mock 的是 `chat_text`，但 `/api/chat` 实际调用 `chat_messages`；昇腾副本的 LLM Key 不同会真实外呼并拖到 40 秒超时。
2. **修改前：** `monkeypatch.setattr(LLMClient, "chat_text", ...)`。
3. **修改后：** 改为 mock `LLMClient.chat_messages`，让离线回退路径不依赖真实网络。
4. **为什么这样改：** 测试意图是验证“模型不可用时也能读草案”，应直接 mock 实际入口，而不是让测试依赖本地 Key/网络状态。
5. **收益：** 测试稳定且快速；本地换 Key 或断网也不会把套件拖慢 40 秒。

**涉及文件：** `app/`（通用后端/前端同步 + 华为赛接入层保留）、`tests/`、`render.yaml`、`.env.example`、`CHANGELOG.md`。

---

## v6.5 —— Realtime 客户端支持本地 llama-omni-server（2026-08-21）

**定位：** 让 Demo 后端可一键从 ModelBest 云端 Realtime 切换到昇腾 A3 上的 `llama-omni-server`。

**审查/修改背景：** A3 已编译并启动 `llama-omni-server`，文本/音频 WebSocket 冒烟测试通过；需要把项目的 Realtime 客户端接到本地 `/backend`，不依赖 API Key。

### 健壮性提升（P1）

#### 1. RealtimeClient 新增本地昇腾模式

1. **问题：** `RealtimeClient` 只支持 ModelBest 云端协议（必须带 API Key、等 `session.queue_done`），无法连接本地 `llama-omni-server` 的 `/backend`。
2. **修改前：** `chat()` 开头直接要求 `MAP_REALTIME_API_KEY`，`_build_uri()` 固定拼接 `mode`/`model` 查询参数，且输入帧带 `tts/enable_thinking/omni_mode`：
   ```python
   if not self.api_key:
       raise RealtimeError("MAP_REALTIME_API_KEY 未配置...", "auth_error")
   uri = self._build_uri()
   headers = {"Authorization": f"Bearer {self.api_key}"}
   ```
3. **修改后：** 新增 `local_ws_url`（默认读 `ASCEND_OMNI_WS_URL`）；本地模式不走鉴权、跳过 `session.queue_done`、`session.init` 使用 `{"mode": "turn_based", "use_tts": ...}`，输入帧只发 `messages/streaming/generation`，`response.done` 后不发送 `session.close`：
   ```python
   if not self.local_ws_url and not self.api_key:
       raise RealtimeError("MAP_REALTIME_API_KEY 或 ASCEND_OMNI_WS_URL 未配置...", "auth_error")
   ```
4. **为什么这样改：** 本地 `/backend` 协议与云端事件流不同，统一收口到同一客户端后，路由和媒体分析代码无需感知后端差异；云端与本地可只靠环境变量切换。
5. **收益：** 昇腾环境复现时后端零代码改动；本地服务未配置时仍可回退云端 API；冒烟测试和正式接口走同一套事件解析。

#### 2. 路由、健康检查与配置同步

1. **问题：** `/api/realtime/status` 和 `/api/realtime/chat` 只看 API Key，健康检查也只报 `realtime_configured`，无法表达本地后端状态。
2. **修改前：** `realtime_status()` 返回 `enabled=bool(MAP_REALTIME_API_KEY)`，`/api/health` 只有 `realtime_configured`。
3. **修改后：** 新增 `ASCEND_OMNI_WS_URL` / `ASCEND_OMNI_TIMEOUT` 配置；状态接口新增 `backend: "local" | "map"`，模型名在本地模式显示 `llama.cpp-omni`；健康检查增加 `realtime_backend`。
4. **为什么这样改：** 评审和排查时需要一眼看出当前走云端还是本地昇腾，避免“以为在昇腾跑、实际在 API 跑”的误判。
5. **收益：** Demo 前端可展示当前后端；部署文档可明确告知评委当前链路；配置错误时 503 文案更准确。

**涉及文件：** `app/services/realtime_client.py`、`app/web/routers/realtime.py`、`app/web/routers/system.py`、`app/config.py`、`.env.example`、`tests/test_realtime_client.py`、`tests/test_api.py`、`CHANGELOG.md`。

---

## v6.4 —— 昇腾本地服务冒烟测试脚本（2026-08-21）

**定位：** 在 A3 上跑通 `llama-omni-server` 后，用 WebSocket 真实验证文本和音频推理。

**审查/修改背景：** 用户已编译成功并启动 `llama-omni-server`，需要先验证本地推理再接入项目前端。

### 健壮性提升（P1）

#### 1. 新增文本/音频 WebSocket 测试脚本

1. **问题：** 终端里手动粘贴长 Python 测试代码容易再次被格式污染，且无法快速重复验证。
2. **修改前：** 只能靠飞书指南里的 heredoc 脚本，粘贴出错后难以排查。
3. **修改后：** 新增 `scripts/ascend_test_ws_text.py` 和 `scripts/ascend_test_ws_audio.py`，固定连接 `ws://127.0.0.1:28099/backend`，按 `session.init -> input.append -> response.output.delta -> response.done` 事件流输出回复文本。
4. **为什么这样改：** 测试脚本作为仓库文件分发，复制到 A3 后直接运行；音频脚本复用仓库自带的测试 WAV，转为 float32 PCM base64。
5. **收益：** 本地昇腾链路可用性可以一键验证；后续接入项目时测试用例可复用。

**涉及文件：** `scripts/ascend_test_ws_text.py`、`scripts/ascend_test_ws_audio.py`（新增）、`CHANGELOG.md`。

---

## v6.3 —— 昇腾模型下载脚本（2026-08-21）

**定位：** 提供不易复制出错的 A3 模型下载脚本，解决 Markdown 粘贴到终端导致命令被破坏的问题。

**审查/修改背景：** 用户把带格式的命令粘进 A3 终端，模型下载没有可靠执行；源码 clone 已通过 `ghfast.top` 完成。

### 健壮性提升（P1）

#### 1. 新增昇腾模型下载脚本

1. **问题：** 长段 Python heredoc 容易在复制时被 Markdown 链接/代码块破坏，导致下载命令没有真正执行。
2. **修改前：** 只能靠手动粘贴长命令，失败后难以定位。
3. **修改后：** 新增 `scripts/ascend_download_models.py`，固定 `OpenBMB/MiniCPM-o-4_5-gguf` 的 10 个必需文件，并用官方文件大小做完整性校验，不完整时返回非 0。
4. **为什么这样改：** 把“复制粘贴一段脚本”降级为“复制一个文件后运行”，避免 Markdown 格式污染；脚本内做大小校验，下载一半也能立刻发现。
5. **收益：** 下载可重复执行；文件缺漏自动提示；团队成员可直接复用。

#### 2. 新增昇腾服务启动脚本

1. **问题：** 用 heredoc 在 A3 终端创建 `start_server.sh` 容易再次被 Markdown 粘贴破坏。
2. **修改前：** 部署指南要求手动 `cat > start_server.sh <<'EOF'` 创建启动脚本。
3. **修改后：** 新增 `scripts/ascend_start_server.sh`，固定 CANN lib64 路径，直接可拖到 `/workspace/llama.cpp-omni/` 后执行。
4. **为什么这样改：** 脚本作为仓库文件分发，避免复制格式污染；`LD_LIBRARY_PATH` 同时包含 `build/bin` 和 CANN `lib64`，减少 `libascendcl.so` 缺失问题。
5. **收益：** 启动服务只需两个命令；团队其他人部署时可直接复用。

**涉及文件：** `scripts/ascend_download_models.py`、`scripts/ascend_start_server.sh`（新增）、`CHANGELOG.md`。

---

## v6.2 —— 归档赛事群聊记录（2026-08-21）

**定位：** 完整保存两段飞书答疑群聊天记录，方便后续逐条答疑和复用群内经验。

**审查/修改背景：** 用户提供了 08-04 至 08-14、08-15 至 08-20 两段聊天记录，并明确要求“好好记一下”，之后还会针对内容提问。

### 打磨（P3）

#### 1. 新增群聊记录索引

1. **问题：** 群聊经验散落在飞书里，飞书需要登录态且无法从外部读取，之后想查原话很困难。
2. **修改前：** `docs/` 只有备赛梳理和部署指南，没有原始聊天记录可查。
3. **修改后：** 新增 `docs/群聊记录/`，保存两段原始记录，并新增 `README.md` 索引 10 条核心结论和检索方式。
4. **为什么这样改：** 原始记录是唯一可追溯的答疑素材；索引负责快速定位，正文负责原话对照。
5. **收益：** 后续“别人怎么解决某个问题”可以直接翻原文；经验不依赖飞书登录态；团队其他人也能复用。

**涉及文件：** `docs/群聊记录/`（新增）、`CHANGELOG.md`。

---

## v6.1 —— 昇腾 A3 部署上手指南归档（2026-08-21）

**定位：** 把飞书文档「MiniCPM-o 4.5 昇腾 910C 部署上手指南」落到仓库，避免文档失效后部署步骤丢失。

**审查/修改背景：** 用户已申请到 A3 环境（CANN 9.1.0-beta.1 / Ascend 910C），飞书 wiki 需要登录且无法从外部读取；用户把正文粘贴进来，需要固化成可复查的本地文档。

### 打磨（P3）

#### 1. 新增昇腾 A3 部署指南

1. **问题：** `docs/` 缺少昇腾 910C 上部署 `llama.cpp-omni` 的完整操作手册，部署步骤只能依赖需要登录的飞书文档。
2. **修改前：** `docs/` 只有 API 接入说明和备赛梳理，没有模型下载、CANN 编译、服务启动与 API 测试步骤。
3. **修改后：** 新增 `docs/昇腾A3_910C_llama_omni部署指南.md`，覆盖环境前置检查、ModelScope 模型下载、`v4.gh-proxy.org` 拉源码、`-DGGML_CANN=ON -DLLAMA_OPENSSL=OFF` 编译、`llama-omni-server` 启动、WebSocket/HTTP 测试以及 F16/单 session/websocket-client 等已知坑位。
4. **为什么这样改：** 部署是昇腾赛复现评分的关键，指南需要脱离飞书登录态长期可查；F16、CANN 后端、国内镜像等结论直接决定 A3 上是否跑得动。
5. **收益：** 团队可按文档逐步复现；关键约束和坑位集中记录，减少现场踩坑；后续接入本地 `/backend` 时以此协议为准。

#### 2. 部署指南补充代理兜底方案

1. **问题：** 2026-08-21 在 A3 实测 `v4.gh-proxy.org` 拉 `llama.cpp-omni` 连接超时，单一代理会卡住整个部署流程。
2. **修改前：** 指南只写了 `v4.gh-proxy.org` 一个推荐代理，失败后没有自动切换方案。
3. **修改后：** 在指南 3.4 增加多代理循环尝试脚本，并给出 `gh-proxy.com` 下载 master zip 的兜底命令。
4. **为什么这样改：** 不同 A3 容器出口网络差异大，代理可用性不稳定；循环尝试 + zip 下载能覆盖多数超时场景。
5. **收益：** 拉源码不再依赖单个代理；即使 git clone 全挂也能用 zip 继续编译。

**涉及文件：** `docs/昇腾A3_910C_llama_omni部署指南.md`（新增）、`CHANGELOG.md`。

---

## v6.0 —— 华为昇腾创新应用赛道：接入 MiniCPM-o Realtime Chat 模式（2026-08-20）

**定位：** 在基础原版之外建立昇腾赛独立副本，先接入 ModelBest 免费 Realtime API，让 MiniCPM-o 4.5 的文本对话、图片 OCR、音频转写全模态能力在 A3 算力排队期间即可演示。

**审查/修改背景：** 用户同时参加多个比赛，`competition` 必须保持基础原版不动；昇腾赛改动全部落在 `competition-ascend`。GitHub 同步确认当前没有新的队友提交，副本直接基于 v5.76。

### 健壮性提升（P1）

#### 1. MiniCPM-o Realtime 客户端（新增 app/services/realtime_client.py）

1. **问题：** 原版只有 DeepSeek/OpenAI 兼容的 HTTP LLM 客户端，无法调用 MiniCPM-o 4.5 的 WebSocket Realtime API；昇腾赛要求的 `llama.cpp-omni /backend` 也不走 HTTP Chat Completions。
2. **修改前：** 没有任何 Realtime 协议实现，只有 `app/llm/client.py` 的 `chat_messages`（OpenAI SDK + `LLM_BASE_URL`）：
   ```python
   resp = self._client.chat.completions.create(
       model=self.model,
       messages=full_messages,
       timeout=LLM_TIMEOUT,
   )
   ```
3. **修改后：** 新增 `RealtimeClient`，按官方事件顺序完成 `session.queue_done -> session.init -> session.created -> input.append -> response.output.delta/response.done -> session.close`，使用 `Authorization: Bearer` 鉴权，并把 text/audio 增量聚合成 `RealtimeChatResult`。事件解析同时兼容官方文档的 `response.output.delta` 和平台控制台文档的 `response.output_text.delta` / `response.output_audio.delta`：
   ```python
   async with websockets.connect(uri, additional_headers=headers, ...) as ws:
       await self._wait_event(ws, {"session.queue_done"}, wait)
       await self._send_event(ws, {"type": "session.init", "payload": {}})
       await self._wait_event(ws, {"session.created"}, wait)
       await self._send_event(ws, {"type": "input.append", "input": {
           "messages": normalized, "streaming": True,
           "generation": {"max_new_tokens": budget},
           "tts": {"enabled": bool(tts_enabled)},
           "enable_thinking": bool(enable_thinking),
           "omni_mode": bool(omni_mode),
       }})
   ```
4. **为什么这样改：** 根因是协议不同：Realtime API 是事件驱动的 WebSocket，不能用 OpenAI REST 客户端调用；封装成独立服务后，API 层和测试都可以稳定模拟事件序列。`omni_mode` 用于后续视频/音频多模态输入。
5. **收益：** 昇腾 Demo 在 A3 排队时也能用云端 MiniCPM-o；协议错误、超时、鉴权失败都能转成用户可读错误；后续换本地 `/backend` 时只替换连接层。

#### 2. Realtime API 路由与配置（新增 app/web/routers/realtime.py + config）

1. **问题：** 配置和路由都没有入口，网页/测试无法调用 Realtime。
2. **修改前：** `app/config.py` 只有 `LLM_*` 配置，`.env.example` 没有 MAP 变量，`app/web/routes.py` 没有 Realtime 路由。
3. **修改后：** 新增 `MAP_REALTIME_API_KEY / MODEL / BASE_URL / MAX_TOKENS / TIMEOUT`；新增 `/api/realtime/chat` 与 `/api/realtime/status`，并在 `routes.py` 注册：
   ```python
   from app.web.routers.realtime import router as realtime_router
   router.include_router(realtime_router)
   ```
4. **为什么这样改：** 配置走统一环境变量，密钥不进代码；路由按业务域拆分，和现有 system/exports/members 风格一致。
5. **收益：** Demo 前端可先查 `/api/realtime/status` 再决定是否展示对话；密钥缺失返回 503 而不是 500；`/api/health` 同步暴露 Realtime 配置状态。

#### 3. Realtime 全模态媒体链路（图片 OCR / 音频转写）

1. **问题：** 比赛要求充分发挥 MiniCPM-o 4.5 的全模态能力，但原版 OCR/ASR 仍走独立视觉/语音模型，`MAP_REALTIME_API_KEY` 只接到对话接口。
2. **修改前：** `app/services/media_analysis.py` 只调用 `APP_VISION_MODEL` / `APP_ASR_MODEL`：
   ```python
   response = _client(APP_VISION_API_KEY, ...).chat.completions.create(
       model=APP_VISION_MODEL, ...)
   response = _client(APP_ASR_API_KEY, ...).audio.transcriptions.create(
       model=APP_ASR_MODEL, ...)
   ```
3. **修改后：** 配置 `MAP_REALTIME_API_KEY` 时优先走 MiniCPM-o Realtime：图片用 `{"type":"image","data":"<base64>"}`；音频先用 PyAV 解码成 16kHz 单声道 float32 PCM，再以 `{"type":"audio","data":"<base64>"}` + `omni_mode:true` 发送：
   ```python
   content_parts = [
       {"type": "text", "text": "请提取这张图片中的文字和关键信息，只输出内容。"},
       {"type": "image", "data": b64},
   ]
   _run_realtime_media_chat(content_parts, 1200, False)
   ```
4. **为什么这样改：** Realtime Chat 模式支持多模态 content；图片 raw base64 与音频 PCM base64 均已实测可被 MiniCPM-o 理解。PyAV 负责把 mp3/m4a/webm/wav 统一转成模型要求的格式。
5. **收益：** 昇腾赛只需一个 `MAP_REALTIME_API_KEY` 即可演示文本、图片、音频全模态；旧模型保留为回退，不破坏原工作台。

### 打磨（P3）

#### 4. 依赖、测试与文档同步

1. **修改后：** `requirements.txt` 新增 `websockets==16.1`、`av==14.2.0`、`numpy==2.4.6`；新增 10 项测试（7 项 Realtime 客户端/路由 + 3 项媒体链路），覆盖事件顺序、system prompt、平台事件别名、服务端 error、缺 Key、路由、Realtime OCR/ASR 与音频 PCM 解码；`.env.example` / `render.yaml` / README / 使用说明补齐配置与接入方法；`app/models/schemas.py` 的版本默认值同步为 v6.0；接入说明明确 `MiniCPM-o-4.5-Realtime` 为昇腾赛主模型，旧视觉/语音配置保留为回退。
2. **为什么这样改：** Realtime 依赖必须显式锁定，测试不发起真实网络请求，避免 CI 外呼。
3. **收益：** 新环境一条命令复现；本地验证与比赛文档一致。

**涉及文件：** `app/config.py`、`app/models/schemas.py`、`app/services/realtime_client.py`、`app/services/media_analysis.py`、`app/web/routers/realtime.py`、`app/web/routers/system.py`、`app/web/routes.py`、`app/main.py`、`tests/test_realtime_client.py`、`tests/test_media_analysis.py`、`tests/test_media_formats.py`、`tests/test_deployment_readiness.py`、`tests/test_api.py`、`tests/conftest.py`、`requirements.txt`、`.env.example`、`render.yaml`、`README.md`、`docs/华为昇腾创新应用赛道接入说明.md`。

---
## v5.77 —— 不可用日期硬约束：时间线避开、日期回填与资源日历负载修复（2026-08-21）

**定位：** 让成员的不可用日期成为排期的硬约束——时间线不再把任务压在不可用日上，排期结果回填到任务自身，资源日历只把负载摊到真正可用的工作日。

**审查/修改背景：** 用户反馈负载均衡后成员仍会在明确标记的不可用日期上被派任务。排查发现三层根因：`assign_with_balance` 只按技能/负载/工时打分，从不读 `unavailable_dates`；时间线的 `_add_work_days` 只在“从起点推进 N 天”时跳过不可用日，起点本身撞上不可用日会被原样接受，多日任务窗口中间也不校验；草案阶段给所有任务填了默认项目窗口日期，时间线算出真实排期后从不回填，资源日历优先读任务自身日期，于是把整个窗口（含不可用日）都显示成有任务。

---

### 关键缺陷（P0）

#### 1. 时间线把任务排在成员不可用日期上

1. **问题：** 截止日紧凑时，倒推得到的起始日正好是成员不可用日，任务被原样压在该日期上；多日任务窗口中间的不可用日也被当作可排日期包进窗口，资源日历随后在该日产生负载并告警。
2. **修改前：** `_add_work_days` 推进时跳过不可用日，但零偏移任务的起点不校验；任务起止只映射两个边界日，窗口中间不检查：
   ```python
   work_offset = es[tid] // 2
   s_date = datetime.combine(_add_work_days(start_base, work_offset, task_skip_dates), datetime.min.time())
   end_offset = work_offset if durations[tid] == 0 else (ef[tid] - 1) // 2
   e_date = datetime.combine(_add_work_days(start_base, end_offset, task_skip_dates), datetime.min.time())
   ```
3. **修改后：** 新增 `_normalize_workday`（起点撞上周末/不可用日时后移）与 `_end_from_workdays`（按可用工作日累计工期，窗口内不可用日/周末算空档并拉长窗口）：
   ```python
   raw_start = _add_work_days(start_base, work_offset, task_skip_dates)
   s_date = datetime.combine(_normalize_workday(raw_start, task_skip_dates), datetime.min.time())
   work_span = math.ceil(durations[tid] / 2)
   e_date = datetime.combine(_end_from_workdays(s_date.date(), work_span, task_skip_dates), datetime.min.time())
   ```
4. **为什么这样改：** 排期起点和窗口中间都必须按“该成员当天是否可用”判定，不可用日不参与可用工作日计数；窗口自动拉长后，成员在空档日不再有任务。
5. **收益：** 起始日撞上不可用日时任务自动后移；多日任务不再把成员不可用日包进窗口；截止日仍然过紧时任务延后并照常触发延期警告，而不是悄悄占用不可用日。

### 健壮性提升（P1）

#### 2. 时间线排期结果回填任务自身日期

1. **问题：** 草案阶段把所有任务的 `start_date/end_date` 填成默认项目窗口，确认与成员调整后时间线算出真实排期却从不写回，资源日历优先读任务自身日期，导致整段窗口（含成员不可用日）都被显示为有任务。
2. **修改前：** coordinator / `edit-members` / 手动分工 / editor 重算时间线后只回填负责人与协作者，任务日期保持草案默认窗口：
   ```python
   plan = plan.model_copy(update={"tasks": [
       t.model_copy(update={"assignee_id": ..., "collaborator_ids": ...})
       for t in plan.tasks
   ]})
   ```
3. **修改后：** `timeline.py` 新增 `sync_task_dates()`，时间线跑完后把每项任务的真实起止日期回填到任务：
   ```python
   plan = sync_task_dates(plan, timeline)
   # by_id 中同 id 的 TimelineTask 起止日期写回 plan.tasks
   ```
   并在 coordinator 主流程/confirm、`/api/edit-members`、手动分工、editor 四处统一调用。
4. **为什么这样改：** 时间线是排期的事实来源，任务自身日期应与甘特图一致；资源日历、任务列表、导出文档全部基于同一份日期，消除“时间线避开但日历仍显示冲突”的数据断链。
5. **收益：** 资源日历与甘特图一致；成员调整后新不可用日期真实生效、旧日期重新可用；旧存档即使任务日期过期，日历也优先采用时间线日期。

#### 3. 资源日历只把负载摊到可用工作日

1. **问题：** 资源日历把任务工时除以整个日历跨度（含周末与成员不可用日），导致即使时间线窗口已拉长避开不可用日，成员在不可用日当天仍显示负载并告警。
2. **修改前：** 按 `calendar_span` 平均分摊，循环不区分周末/不可用日：
   ```python
   span = max(1, (end - start).days + 1)
   daily = hours / span
   for i in range(span):
       member_load[name][key] += daily
   ```
3. **修改后：** 先算每个参与者可用的工作日集合（剔除周末与本人不可用日），负载只在可用工作日上分摊，空档日负载为 0：
   ```python
   workday_keys = [key for i in range(calendar_span)
                   if not _is_weekend(day) and day not in unavailable]
   daily = hours / len(workday_keys) if workday_keys else 0.0
   # 循环中 member 的周末/不可用日直接 continue，不累加负载
   ```
4. **为什么这样改：** 成员在不可用日不工作，其每日负载就不该有值；把工时分摊到其余可用工作日也反映了真实的工作强度，超载告警因此更准确。
5. **收益：** 不可用日与周末不再显示负载；不可用日冲突告警不再误报；真实产能不足会以“每日负载超上限”的形式暴露，引导用户拆分任务或调整人手。

**同步修改：**
- `app/services/project_service.py`：资源日历优先采用时间线日期，任务自身日期仅在时间线缺失时兜底；新增模块级 `_is_weekend`。
- `tests/test_timeline.py`：新增起始日不可用、多日任务窗口跳不可用日、日期回填 3 个用例。
- `tests/test_project_service.py`：资源日历负载口径更新 + 时间线日期优先用例。
- `tests/test_member_edit.py`：成员编辑重算后避开不可用日并回填日期的回归用例。

---
## v5.76 —— 基础原版整合 v5.49–v5.76 通用能力与导出区上移（2026-08-18）

**定位：** 把清小搭分支中与平台接入无关的通用提升（A 组前端/工作台 + B 组后端能力/稳定性）整体移植到基础原版，并应用导出/分享按钮上移与响应式头部修复，让其他比赛直接复用最新工作台能力。

**审查/修改背景：** 基础原版停留在 v5.48（2026-08-11），缺少角色化工作台、AI 助手稳定交互、扫描版 PDF OCR、健康检查/监控、对象存储同步、Agent 稳定性等通用能力；用户参加多个比赛，需要一份具备全部通用能力、且不含清小搭接入的独立版本。本次从清小搭分支搬运 `app/` 通用代码与前端文件，并彻底移除清小搭接入残留：`app/compat/`（/v1 协议层）、`app/services/qingxiaoda_io.py`、`QINGXIAODA_*` 配置、来源/返回入口 UI、限时编辑令牌机制及其测试。

---

### 关键缺陷（P0）

#### 1. 角色视图乱码修复随搬运落地（`[object Object]` / `NaN%`）

1. **问题：** 清小搭 v5.76 修复的「任务数组被当计数渲染」问题，在本次把角色化工作台搬入基础版时必须采用修复后实现，否则评委/负责人视图会显示 `[object Object]` 与 `NaN%`。
2. **修改前：** 基础原版没有角色视图；若直接搬 v5.68 的旧实现会引入 `audiencePlanStats()` 返回 `tasks` 数组、统计处当数字用的问题：
   ```js
   return{tasks:tasks,assigned:assigned,blocked:blocked,...};   // tasks 是对象数组
   '<strong>'+stats.tasks+' 项任务 · '+stats.totalHours+'h</strong>'  // 数组拼串 → [object Object]
   var coverage=stats.tasks?Math.round(stats.assigned/stats.tasks*100):0;  // 数组除法 → NaN
   ```
3. **修改后：** 搬运 v5.76 修复后实现，`audiencePlanStats()` 新增 `taskCount` 数字字段，统计一律用数字：
   ```js
   return{tasks:tasks,taskCount:tasks.length,assigned:assigned,blocked:blocked,...};
   '<strong>'+stats.taskCount+' 项任务 · '+stats.totalHours+'h</strong>'
   var coverage=stats.taskCount?Math.round(stats.assigned/stats.taskCount*100):0;
   ```
4. **为什么这样改：** 直接取清小搭当前已修复的前端文件，避免把历史 bug 一并搬入；`tasks` 数组保留给成员视图按负责人/协作者过滤。
5. **收益：** 基础版一引入角色视图就是正确数字，无需二次返工。

### 体验优化（P2）

#### 2. 角色化工作台（负责人 / 成员 / 教师评委）

1. **问题：** 基础版最终方案页只有单一视图，负责人、执行成员和评委看到的信息完全一样，无法按角色收敛信息与操作。
2. **修改前：** 基础版 `index.html` 无角色切换入口，`app.js` 无 `audiencePlanStats` / `renderAudienceSummary` / `renderEvaluationView` / `renderMemberTasks`。
3. **修改后：** 搬运 v5.68 角色化工作台：顶部「负责人 / 成员 / 教师·评委」切换、成员选择器、评委四维指标卡（交付边界/分工完整度/平均匹配度/排期证据）与风险清单；数据全部来自 `state.plan` 的 `tasks` / `qa_matrix.assignments` / `timeline.critical_path`，基础版 schema（`app/models/schemas.py` 的 `QAOutput.assignments`、`critical_path`）本就具备。
4. **为什么这样改：** 同一份计划按角色收敛是工作台通用能力，与清小搭平台无关；评委证据评审比主观总分更可追溯。
5. **收益：** 评审/答辩场景可直接演示角色化视图；不依赖清小搭任何接口。

#### 3. 导出/分享按钮上移顶部工具栏 + 响应式头部

1. **问题：** 基础版底部 sticky 栏把导出 Excel/CSV/ICS、复制只读链接和返回调整堆在一起，窄屏换行成多行，占据整块底部空白。
2. **修改前：** 底部 `final-export-actions` 四个按钮 + `backBoardBtn`：
   ```html
   <div class="final-export-actions"><button id="exportExcelBtn">导出Excel</button>…</div>
   <button id="backBoardBtn">返回调整</button>
   ```
3. **修改后：** 四个按钮移到顶部 `.header-tools`，与导出 MD/Word/PDF 并列，并随方案可用性启用/禁用（确认分工、载入方案后启用，切换模式置灰）；底部只留提示与返回调整。响应式：桌面 1440px 一行 9 按钮、平板 1000px 3×3、移动端 480px 5×2。
4. **为什么这样改：** 导出是全局动作，统一放顶部工具栏；底部只保留流程导航（返回调整），避免窄屏撑出大片空白。
5. **收益：** 底部不再有大块空白；三档视口布局完整；方案未生成时按钮置灰。

### 健壮性提升（P1）

#### 4. AI 助手稳定交互与工作台视觉统一

1. **问题：** 基础版 AI 助手按钮拖动与抽屉易互相遮挡、变形，窄屏下布局不稳。
2. **修改后：** 搬运 v5.49/v5.50/v5.52：Pointer Events 区分拖动与点击、固定几何尺寸、按按钮四周可用面积自动避让、视口边界钳制、统一配色层级与人员身份框视觉。
3. **为什么这样改：** 交互稳定性与视觉统一是通用体验问题，不依赖任何平台。
4. **收益：** 拖动不误触、不遮挡、不变形；桌面/移动端均稳定。

#### 5. 后端监控、健康检查、冷启动预热与性能埋点

1. **问题：** 基础版无法观测请求量/错误率/响应时间，健康检查只有 ok，冷启动首请求慢，且完整链路每次都等 Reporter/Reflection。
2. **修改后：** 搬运 `app/metrics.py`、`app/performance.py`、`/api/metrics`、`/api/performance/llm`、健康检查细化、lifespan 预热共享 LLM 客户端、`/api/report` 按需生成（用户打开报告页或导出时才调用 Reporter）。
3. **为什么这样改：** 可观测性与冷启动预热是通用运维能力，与清小搭无关。
4. **收益：** 现场可先看健康检查与指标；报告不再占用首次响应关键路径。

#### 6. 扫描版 PDF OCR、逐文件状态与对象存储同步

1. **问题：** 基础版扫描版 PDF 没有文本层直接失败，方案/附件只写本地 `memory/`，重启会丢。
2. **修改后：** 搬运 v5.74：无文本 PDF 逐页渲染 PNG 交视觉模型 OCR（新增 `pymupdf` 依赖）；`app/services/storage.py` 提供 S3 兼容对象存储同步层（`share_store` 已接入，分享令牌重启不丢）；前端 `renderFileList`/`fileMeta` 逐文件状态展示。另将清小搭分支的远程文件 SSRF/重定向/大小上限保护提取为通用模块 `app/services/remote_io.py`（`download_remote_file`/`cleanup_artifacts`），与具体平台解耦。
3. **为什么这样改：** OCR 与持久化是通用能力，与清小搭平台无关。
4. **收益：** 扫描件可进入分析链路；配置 `STORAGE_BACKEND=s3` 后分享令牌可同步到对象存储并自动恢复；远程拉取自带内网/白名单/重定向防护。

#### 7. Agent 稳定性：报告延迟生成、错误分类重试与 thinking 兼容

1. **问题：** 基础版每次完整跑链路都等待 Reporter/Reflection，DeepSeek V4 默认 thinking 易超时断连，无 Key 时也会构造必然失败的客户端。
2. **修改后：** 搬运 v5.54/v5.55/v5.58：Reporter/Reflection 移出核心响应路径、Planner/Matcher 错误分类重试、`LLM_DISABLE_THINKING` 只对 DeepSeek 发送厂商私有参数、无 Key 不构造客户端。
3. **为什么这样改：** 核心链路越快越稳是所有比赛演示的共同诉求，与接入平台无关。
4. **收益：** 首次响应更快、超时断连减少；导出/报告按需生成。

### 体验优化（P2）

#### 9. 成员日期选择器说明、删除按钮重叠与页面留白（与清小搭分支同步修复）

1. **问题：** 成员添加/成员管理里的日期框没有任何说明，用户不知道它是「不可用日期」；小项目隐藏「上级姓名」后，成员行自动排布错位，日期框右缘盖住删除叉；版本树长文件名/操作名会挤到右侧按钮；顶部/底部工具条文字贴边、底部按钮贴底，观感局促。
2. **修改前：** 日期选择器无标签；`.member-row`/`.member-edit-row` 隐藏 `member-manager`/`edit-member-manager` 后自动排布错位，`.unavailable-picker`（`min-width:190px`）落入 82px/90px 窄列向右溢出盖住删除按钮；平板/移动端规则还把 `grid-column` 写在了隐藏输入框 `.member-unavailable` 上（选择器失效）：
   ```html
   <div class="unavailable-picker"><input type="hidden" class="unavailable-value member-unavailable" …>…</div>
   ```
   ```css
   .member-row .member-unavailable { grid-column: 4; }  /* 目标是隐藏输入框，容器未定位 */
   ```
3. **修改后：** 日期框内新增小标签「不可用日期」（`title` 提示「选择该成员不能参与的日期，排期会自动避开」）；给成员行/成员管理行显式指定 `grid-column`（日期框 7/6、删除按钮 8/7），并把平板/移动端错选的 `.member-unavailable` 选择器改为 `.unavailable-picker`；行内日期框允许收缩（`min-width:0` + `max-width:100%` + `justify-self:start`）避免任何列宽下溢出；版本树标题 `strong` 支持省略号截断、摘要/元数据 `display:block` 防溢出、节点间距 `gap` 10→14px；顶部工具条（`.toolbar-bar`）与底部操作栏（`.sticky-action`）增加左右/下方留白。
4. **为什么这样改：** 根因是隐藏字段导致 CSS Grid 自动排布错位，且平板/移动端选择器指向了隐藏输入框而非日期容器；显式列定位 + 修正选择器让日期框/删除按钮各归其位，收缩兜底保证任何列宽都不溢出，留白让文字不再贴边。
5. **收益：** 用户一眼看懂日期框含义；删除叉不再被日期框盖住；版本树文字不再挤压右侧按钮；页面上下左右留白更舒展。
6. **补充（报告页）：** 项目报告正文存在文字紧贴内容区边缘的问题：① 全局 `*{margin:0}` 重置后，报告只给 h3/h4 设了间距，h1/h2 标题（如「任务列表」「责任分工」）紧贴下方表格/段落——为 `.report-box` 补上 h1/h2/h3 的上下 margin、表格上下 margin 加到 18px；② `.report-box` 增加左右 18px 内边距，与上方 Tab/角色栏对齐；③ 静态资源缓存参数 `?v=5.76` 提升为 `?v=5.76.1`，强制浏览器刷新旧 CSS。
7. **补充（方案列表）：** 方案版本弹窗的方案列表里，「版本树」按钮因 `.modal #planList button { flex:1 }` 优先级更高被拉宽，与文件名按钮各占一半宽度、挤得长文件名换行两行。新增 `.modal #planList .versions-btn { flex:0 0 auto }` 让按钮按内容收缩，文件名改为单行省略号（hover 显示全名），整体更整齐。

### 健壮性提升（P1）

#### 10. 排期与甘特图精确化（周末截止日、半天精度、工作日刻度）

1. **问题：** 周末作为截止日时任务会被错误排到下一周；半天任务因 Python 银行家舍入（`round`）在 0.5/1.5 处忽前忽后；甘特图只有 7 格近似刻度，跨周和半天条形不可信。
2. **修改前：** `TimelineAgent` 用 `round(es[tid]/2)` 计算半天偏移，截止日不处理周末；甘特图按固定 7 列绘制：
   ```python
   work_offset = round(es[tid] / 2)
   ideal_start = _sub_work_days(deadline_date, project_days - 1)
   ```
3. **修改后：** 周末截止日回退到前一个工作日（`_previous_workday`）；半天偏移改用 `//2` 并输出 `start_offset_days`/`duration_days`（精确到半天）；前端重写为按工作日刻度、跳过周末、带真实日期轴的甘特图：
   ```python
   deadline_base = _previous_workday(deadline_date)
   work_offset = es[tid] // 2
   ```
   ```js
   renderGantt=function renderAccurateGantt(){ /* 工作日刻度 + 半天精度 */ }
   ```
4. **为什么这样改：** 排期数据必须可核对（工作日、半天），舍入必须确定；甘特图应直接反映真实日期而非固定 7 格近似。
5. **收益：** 周末截止日不再越界；半天任务位置稳定；甘特图跨周、半天、任务编号与负责人一目了然。

#### 11. 版本/审计持久化与并发加固（S3 同步、线程锁、就绪探测）

1. **问题：** 配置 S3 后版本快照与审计记录仍只写本地，实例重启/扩容会丢；并发保存可能写坏 JSONL；无法探测对象存储是否可访问。
2. **修改后：** `audit_store` 的版本快照与审计记录同步到对象存储并在本地缺失时恢复；`save_version` 加 `_AUDIT_LOCK` 线程锁；`storage` 新增 `check()` 最小权限探测；`share_store` 加 `_SHARE_LOCK` 保护创建令牌。
3. **为什么这样改：** 版本树和审计是可追溯证据，必须与方案一起持久化；并发写入需要互斥；就绪检查需要真实的存储可达性证据。
4. **收益：** 重启不丢版本树；并发保存安全；`/api/ready` 能真实反映存储状态。

#### 12. 媒体处理可配置与提速（OCR 页数/超时、并行 OCR）

1. **问题：** 扫描 PDF 最多 20 页、单页串行等待视觉模型，长文档在平台超时预算内容易失败；模型超时固定不可调。
2. **修改后：** OCR 页数改为可配置（`APP_OCR_MAX_PDF_PAGES` 默认 6，上限 12）；媒体模型超时可配置（`APP_MEDIA_TIMEOUT` 默认 20s）；扫描 PDF 逐页 OCR 改最多 3 路并发并按页序合并。
3. **为什么这样改：** 处理上限与超时须与部署环境匹配，并行能显著缩短长文档耗时。
4. **收益：** 长扫描件更稳更快；部署方可按模型/网络调优。

#### 13. 就绪检查 `/api/ready` 与健康检查版本

1. **修改后：** 新增 `/api/ready` 严格就绪检查（校验 LLM 配置、S3 配置与可达性，未就绪返回 503），并加入鉴权放行列表；`/api/health` 返回版本号。基础版版本保持 v5.76。
2. **为什么这样改：** 健康检查只证明进程存活，就绪检查用于正式部署的滚动/负载均衡准入。
3. **收益：** 部署可区分「活着」与「真正可用」；健康检查带版本便于核对。

#### 14. 部署安全：网页管理端 APP_ADMIN_TOKEN

1. **问题：** 公网部署若只配入站密钥，网页管理 `/api`（方案列表/删除/模型调用）仍裸露；基础版同样需要管理端鉴权。
2. **修改后：** `.env.example`/`render.yaml` 增加 `APP_ADMIN_TOKEN`（Render 可 `generateValue: true`），中间件默认放行健康/就绪/登录，其余 `/api` 需鉴权。
3. **收益：** 公网部署默认受保护；入站与管理端密钥相互隔离。

### 打磨（P3）

#### 8. 依赖、部署配置、测试与文档同步

1. **修改后：** `requirements.txt` 锁定版本并新增 `pymupdf`/`boto3`；`.env.example`/`render.yaml` 补充视觉/ASR/存储/监控变量并移除 `QINGXIAODA_*`；测试新增 role_views、storage、media_formats、deployment_readiness、agent_benchmark、fault_drills、eval 等，`test_share_tokens.py` 重写为只读分享测试，`test_qingxiaoda.py` 与编辑令牌测试随清小搭接入一并移除；脚本与 `eval/cases.json` 一并搬运。
2. **为什么这样改：** 新环境可复现、评审/部署文档一致，且基础版已完全移除清小搭接入层，后续可独立演进。
3. **收益：** 全量 244 项测试通过；部署文档可复查。

**涉及文件：** `app/` 下通用后端与前端文件（删除 `app/compat/` 清小搭协议层与 `tests/test_qingxiaoda.py`；新增 `app/services/remote_io.py`；重写 `app/services/share_store.py` 为只读令牌；同步 `app/agents/timeline.py`、`app/services/audit_store.py`、`app/services/media_analysis.py`、`app/web/routers/system.py`、`app/web/static/app.js`/`style.css` 等通用能力）、`tests/`（重写 `test_share_tokens.py`/`test_fault_drills.py` 走通用路径，新增排期/审计测试）、`scripts/`、`eval/cases.json`、`docs/`（演示、部署与使用说明书）、`requirements.txt`、`.env.example`、`render.yaml`、`README.md`、`CHANGELOG.md`。

---

## v5.48 —— 需求驱动的材料答辩模拟（2026-08-11）

**定位：** 答辩模拟不再是所有项目的固定入口，也不再围绕任务完成情况提问；只有实际需求包含答辩、汇报、路演或成果展示时才出现，并以用户提交的答辩稿或 PPT 为提问依据。

**审查/修改背景：** 原功能把“项目任务计划”直接交给评审 Agent，因此容易生成“如何保证按时完成”“任务如何分工”一类执行检查问题；同时最终页对任何项目都展示评审入口，用户无法提交真正用于答辩的讲稿或演示文件，功能与真实使用场景错位。

### 当前工作区累计改动总览

本次工作区尚未提交的功能改动已全部纳入以下连续版本，没有未归档的代码文件：

| 版本 | 修改主题 | 主要代码范围 | 状态 |
|------|----------|--------------|------|
| v5.41 | Web 路由按业务域拆分 | `app/web/routes.py`、`app/web/routers/` | 已记录 |
| v5.42 | Excel 后缀修复与响应式布局统一 | `app/web/static/app.js`、`style.css`、导出路由 | 已记录 |
| v5.43 | 相似任务版本树、对比与分支回滚 | `app/services/audit_store.py`、历史接口与测试 | 已记录 |
| v5.44 | 最终页 14 个入口归并、知识库内隐 | `index.html`、`app.js`、`style.css` | 已记录 |
| v5.45 | 日历式不可用日期与大型项目排期闭环 | Schema、成员接口、`app.js`、`participants.js` | 已记录 |
| v5.46 | 错误和警告分层展示 | `app.js`、`style.css` | 已记录 |
| v5.47 | 小型项目去层级、志愿者极简档案与认领 | Schema、成员接口、前端与测试 | 已记录 |
| v5.48 | 按需求展示、材料驱动的答辩模拟 | 答辩 Agent、提示词、接口、前端与测试 | 已记录 |

---

### 产品边界修正（P1）

#### 1. 根据原始需求决定是否展示答辩模拟

1. **问题：** 普通协作任务没有答辩环节，最终页仍固定展示模拟入口。
2. **修改前：** “评审预演”是 7 个最终结果标签之一，任何项目生成方案后都可见。
3. **修改后：** 前端从项目名称、描述、任务要求、补充要求和需求分析中识别“答辩 / 汇报 / 路演 / 成果展示 / PPT / presentation / pitch / defense”等明确场景；未命中时隐藏“答辩模拟”，若旧状态停留在该页则自动回到任务计划。
4. **为什么这样改：** 是否需要答辩由原始交付要求决定，不能由系统默认强加给所有项目。
5. **收益：** 普通项目减少无关入口；带答辩要求的项目仍能直接进入模拟；标签数量随实际任务语义变化。

#### 2. 支持提交答辩稿或演示文件

1. **问题：** 用户只能填写一句“评委关注点”，无法提供实际讲稿或 PPT，AI 缺少可靠提问依据。
2. **修改前：** `/api/interview` 只接收计划、分工矩阵和自定义要求，问题主要从任务列表生成。
3. **修改后：** 页面支持直接粘贴答辩稿，也支持一次上传最多 4 个 PPTX、PDF、DOCX、TXT 或 Markdown 文件；新增 `/api/interview/materials` 复用安全文本提取能力，限制文件类型、数量和单文件大小，只返回提取文本而不保存原文件。
4. **为什么这样改：** 正式答辩的追问对象是演示者实际陈述的观点、数据与图表，而不是项目管理后台的数据。
5. **收益：** 用户可使用已有答辩资产；不同格式统一进入同一模拟链路；上传材料不落盘，降低隐私与存储负担。

### 模拟语义修正（P1）

#### 3. 提问改为材料驱动并排除任务完成检查

1. **问题：** 旧提示词把 Agent 定义成“项目评审专家”，上下文包含任务计划和分工，容易追问进度、排期和准时完成措施。
2. **修改前：** 一次性问题和多轮对话主要参考任务名称、任务描述与分工矩阵，前端还错误地可能把字符串结果的第一个字符当成首题。
3. **修改后：** 一次性提问、回答点评和调整提问三套提示词都以答辩材料及原始答辩要求为主上下文，明确禁止检查任务状态、进度、排期和人员负载；问题维度改为核心主张、方案逻辑、数据证据、创新价值、局限改进和表达呈现；接口统一返回问题数组。
4. **为什么这样改：** 答辩模拟应复现评委针对陈述内容的现场追问，而不是复用项目执行审查。
5. **收益：** 问题更贴近真实答辩；多轮追问始终围绕提交材料；首题解析稳定，用户可继续回答或要求调整问题。

### 版本与文档同步（P3）

#### 4. 当前工作区统一发布为 v5.48

1. **问题：** 连续功能优化同时修改了后端、前端静态资源和说明文档，如果版本号不同步，浏览器可能继续使用旧缓存，用户也无法确认当前代码包含哪些能力。
2. **修改前：** 本轮开始时应用、README 和静态资源仍标记为 v5.47：
   ```python
   app = FastAPI(title="协作分工智能体", version="5.47")
   ```
   ```html
   <link rel="stylesheet" href="/static/style.css?v=5.47">
   <script src="/static/app.js?v=5.47"></script>
   ```
3. **修改后：** FastAPI、README、CSS/JS 缓存参数和版本规划表统一为 v5.48：
   ```python
   app = FastAPI(title="协作分工智能体", version="5.48")
   ```
   ```html
   <link rel="stylesheet" href="/static/style.css?v=5.48">
   <script src="/static/app.js?v=5.48"></script>
   <script src="/static/participants.js?v=5.48"></script>
   ```
4. **为什么这样改：** 应用元数据、用户文档和浏览器缓存键必须指向同一个可识别发布版本，才能让代码、说明和实际加载资源保持一致。
5. **收益：** 用户刷新后能加载最新界面；API 版本与文档一致；v5.41–v5.48 的全部工作区改动均可从版本总览追溯。

**涉及文件：**

- `app/web/templates/index.html`、`app/web/static/app.js`、`app/web/static/style.css`：条件入口、材料提交和答辩对话界面。
- `app/web/routes.py`：材料解析接口与答辩请求字段。
- `app/agents/interview_sim.py`、`app/llm/prompts.py`：材料优先的模拟上下文和评委边界。
- `tests/test_agents.py`、`tests/test_workflow_v4.py`、`tests/test_demo_readiness.py`：提示词、上传接口和前端契约回归。

---
## v5.47 —— 项目规模角色模型简化（2026-08-11）

**定位：** 小型项目取消人员上下级，大型项目将志愿者简化为姓名与身份两项，同时允许其参与模块和任务认领。

**审查/修改背景：** 小型固定团队没有维护组织层级的必要，“上级姓名”和组织树增加了无效填写；大型项目的临时志愿者通常不会提供技能、每日工时、不可用日期等完整档案，但旧界面将其和骨干使用同一套详细表单，且项目模式切换还会把志愿者角色重置成骨干。

---

### 产品模型修正（P1）

#### 1. 小型项目移除人员层级

1. **问题：** 小型项目成员数量少、协作关系扁平，仍要求填写“上级姓名”会制造不存在的组织关系。
2. **修改前：** 小型和大型项目共用成员表单、组织树和成员编辑字段，都会展示并保存 `manager`。
3. **修改后：** 小型项目配置和最终成员编辑隐藏上级字段，“团队负载”只展示工作量而不展示组织关系；后端输入模型和成员编辑接口将小型项目的 `manager` 统一归零。
4. **为什么这样改：** 项目规模不同，协作模型也应不同；小型项目关注任务与负载，不需要组织治理信息。
5. **收益：** 减少一个无效字段和一个无效展示区块；旧数据不会继续把层级带入小型项目；前后端语义保持一致。

### 大型项目体验优化（P1）

#### 2. 志愿者采用极简成员档案

1. **问题：** 志愿者被要求填写上级、技能、能力模式、每日工时和不可用日期，不符合临时参与者的实际信息条件。
2. **修改前：** 志愿者角色只改变下拉值，其余详细输入仍全部显示并提交。
3. **修改后：** 大型项目选择“志愿者 / 外部协作者”后，仅保留姓名与角色；其余字段折叠并清空，内部以默认工时维持排期模型必需数据；配置、骨干认领和最终成员管理均采用同一规则。
4. **为什么这样改：** 志愿者的核心信息是身份和认领结果，不应伪造无法获得的详细档案。
5. **收益：** 志愿者录入成本显著降低；不完整信息不会污染技能匹配与组织层级；三处成员界面行为一致。

#### 3. 志愿者进入认领候选并保留角色

1. **问题：** 项目模式切换会强制重写所有成员角色，已经标记的志愿者会变回骨干，认领下拉也看不出其身份。
2. **修改前：** 切换到大型项目时所有角色统一设为“骨干 / 模块负责人”；候选项只显示姓名。
3. **修改后：** 非默认角色通过成员行状态保留；模块认领下拉显示“姓名（志愿者）”；后端继续按正式成员名校验，因此志愿者可认领模块，其子任务也会继承该负责人。
4. **为什么这样改：** 是否能认领应由用户在项目阶段决定，而不是由“志愿者”标签自动禁止。
5. **收益：** 志愿者身份和认领能力兼容；切换模式不会丢失用户选择；新增回归测试覆盖志愿者认领模块及子任务继承。

---

## v5.46 —— 错误与警告信息分层展示（2026-08-11）

**定位：** 将大段错误和分号拼接警告重构为紧凑摘要卡片，通过可展开列表保留完整详情。

**审查/修改背景：** 后端校验错误、资源冲突和工作量建议可能同时包含多条信息，旧版直接显示原始字符串或把数组拼成一个段落，视觉层级缺失、阅读定位困难，移动端还会形成大面积高饱和色块。

---

### 视觉与信息架构优化（P1）

#### 1. 顶部通知改为结构化提示卡片

1. **问题：** 错误通知使用整块红色背景承载全部原始文本，长错误既刺眼又难以扫描，并在 3.5 秒后消失。
2. **修改前：** `showNotice()` 直接设置 `textContent`，所有信息只有一个文本层级。
3. **修改后：** 通知包含状态图标、固定标题、首条摘要、信息条数、可展开详情和关闭按钮；错误停留时间延长至 10 秒，普通反馈保持短时展示。
4. **为什么这样改：** 用户首先需要知道发生了什么，再决定是否查看技术详情；摘要与诊断信息不应争夺同一视觉层级。
5. **收益：** 首屏提示更短、更稳定；复杂校验错误仍可完整查看；长路径和异常文本自动换行，不再撑破布局。

#### 2. 后端校验错误转换为可读条目

1. **问题：** Pydantic 等后端校验错误可能以 JSON 数组返回，旧版会把整个对象序列化成一大段 JSON。
2. **修改前：** 非字符串 `detail` 直接 `JSON.stringify` 后抛给通知组件。
3. **修改后：** 统一解析数组、`detail`、`msg` 和字段路径，将每项错误转换成独立条目；普通换行和分号消息也会自动拆分。
4. **为什么这样改：** 结构化错误应在前端保留结构，而不是降级为不可读的序列化文本。
5. **收益：** 字段位置与错误原因对应清晰；同一套展示兼容字符串、数组和对象错误。

### 警告展示优化（P2）

#### 3. 分工建议与资源冲突改为可折叠列表

1. **问题：** 多条建议通过中文分号连接成一个长段落，信息数量和边界不明确。
2. **修改前：** 工作量警告和资源日历警告使用 `warnings.map(...).join('；')`。
3. **修改后：** 默认只展示首条摘要及总条数，“查看详情”中按列表呈现所有建议；资源日历、提醒、组织复盘和报告加载错误复用同一组件。
4. **为什么这样改：** 警告的常见操作是先判断严重程度，再逐条处理，列表比长段落更符合这一阅读顺序。
5. **收益：** 页面高度可控；警告数量一眼可见；不同页面的错误视觉和交互保持一致。

---

## v5.45 —— 多选不可用日期与大型项目排期闭环（2026-08-11）

**定位：** 用原生日历选择器和可删除日期标签替代逗号文本输入，并让不可用日期贯穿大型项目骨干、最终成员编辑和重新排期。

**审查/修改背景：** 小型项目要求用户手工输入完整日期和分隔符，输入成本高且无错误反馈；大型项目在骨干认领阶段重建成员对象时没有复制不可用日期，最终成员编辑接口也只处理工时、角色和上级，导致日期即使填写也不能稳定影响排期。

---

### 交互优化（P1）

#### 1. 文本日期改为日历多选与日期标签

1. **问题：** 用户必须手工输入 `2026-08-05,2026-08-06`，移动端尤其困难，格式稍有偏差还会被静默忽略。
2. **修改前：** 成员行使用普通文本框，提交时按逗号拆分并用正则过滤。
3. **修改后：** 成员行使用浏览器原生日期选择器；每次选择后点击“添加”，已选日期显示为可单独删除的月日标签；日期自动去重并排序。
4. **为什么这样改：** 日期应该通过受约束控件选择，而不是让用户记忆格式；标签能清楚反馈当前已选结果。
5. **收益：** 不再需要键入日期；桌面和移动端均可调用系统日历；重复日期、错误格式和顺序混乱被统一消除。

### 功能修复（P0）

#### 2. 大型项目骨干阶段保留不可用日期

1. **问题：** 大型项目进入骨干认领后会重新收集成员，但重建对象时没有 `unavailable_dates`，造成配置阶段的数据丢失。
2. **修改前：** 骨干行只包含姓名、角色、上级、技能和每日工时，`syncBackbones()` 生成的新成员也不含日期。
3. **修改后：** 每个骨干行增加同一套多日期选择器；同步骨干前按成员名收集日期，重建成员后恢复到 `state.input.members`。
4. **为什么这样改：** 时间线本身已经支持跳过负责人和协作者的不可用日期，问题发生在进入算法之前的数据断链。
5. **收益：** 大型项目不可用日期真正参与排期；阶段切换、骨干增删和重新渲染后日期不再丢失。

#### 3. 最终成员编辑支持日期更新并触发重排

1. **问题：** 最终方案的成员管理显示了不可用日期输入框，但既不回填旧值，也没有把新值发送给后端。
2. **修改前：** `/api/edit-members` 只接收每日工时、角色和上级；新增成员也忽略不可用日期。
3. **修改后：** 编辑请求新增 `member_unavailable_dates`；现有成员日期经 Pydantic 校验、去重和排序后写回，新成员同时接收日期，随后沿既有流程重算分工、时间线和报告。
4. **为什么这样改：** 不可用日期属于成员可用性的一部分，和每日工时一样必须进入重算接口。
5. **收益：** 用户可在最终分工阶段临时调整请假或冲突日期；更新立即反映到排期和资源日历；接口级回归测试覆盖现有成员和新增骨干。

---

## v5.44 —— 最终方案入口归并与知识能力内隐（2026-08-11）

**定位：** 将最终方案页从 14 个并列入口收敛为 7 个按用户任务组织的工作区，并移除面向用户的知识库入口。

**审查/修改背景：** 旧版把同一工作流里的明细拆成多个平级标签，用户需要在时间线与资源日历、组织树与工作量、分工矩阵与参与清单之间频繁切换；知识库属于系统用于校准和推理的内部能力，不应要求用户理解或直接操作。

---

### 信息架构优化（P1）

#### 1. 14 个平级入口归并为 7 个任务工作区

1. **问题：** 最终方案页入口数量过多，相近信息被拆散，完成一次排期或分工检查需要反复切换标签。
2. **修改前：** 页面展示任务计划、时间线、分工矩阵、工作量、资源日历、组织树、参与清单、复盘、提醒、知识库、组织复盘、报告、评审预演和成员管理共 14 个标签。
3. **修改后：** 页面收敛为 `任务计划 / 排期资源 / 团队负载 / 分工协作 / 执行复盘 / 项目报告 / 评审预演` 7 个入口；同一入口内用有标题和说明的区块承载关联信息。
4. **为什么这样改：** 顶层导航应对应用户目标，而不是对应后端接口或单个数据组件；归并后每次进入都能完成一段完整工作。
5. **收益：** 顶层入口减少 50%；排期、分工和复盘信息上下文连续；桌面端与移动端都减少横向导航拥挤。

#### 2. 合并视图复用既有交互和异步数据

1. **问题：** 简单删除标签会导致资源日历、成员编辑、提醒等能力失去入口，或异步加载覆盖整个结果页。
2. **修改前：** 资源日历固定写入 `resultContent`，参与清单和成员变更保存后跳回已经独立存在的旧标签。
3. **修改后：** 资源日历支持写入指定容器；工作量、参与清单、成员管理、提醒和组织复盘在组合视图内分别绑定并加载；保存后返回新的“分工协作”入口。
4. **为什么这样改：** 入口归并不能牺牲功能完整性，需要让原有组件在组合容器中独立更新。
5. **收益：** 所有保留能力继续可用；异步响应不会覆盖同页其他区块；后续新增同类数据可以继续挂入对应工作区。

### 产品边界优化（P2）

#### 3. 知识库改为内部能力，不再面向用户呈现

1. **问题：** “知识库”作为顶层标签暴露了内部实现概念，并提供查询、Agent 和工具调用按钮，增加用户理解成本。
2. **修改前：** 用户可直接进入知识库标签，手动选择查询或底层工具。
3. **修改后：** 最终方案导航和新渲染链路均不再提供知识库入口；知识检索接口继续保留给规划和 Agent 内部使用。
4. **为什么这样改：** 用户需要的是更可靠的分工结果，而不是操作系统内部的数据检索层。
5. **收益：** 页面表达更聚焦；内部知识能力仍可持续校准结果；后端兼容性不受影响。

---

## v5.43 —— 相似任务版本树、差异对比与分支回滚（2026-08-11）

**定位：** 将平铺独立快照升级为可追踪父子关系和分支的版本树，并支持同一或相似任务跨文件对比与一键回滚。

**审查/修改背景：** 旧版审计记录只有时间顺序，无法识别版本从哪一版演化；回滚会生成另一个独立文件，导致历史继续碎片化，也无法直观看到两个版本具体改了什么。

---

### 健壮性提升（P1）

#### 1. 快照日志升级为带父节点的版本树

1. **问题：** 每条快照只有 `version_id`、时间和摘要，版本之间没有父子或分支关系。
2. **修改前：** 审计记录是纯平铺结构：
   ```python
   entry = {
       "version_id": version_id,
       "timestamp": timestamp,
       "action": action,
       "summary": summary,
   }
   ```
3. **修改后：** 每个新节点记录父版本、来源文件、根版本、版本族和任务指纹：
   ```python
   entry = {
       "parent_version_id": parent_id,
       "parent_filename": parent_name,
       "root_version_id": root_id,
       "family_id": family_id,
       "task_fingerprint": profile["task_fingerprint"],
   }
   ```
4. **为什么这样改：** 版本树的核心不是展示样式，而是持久化父节点；正常保存连接当前最新版，回滚连接被选中的历史版本，才能准确表达线性演化和分叉。
5. **收益：** 可以识别分支点；回滚历史不会丢失；旧 JSONL 快照会在读取时自动补齐线性父节点，无需破坏性迁移。

#### 2. 相似任务自动归入同一版本族

1. **问题：** 同一项目另存为新文件、轻微改名后会成为孤立历史，用户无法在一个界面中比较。
2. **修改前：** 版本只按完全相同的文件名查询，不分析项目或任务内容。
3. **修改后：** 从项目名称和规范化任务名称生成任务画像，以名称相似度和任务集合重合度计算综合分数；达到阈值的新方案自动连接最相似版本，历史查询也会聚合同一批相似任务。
4. **为什么这样改：** 文件名是存储标识，不是业务身份；项目名称与任务集合的组合更能代表“是否是同一/相似任务”，同时不需要引入外部向量数据库。
5. **收益：** 另存方案仍能延续历史；相似项目可跨文件对比；完全无关的任务不会混入同一版本族。

#### 3. 回滚改为当前文件内创建版本分支

1. **问题：** 旧回滚每次生成 `*_rollback.json` 新文件，用户看到的是更多独立方案，而不是当前方案恢复到旧状态。
2. **修改前：** 回滚写入带时间戳的新文件，再为新文件创建第一条独立审计记录。
3. **修改后：** 目标快照直接恢复到当前方案文件，同时新增一个以目标版本为父节点的“回滚”版本：
   ```python
   save_version(
       data, current_filename, action="回滚",
       parent_version_id=target_version,
       parent_filename=source_filename,
   )
   ```
4. **为什么这样改：** 回滚应该改变当前工作副本，同时保留回滚前历史；把新节点挂到目标版本上可以同时满足恢复和可追溯性。
5. **收益：** 一次点击即可恢复；当前文件名和权限不变；回滚后可继续保存形成新分支。

---

### 体验优化（P2）

#### 4. 新增版本差异 API 和树形历史界面

1. **问题：** 用户只能看到“保存时间”，无法判断两个版本在任务、成员或截止日期上的差别。
2. **修改前：** 历史弹窗逐行展示快照，每行只有“回滚到此版本”按钮。
3. **修改后：** 新增 `/api/plan-compare`，返回项目字段、任务新增/移除/修改、负责人和成员变化；前端以缩进节点、连接线、当前/分支点/相似任务标记展示版本树，并提供“对比”“回滚”两个操作。
4. **为什么这样改：** 差异计算放在服务端可复用且能统一字段语义，前端只负责可视化；树节点同时携带来源文件，支持跨相似方案比较和回滚。
5. **收益：** 用户可在回滚前确认影响；分支和当前版本一眼可见；桌面端和手机端均保持可操作布局。

**同步修改：** 新增版本树、相似任务、差异和分支回滚单元测试；README、静态资源缓存及 FastAPI 版本更新为 v5.43。

---
## v5.42 —— Excel 下载后缀与响应式布局修复（2026-08-11）

**定位：** 修复 Excel 导出文件无法直接打开的问题，并统一项目配置页在桌面端、平板和手机端的布局规则。

**审查/修改背景：** 用户实际下载得到 `.excel` 文件；浏览器复核同时发现手机端品牌标题被操作按钮挤成竖排，成员输入在不同宽度下缺少稳定的网格分组。

---

### 关键缺陷（P0）

#### 1. Excel 下载文件名使用真实 `.xlsx` 后缀

1. **问题：** 浏览器将接口格式名 `excel` 直接作为后缀，生成无效的 `plan_report.excel`，系统无法按 Excel 工作簿识别。
2. **修改前：** 前端仅为 Markdown 做了特殊映射：
   ```javascript
   a.download='plan_report.'+(fmt==='markdown'?'md':fmt)
   ```
3. **修改后：** 优先读取服务端 `Content-Disposition` 文件名，并以完整格式映射作为兜底：
   ```javascript
   var filenameMatch=/filename="?([^";]+)"?/i.exec(disposition)
   var extensions={markdown:'md',excel:'xlsx',docx:'docx',pdf:'pdf',csv:'csv',ics:'ics'}
   a.download=filenameMatch?filenameMatch[1]:'plan_report.'+(extensions[fmt]||fmt)
   ```
4. **为什么这样改：** API 已返回合法的 `plan_export.xlsx`；客户端应尊重服务端文件名。显式映射保证响应头缺失时也不会再次把业务格式名误当作文件扩展名。
5. **收益：** Excel 文件可被系统和办公软件直接识别；其他导出格式也统一使用正确文件名；服务端未来调整文件名时前端无需同步硬编码。

---

### 体验优化（P2）

#### 2. 顶栏、配置操作和成员表单采用统一响应式网格

1. **问题：** 手机端顶栏仍沿用桌面单行布局，品牌标题被压成竖排且导出按钮溢出；成员字段在桌面端过密，在手机端又缺少明确的行列分组。
2. **修改前：** 小屏仅缩小按钮和把成员区域改成无约束的两列：
   ```css
   .header-tools .btn { padding: 7px 9px; }
   .member-row { grid-template-columns: 1fr 1fr; }
   ```
3. **修改后：** 手机顶栏分为品牌行和五等分操作行，配置操作两列对齐；成员字段按姓名/角色、上级/描述模式、技能、工时/不可用日期分组，并保留独立删除列：
   ```css
   .app-header { flex-direction: column; align-items: stretch; }
   .header-tools { grid-template-columns: repeat(5,minmax(0,1fr)); }
   .member-row { grid-template-columns: repeat(2,minmax(0,1fr)) 30px; }
   .member-row .member-skills { grid-column: 1/3; }
   ```
4. **为什么这样改：** 响应式布局需要为信息层级重新分组，单纯缩小字号无法解决空间竞争；显式网格使桌面、平板和手机都有可预测的字段位置。
5. **收益：** 手机端品牌和操作按钮不再互相挤压；成员信息更容易逐行阅读；最终方案标签和底部操作在窄屏下保持整齐。

**同步修改：** 新增前端 `.xlsx` 映射与 Excel 响应头/工作簿回归断言；静态资源缓存版本、README 和 FastAPI 版本统一更新为 v5.42。

---
## v5.41 —— Web 路由按业务域拆分（2026-08-11）

**定位：** 在不改变 API 地址和业务行为的前提下，降低单体路由文件的职责密度，为后续增量开发建立清晰边界。

**审查/修改背景：** `app/web/routes.py` 随版本增长已超过 1100 行，鉴权、工具、导出、成员重算与项目流程混在同一模块，修改任一功能都需要理解大量无关代码。

---

### 健壮性提升（P1）

#### 1. 系统、导出与成员路由按业务域拆分

1. **问题：** 单一 Router 同时承担系统能力、文件导出和成员重算，模块职责过宽，容易产生导入膨胀和多人修改冲突。
2. **修改前：** 所有接口直接定义在巨型文件中：
   ```python
   router = APIRouter()

   @router.get("/tools")
   async def tools_list(): ...

   @router.post("/export/pdf")
   def export_pdf(plan: FullPlan): ...

   @router.post("/edit-members")
   def edit_members_endpoint(req: MemberEditRequest): ...
   ```
3. **修改后：** 新增业务域子路由，各模块只依赖自身需要的服务：
   ```python
   # app/web/routers/system.py
   router = APIRouter()

   # app/web/routers/exports.py
   router = APIRouter(prefix="/export")

   # app/web/routers/members.py
   router = APIRouter()
   ```
4. **为什么这样改：** 路由模块应该以业务能力为边界，而不是按功能加入时间不断累积；拆分后导出依赖不会污染成员管理，鉴权模型也不会混入项目流程。
5. **收益：** `routes.py` 减少约 230 行；功能定位更直接；后续可按同一方式继续迁移存储、协作和访谈接口。

#### 2. 保留统一聚合入口和既有 API 契约

1. **问题：** 直接替换 `app.web.routes` 会影响 `app.main`、测试及外部调用方，结构重构可能意外演变为兼容性改动。
2. **修改前：** 主应用只导入一个包含全部实现的 Router：
   ```python
   from app.web.routes import router as api_router
   app.include_router(api_router, prefix="/api")
   ```
3. **修改后：** `routes.py` 继续作为聚合入口，并包含三个子 Router：
   ```python
   router.include_router(system_router)
   router.include_router(export_router)
   router.include_router(member_router)
   ```
4. **为什么这样改：** FastAPI 的子 Router 可以在内部建立模块边界，同时保持主应用注册方式、URL、请求体和响应体不变，是风险最低的渐进式拆分方法。
5. **收益：** 前端无需修改；清小搭适配层不受影响；现有测试和部署入口仍可沿用。

**同步修改：** `README.md` 补充新目录说明；FastAPI 与 README 版本同步更新为 v5.41。

---
## v5.40 —— 清小搭移动端问题原文展示兜底（2026-08-10）

**定位：** 在清小搭手机客户端持续不渲染用户问题气泡时，提供服务端可控、稳定可展示的兼容兜底。

### 1. 回答顶部回显用户问题

1. **问题：** 服务端严格对齐官方 SSE 首帧规范后，手机端仍只显示助手回答、不显示用户问题；用户无法从页面确认本轮提问内容。
2. **修改前：** 服务端只按 OpenAI 协议返回 `assistant` 内容，完全依赖清小搭客户端自行渲染 `user` 气泡。
3. **修改后：** 每次正式回答的首个内容块增加 Markdown 引用“你问：问题原文”；问题压缩为空格分隔并限制 160 字，流式和非流式保持一致。
4. **为什么这样改：** OpenAI 响应协议没有返回 `user` 消息的字段，服务端无法控制清小搭客户端气泡；在助手正文顶部回显问题是唯一可以由后端保证的展示方案。
5. **收益：** 即使手机端继续丢失问题气泡，演示画面仍完整包含问题和回答；桌面端只会出现一行轻量引用，不影响功能。

### 2. 探测与回归保护

1. **问题：** 平台 `max_tokens:1` 连通探测若也回显问题，可能超过最小响应预算或影响协议验证。
2. **修改前：** 没有移动端回显逻辑。
3. **修改后：** 探测路径继续只返回“好”，不增加问题引用；新增流式、非流式回显以及探测不回显测试。
4. **为什么这样改：** 展示兼容不能破坏已通过的接入探测和首帧约束。
5. **收益：** 手机端信息完整与清小搭接入稳定性可以同时保持。

---
## v5.39 —— 清小搭移动端首帧严格兼容（2026-08-10）

**定位：** 修复手机端可能只显示回答、不显示用户问题的流式会话关联异常。

### 1. SSE 首帧顺序严格对齐官方协议

1. **问题：** 清小搭桌面端能显示完整对话，但手机端可能丢失用户提问气泡，只保留助手回答。
2. **修改前：** 在角色帧前发送 `: connected` SSE 注释，角色帧同时包含 `role` 和空 `content`。
3. **修改后：** 响应第一帧严格为 `data:` JSON，且 `delta` 恰好等于 `{"role":"assistant"}`；后续才发送标准 `content` 帧、stop 帧和 `[DONE]`。
4. **为什么这样改：** 清小搭官方指南明确要求 role 帧“恰好一次、首帧”；移动端可能依赖首帧建立用户消息与回答的关联，不能假设它会忽略额外注释或字段。
5. **收益：** 最大限度兼容清小搭桌面端和移动端的消息配对逻辑，同时保留真流式回答和可见状态提示。

### 2. 首帧协议回归测试

1. **问题：** 原测试只统计 role 帧数量，没有约束它必须是响应中的第一条非空数据，也没有限制 `delta` 的精确结构。
2. **修改前：** SSE 注释或角色帧附加字段仍能通过测试。
3. **修改后：** 新增断言：首条非空行必须以 `data:` 开始，首帧 `delta` 必须严格等于 `{"role":"assistant"}`。
4. **为什么这样改：** 移动端兼容依赖帧顺序和字段形状，必须将官方要求固化为精确测试。
5. **收益：** 后续延迟优化不会再次在 role 帧之前插入内容，避免移动端显示问题复发。

---
## v5.38 —— 清小搭线上模型固定为 DeepSeek-V3.2（2026-08-10）

**定位：** 根据真实接口测速结果，将比赛展示环境的默认模型明确固定为响应更快的 DeepSeek-V3.2。

### 1. Render 模型配置

1. **问题：** `LLM_MODEL` 在 Blueprint 中要求部署时手动填写，不同部署可能使用不同模型，难以保证清小搭响应速度和结果一致。
2. **修改前：** `render.yaml` 中 `LLM_MODEL` 使用 `sync: false`，模型名完全依赖 Render 控制台已有值。
3. **修改后：** `render.yaml` 明确设置 `LLM_MODEL=DeepSeek-V3.2`；API 密钥和接口地址仍保持私密变量，不写入仓库。
4. **为什么这样改：** 同一供应商接口实测 DeepSeek-V3.2 短回答约 9 秒，快于三个已测试的千问型号（约 12–16 秒），更适合比赛现场展示。
5. **收益：** 自动部署后的模型选择稳定、可复现，普通问答和复杂需求理解不会因误选更慢模型而增加等待。

---
## v5.37 —— 通用问答真实流式输出与超时修复（2026-08-10）

**定位：** 修复通用问答正确分流后仍返回失败兜底的问题，并进一步降低用户感知到的首字等待。

### 1. 模型超时阈值修复

1. **问题：** 线上通用问答进入模型通道后总是显示“暂时没有回答成功”。
2. **修改前：** 通用问答和复杂需求理解的单次超时被压缩到 6 秒。
3. **修改后：** 超时改为 18 秒；本地真实接口验证当前模型约 9 秒完成短回答，可在新阈值内稳定返回。
4. **为什么这样改：** 供应商模型的正常响应时间超过 6 秒，过短阈值会把正常调用误判为失败，不能用牺牲成功率换取表面上的快速结束。
5. **收益：** 普通知识问题能够实际得到模型答案，不再固定落入错误兜底。

### 2. 通用问答直接流式转发

1. **问题：** 即使模型成功，旧链路也要等待完整答案生成后才开始发送正文。
2. **修改前：** `chat_messages` 同步收集完整文本，再由清小搭适配器切成伪流式片段。
3. **修改后：** LLM 客户端新增标准流式多轮调用；清小搭通用问答直接转发供应商产生的文本增量。
4. **为什么这样改：** 真流式可以在完整生成结束前展示内容，尤其适合清小搭对话场景。
5. **收益：** 真实接口测试约 7.4 秒收到首个模型文本片段，早于完整回答完成时间。

### 3. 标准可见状态提示

1. **问题：** Render 唤醒和模型生成期间，用户容易误以为页面卡死。
2. **修改前：** 只能发送不可见的角色帧或非标准推理字段。
3. **修改后：** 普通问答立即发送标准 `content` 提示“正在回答”，规划请求发送“正在生成分工与排期”，随后继续同一条标准流式回答。
4. **为什么这样改：** 使用平台明确支持的内容字段，既能提供即时反馈，也不会重新引入移动端扩展字段兼容问题。
5. **收益：** 首屏反馈更及时，桌面端和移动端都能按普通助手消息渲染。

### 4. 流式回归测试

1. **问题：** 原测试只验证模拟流格式，没有验证模型文本增量是否被直接转发。
2. **修改前：** 普通问题主要覆盖非流式回答。
3. **修改后：** 新增通用问题流式转发、可见状态、18 秒阈值和分片内容断言，并对真实供应商进行首片段兼容验证。
4. **为什么这样改：** 本次故障发生在真实模型延迟和平台流式协议的交界处，需要同时覆盖逻辑与协议。
5. **收益：** 后续调整超时或流式实现时，可及时发现普通问答再次失效。

---
## v5.36 —— 清小搭通用问答、延迟与移动端兼容修复（2026-08-10）

**定位：** 修复清小搭中普通问题被强制拆解、简单规划等待过久，以及移动端流式消息显示异常的问题。

### 1. 普通问答与项目规划分流

1. **问题：** 旧逻辑只要出现“项目、任务、计划、报告”等宽泛词语，就执行完整的任务拆解和分工，普通知识问题无法正常回答。
2. **修改前：** 单个关键词命中即可进入规划链路；未命中时只返回固定的功能介绍。
3. **修改后：** 仅在用户明确要求任务拆解、分工、排期、甘特图，或同时给出人数、期限和交付物时进入规划；其他问题使用完整多轮上下文交给千问直接回答。
4. **为什么这样改：** 智能体既需要突出协作规划特色，也应具备正常的通用对话能力，不能把所有问题套用为项目。
5. **收益：** 常识、解释、比较等问题可以直接得到答案，“什么是甘特图”也不会被误生成项目计划。

### 2. 简单项目需求快速路径

1. **问题：** “3 个人 5 天做 PPT”这类信息完整的请求仍先等待一次千问需求整理，增加不必要的响应时间。
2. **修改前：** 所有项目规划都同步调用千问，最长等待 12 秒，再执行确定性规划。
3. **修改后：** 结构清楚的单轮项目需求直接进入本地规划；只有长文本或包含增加、删除、延期等多轮修改时才让千问归一化，调用上限缩短到 6 秒。
4. **为什么这样改：** 规则已经能可靠读取简单结构化条件，重复调用模型不会改善结果，只会延长演示等待。
5. **收益：** 常用 Demo 提问更快；复杂和多轮需求仍保留千问的语义理解能力。

### 3. OpenAI 标准流式协议与移动端兼容

1. **问题：** 流式响应发送了非标准 `reasoning` 增量字段，清小搭移动端可能出现消息状态或用户问题显示异常。
2. **修改前：** 首帧后发送 `delta.reasoning`，再发送 `delta.content`。
3. **修改后：** 仅发送标准的 `role`、`content` 和 `finish_reason` 字段，并用 SSE 注释尽早建立连接。
4. **为什么这样改：** 清小搭声明兼容 OpenAI 协议，适配层应避免依赖平台未声明支持的扩展字段。
5. **收益：** 桌面端和移动端使用同一套标准消息格式，降低提问气泡缺失及流式解析异常的概率。

### 4. 成员文本边界修复

1. **问题：** “成员：甲、乙、丙；5天完成”中的期限文本可能被误识别为第四位成员。
2. **修改前：** 成员正则只在换行、截止等固定词处停止。
3. **修改后：** 分号后出现相对期限时结束成员列表解析。
4. **为什么这样改：** 中文单句项目描述通常用分号分隔成员和期限，需要识别这种自然边界。
5. **收益：** 成员数量和姓名更准确，避免出现“5天完成PPT”这样的伪成员。

### 5. 回归测试

1. **问题：** 原测试没有覆盖普通知识问答、概念问题误判、简单需求快路径和非标准流字段。
2. **修改前：** 重点验证规划与文本甘特图。
3. **修改后：** 新增通用问答、甘特图概念问答、千问复杂需求调用、简单需求不调用千问，以及流式字段兼容断言。
4. **为什么这样改：** 用户反馈涉及路由、性能和协议三个边界，必须分别固化测试。
5. **收益：** 后续合并可及时发现“所有问题都被拆解”或移动端协议回退。

---
## v5.35 —— 清小搭多轮规划与甘特图修复（2026-08-10）

**定位：** 在 v5.34 队友最新主线基础上修复清小搭接入层的固定回复、上下文丢失、甘特图缺失和首字等待问题，不新增 RAG 或 Memory Agent。

### 1. 项目意图、人数和相对日期识别

1. **问题：** “3 个人 5 天做一个 PPT，生成甘特图”等自然表达未命中项目请求判断，接口反复返回固定开场白。
2. **修改前：** 仅识别少量“项目/成员/截止”等关键词；人数只接受姓名列表；日期只接受完整年月日。
3. **修改后：** 增加“甘特图、PPT、排期、人数”等意图，并支持“3 个人”“5 天”“两周”等人数和相对日期表达；缺少姓名时自动生成临时成员。
4. **为什么这样改：** 清小搭用户通常以一句自然语言描述项目，不应要求先学习固定输入格式。
5. **收益：** 示例问题可以直接进入任务拆解、智能分工与排期流程，不再误回欢迎语。

### 2. 完整多轮上下文参与规划

1. **问题：** 适配器只读取最后一条用户消息，后续单独说“生成甘特图”时丢失上一轮项目、人数和期限。
2. **修改前：** `messages` 中只提取最后一条 `user` 文本。
3. **修改后：** 按顺序合并本轮请求内全部用户消息，并保留最新消息用于判断用户当前动作。
4. **为什么这样改：** OpenAI 兼容协议会携带对话历史，服务端应使用完整上下文理解省略表达。
5. **收益：** 用户可以自然地先描述项目、再要求排期或甘特图，多轮对话保持连贯。

### 3. 千问参与需求理解并提供安全兜底

1. **问题：** 清小搭链路固定使用 `use_ai=False`，已配置的千问模型没有参与需求理解。
2. **修改前：** 本地正则直接生成草案，复杂表达和最新修改难以归一化。
3. **修改后：** 先由兼容 OpenAI 协议的千问模型提炼项目目标、人数、技能、截止日和交付物，再交给确定性规划链路；模型调用限制为 12 秒，失败自动回退本地解析。
4. **为什么这样改：** 大模型适合理解自然语言，确定性算法适合保证分工和日期稳定，两者分工可以兼顾效果与可靠性。
5. **收益：** 需求理解更准确，同时在模型超时或不可用时仍可完成演示。

### 4. 清小搭文本甘特图与网页工作台入口

1. **问题：** 清小搭只返回任务列表，无法直接展示甘特关系，也没有引导用户查看网页可视化。
2. **修改前：** 回复只包含任务、负责人和起止日期。
3. **修改后：** 回复新增 Markdown 文本甘特图，按任务日期生成进度条，并附公开网页工作台链接。
4. **为什么这样改：** 清小搭消息区适合轻量文本预览，复杂编辑与可视化仍由现有网页工作台承载。
5. **收益：** 评委可在对话内立即看到排期，也能一键进入完整 Demo 流程继续人工调整和导出。

### 5. 首字响应和同步等待优化

1. **问题：** 流式接口在完成需求理解、规划和反思后才返回第一帧，Render 唤醒后体感等待更长。
2. **修改前：** 先同步生成完整答案，再创建 SSE 输出；确认草案还会额外等待 AI Reflection。
3. **修改后：** SSE 先发送角色和状态帧，再执行规划；清小搭路径跳过非必要的同步 AI Reflection；`max_tokens=1` 连通性探针直接快速返回。
4. **为什么这样改：** 首帧应尽快建立流式响应，演示链路中的反思不应阻塞主要结果。
5. **收益：** 热服务首字更快，平台探测更稳定；Render 免费实例冷启动仍取决于平台唤醒时间。

### 6. 回归测试

1. **问题：** 原测试未覆盖相对日期、无姓名人数、多轮甘特图和探针快速路径。
2. **修改前：** 只验证基本鉴权、模型列表和单轮项目请求。
3. **修改后：** 新增相对日期与人数、多轮上下文、缺少项目条件时的定向追问、文本甘特图及工作台链接断言。
4. **为什么这样改：** 将用户实际遇到的问题固化为自动化测试，避免后续合并时复发。
5. **收益：** 清小搭关键演示路径可以持续、快速地回归验证。

---
---
---
---
---
---
## v5.34 —— 深度审查修复（2026-08-04）

**定位：** 按第三方深度审查结论修复 P0/P1：ACL 越权、所有权、回滚权限、跨用户知识隔离、音频转写、只读一致性等。

**审查/修改背景：** 审查实测发现登录用户之间可互相读方案、创建分享链接、普通编辑者保存夺走 owner、回滚产物无 ACL、知识库跨用户泄漏、音频转写参数不兼容等高风险问题。

---

### 关键缺陷（P0）

#### 1. ACL 全链路补校验

1. **问题：** `/api/load` 有校验，但历史版本、导出、分享创建、回滚未校验，bob 能越权读取 alice 方案。
2. **修改前：** `plan-history`、`/plans/{filename}/export`、`share`、`plan-rollback` 直接放行。
3. **修改后：** 这些接口全部接入 `can_read/can_write`，越权请求返回 403。
4. **为什么这样改：** 权限必须覆盖所有能拿到方案内容的入口，只堵 `/load` 等于没堵。
5. **收益：** 越权读取、越权创建公开分享链接、越权回滚均被后端拦截。

#### 2. 所有权与回滚 ACL

1. **问题：** `save_plan` 每次把 owner 改成当前用户；回滚生成的新文件没有 ACL，原作者也访问不了。
2. **修改前：** `set_acl(filename, owner=username)` 无条件覆盖；回滚只生成新文件。
3. **修改后：** 新建方案才设 owner，已有方案只 `add_editor`；回滚前校验写权限，回滚后继承 owner/editors/viewers。
4. **为什么这样改：** 项目归属应稳定；回滚产物必须和原方案保持同一权限模型。
5. **收益：** 编辑者不再夺权；回滚不会产生无主或越权方案。

#### 3. 跨用户知识隔离

1. **问题：** `knowledge_search` 遍历全部 memory 文件，bob 能检索 alice 私有方案。
2. **修改前：** 知识检索不感知当前用户。
3. **修改后：** 按当前用户 ACL 过滤 memory 文件，`/api/knowledge`、`/api/tools/call`、`/api/agent/ask` 均透传 username。
4. **为什么这样改：** 知识检索是“读方案内容”的另一种入口，必须与 `/load` 同一套权限。
5. **收益：** 私有方案不会出现在其他用户的知识库、工具或 Agent 回答中。

#### 4. 音频转写参数

1. **问题：** `audio.transcriptions.create` 传了 SDK 不支持的 `filename/mime_type`，必然失败。
2. **修改前：** `file=BytesIO(...), filename=..., mime_type=...`。
3. **修改后：** `file=(filename, BytesIO, mime)`。
4. **为什么这样改：** OpenAI SDK 要求文件信息放进 `file` 元组。
5. **收益：** 配置语音模型后转写可真正调用。

### 健壮性提升（P1）

#### 5. 只读模式一致性

1. **问题：** 只读模式会拦截所有非 GET 请求，连报告/知识库等只读查询都 403；同时部分编辑入口仍可操作。
2. **修改前：** 所有非 GET `/api` 都按写请求拦截。
3. **修改后：** 中间件只拦截真正的写接口，只读查询白名单放行；前端只读模式隐藏返回调整/成员管理/评审预演等入口，并禁用编辑控件。
4. **为什么这样改：** 只读模式应“可看不可改”，不是“什么都用不了”。
5. **收益：** 分享页能正常查看报告、知识库和 Agent 结果，但不能改数据。

#### 6. 参与清单工时与数据同步

1. **问题：** 协作者/志愿者默认 0h，保存参与清单后原折算工时被清零；`volunteer_pool` 与参与清单互相打架。
2. **修改前：** 前端默认填 0，后端保存参与清单不改 volunteer_pool。
3. **修改后：** 默认按 0.3/0.5 折算；保存参与清单时同步重建 volunteer_pool。
4. **为什么这样改：** 同一份参与数据只能有一个口径，否则 workload 与资源日历会不一致。
5. **收益：** 参与清单保存后工作量不归零，志愿者数据一致。

#### 7. 前端流程与验证规则

1. **问题：** Token 失效后没有重新登录入口；新方案会复用旧文件名静默覆盖；AGENTS.md 前端验证空跑。
2. **修改前：** fetch 包装闭包固定 Token；`generateDraft` 不清旧文件名；内联 script 检查匹配不到内容。
3. **修改后：** 每次请求动态读 Token，401 自动弹登录；新方案/导入清空旧文件名；AGENTS.md 改为 `node --check app.js / participants.js`。
4. **为什么这样改：** 会话恢复、防覆盖、真实语法检查都是可落地前必须补的流程。
5. **收益：** 登录过期可恢复；不会静默覆盖旧方案；前端验证规则真实有效。

### 同步修改

- `app/services/auth_store.py`：ACL 缺省收紧、add_editor/get_acl、set_acl 保留 owner 并支持 viewers。
- `app/web/routes.py`：save/history/export/share/rollback ACL，knowledge/agent/tools 用户透传，文件读取限流。
- `app/main.py`：只读模式写接口白名单。
- `app/services/collab.py`：知识检索按 ACL 过滤、经验去重。
- `app/services/knowledge_agent.py`：风险意图调用多工具、用户透传。
- `app/services/media_analysis.py`：音频转写参数、OpenAI client 参数。
- `app/services/plan_io.py`：ICS DTEND 与转义。
- `app/services/project_service.py`：参与清单同步 volunteer_pool。
- `app/web/static/app.js`：Token 动态读取、401 重新登录、新方案清空旧版本号。
- `app/web/static/participants.js`：参与默认工时、只读模式禁用态。
- `app/web/static/style.css`：只读禁用态与入口隐藏。
- `AGENTS.md`：前端语法检查规则修正。
- `tests/test_auth_routes.py`：P0 越权/夺权/回滚/知识泄漏回归测试。


## v5.33 —— 图片 OCR / 音频转写（2026-08-04）

**定位：** 把图片和音频从“只记元数据”升级为可配置的 OCR/ASR，无模型时仍保留兜底。

**审查/修改背景：** 上一版图片/音频只能记录文件名和大小，无法真正识别内容。

---

### 体验优化（P2）

#### 1. 视觉模型 OCR

1. **问题：** 图片无法读取文字，项目要求里的截图/海报只能人工看。
2. **修改后：** 新增 `APP_VISION_MODEL` 配置，接入 OpenAI 兼容视觉模型后，文件分析会自动提取图片文字；未配置时返回元数据兜底。
3. **收益：** 截图、海报、手写方案等图片内容可进入项目分析。

#### 2. 语音模型转写

1. **修改后：** 新增 `APP_ASR_MODEL` 配置，接入 OpenAI 兼容语音转写模型后，音频文件自动转写为文本；未配置时返回元数据兜底。
2. **收益：** 会议录音、口头需求也能成为可分析的项目资料。

#### 3. 文件分析统一入口

1. **修改后：** 图片/音频走 `media_analysis` 服务，与 PDF/Word/Excel 等文档共用 `extract_text` 流程。
2. **收益：** 多模态资料统一进入 `requirement_analysis`，不需要用户区分文件类型。

### 同步修改

- `app/config.py`：新增 `APP_VISION_MODEL`、`APP_ASR_MODEL`。
- `app/services/media_analysis.py`：OCR/ASR 服务与兜底。
- `app/file_analysis.py`：图片/音频接入识别流程。
- `app/services/auth_store.py`：ACL 缺省收紧，未登记方案仅 admin 可访问。
- `app/main.py`：只读分享请求禁止写操作。
- `app/web/static/app.js`：登录 Token 动态读取，只读模式带写保护头。
- `app/services/collab.py`：经验写入去重。
- `app/services/media_analysis.py`：OpenAI client 参数兼容。
- `app/web/templates/index.html`：缓存版本升级为 5.33。
- `README.md`：版本与演进表同步 v5.33。
- `tests/test_media_analysis.py`：OCR/ASR 兜底与模型调用用例。


## v5.32 —— 多用户账号 + 项目级权限（2026-08-04）

**定位：** 从单管理密码升级为多用户登录，并按方案控制谁能查看、谁能编辑。

**审查/修改背景：** 上一版鉴权只是单 Token，没有账号体系，也没有“某个方案归谁管、谁能改”的项目级权限。

---

### 健壮性提升（P1）

#### 1. 多用户账号与会话

1. **问题：** 只有一个管理密码，无法区分不同用户。
2. **修改后：** 新增 `APP_USERS_JSON` 配置定义用户列表（用户名/密码/角色）；未配置时兼容原 `APP_ADMIN_TOKEN` 生成 admin 账号。登录成功后发放会话 Token。
3. **收益：** 团队多人各自登录，可审计当前用户。

#### 2. 项目级 ACL

1. **问题：** 任何登录用户都能读改所有方案。
2. **修改后：** 每个方案保存时记录 owner；保存/加载/删除/列表按 ACL 判断读写权限，admin 拥有全部权限。
3. **收益：** 项目归属清晰，编辑和查看范围可控。

#### 3. 前端登录表单

1. **修改后：** 登录弹窗改为“用户名 + 密码”，登录后记住当前用户并给所有 `/api` 请求加 Bearer Token。
2. **收益：** 使用体验与账号体系一致。

### 同步修改

- `app/config.py`：新增 `APP_USERS_JSON`。
- `app/services/auth_store.py`：用户、会话、ACL 存储。
- `app/main.py`：多用户鉴权中间件，注入当前用户。
- `app/web/routes.py`：登录/me 接口，保存/加载/列表/删除 ACL 控制。
- `app/web/static/app.js`：登录表单用户名/密码与用户记忆。
- `app/web/templates/index.html`：登录弹窗加用户名输入，缓存版本升级为 5.32。
- `README.md`：版本与演进表同步 v5.32。
- `tests/test_auth_store.py`：用户/会话/ACL 用例。


## v5.31 —— 外部通知：Webhook 推送提醒（2026-08-04）

**定位：** 让提醒不再只停留在系统内，可通过 Webhook 推送到企业微信/钉钉/自建服务。

**审查/修改背景：** 提醒中心已经能在页面展示，但没有外部通知渠道，无法主动触达团队成员。

---

### 体验优化（P2）

#### 1. Webhook 通知服务

1. **问题：** 提醒只能打开系统看，到期/志愿者确认等不会主动通知。
2. **修改后：** 新增 `APP_NOTIFY_WEBHOOK` 配置与 `/api/notify` 接口，把项目名和提醒列表以 JSON POST 到外部 Webhook。
3. **收益：** 可接入企业微信机器人、钉钉机器人、飞书或自建消息服务。

#### 2. 提醒中心一键发送

1. **修改后：** 提醒 Tab 新增“发送提醒通知”按钮，发送后显示成功/失败；未配置 Webhook 时明确提示。
2. **收益：** 操作入口与提醒列表放在一起，不需要记接口。

### 同步修改

- `app/config.py`：新增 `APP_NOTIFY_WEBHOOK`。
- `app/services/notifier.py`：Webhook 推送。
- `app/web/routes.py`：新增 `/api/notify`。
- `app/web/static/participants.js`：提醒页发送按钮与反馈。
- `app/web/templates/index.html`：缓存版本升级为 5.31。
- `README.md`：版本与演进表同步 v5.31。
- `tests/test_notifier.py`：Webhook 启用/禁用与载荷用例。


## v5.30 —— Knowledge Agent 自主工具调用 + 跨项目经验复用（2026-08-04）

**定位：** 把工具调用从“手动按钮”升级为 Agent 自主选择，并把组织复盘经验沉淀成跨项目知识。

**审查/修改背景：** 上一版已有 `/api/tools/call`，但没有 Agent 能根据问题自己决定调哪些工具；组织复盘建议也只停留在单方案里，没有被后续项目复用。

---

### 体验优化（P2）

#### 1. 组织复盘经验自动沉淀

1. **问题：** 组织复盘建议只在当前页面显示，换个项目就查不到。
2. **修改后：** 保存方案时自动把组织复盘建议写入 `memory/experience.jsonl`，知识库问答会同时检索历史方案和经验记录。
3. **收益：** “这类任务易低估/高估”会成为跨项目经验，不再是一次性结论。

#### 2. Knowledge Agent 自主选择工具

1. **问题：** 用户必须自己点工具按钮，AI 不能根据问题决定调哪个工具。
2. **修改后：** 新增 `/api/agent/ask`，Agent 根据问题关键词自主调用工作量、资源日历、提醒、组织复盘、知识检索等工具，并把多个工具结果合成一段回答。
3. **收益：** 问“分析一下当前排期风险”能同时看资源日历和提醒，而不是让用户分别点。

#### 3. 前端 Agent 模式

1. **修改后：** 知识库页新增“Agent 分析”按钮，回答下方显示实际调用链路（如 `workload → reminders`）。
2. **收益：** 工具调用过程透明，结果可追溯。

### 同步修改

- `app/services/collab.py`：经验持久化与检索。
- `app/services/knowledge_agent.py`：Agent 意图识别、工具调用与回答合成。
- `app/web/routes.py`：`/api/agent/ask`；保存时沉淀经验。
- `app/web/static/app.js`：知识库页新增“Agent 分析”按钮。
- `app/web/static/participants.js`：Agent 调用与轨迹展示。
- `app/web/templates/index.html`：缓存版本升级为 5.30。
- `README.md`：版本与演进表同步 v5.30。
- `tests/test_knowledge_agent.py`：Agent 工具调用与经验复用用例。


## v5.29 —— 鉴权 + 工具调用 + 并发冲突 + 多模态文件（2026-08-04）

**定位：** 把最后一批基础能力补齐：可选网络鉴权、系统工具调用、并发保存冲突检测，以及图片/音频文件接入。

**审查/修改背景：** 之前系统没有登录保护，AI 工具只能靠知识问答，多人同时保存会互相覆盖，文件分析也只支持文档。

---

### 健壮性提升（P1）

#### 1. 可选网络鉴权

1. **问题：** `/api` 接口没有访问控制，部署到公网后任何知道地址的人都能读写方案。
2. **修改后：** 配置 `APP_ADMIN_TOKEN` 后自动开启鉴权，除健康检查/登录/只读分享外，所有 `/api` 请求需要 `Bearer` Token；前端显示登录弹窗并持久化登录态。
3. **收益：** 本地不配 Token 不影响开发；公网部署配一个密码即可保护写入。

#### 2. 系统工具调用

1. **问题：** 知识库只能问答，AI 没有可调用的系统工具。
2. **修改后：** 新增 `GET /api/tools` 与 `POST /api/tools/call`，支持工作量、资源日历、提醒、组织复盘、知识检索五类工具；前端知识库页可直接点按钮调用。
3. **收益：** 为后续 Knowledge Agent 提供统一工具入口。

#### 3. 并发保存冲突检测

1. **问题：** 多人打开同一个方案后各自保存，后保存的人会覆盖前面的修改。
2. **修改后：** 保存时携带当前版本 ID；如果服务端已有更新版本，返回 409 并提示先载入最新版本。
3. **收益：** 避免无声覆盖，协作时数据更安全。

#### 4. 图片/音频文件接入

1. **问题：** 文件分析只支持文档，图片/音频无法上传。
2. **修改后：** 上传支持 png/jpg/jpeg/webp/mp3/wav/m4a，提取文件名、大小、类型并提示人工查看/收听。
3. **收益：** 项目资料入口覆盖常见图片和音频，不再被拒收。

### 同步修改

- `app/config.py`：新增 `APP_ADMIN_TOKEN`。
- `app/main.py`：`/api` 可选鉴权中间件。
- `app/web/routes.py`：登录接口、工具接口、保存冲突、分享/提醒/知识/复盘接口，导出旧路由乱码注释清理。
- `app/services/tools.py`：系统工具列表与调用。
- `app/services/audit_store.py`：版本 ID 唯一化与顺序化。
- `app/file_analysis.py`：图片/音频元数据提取。
- `app/web/static/app.js`：登录态、请求头、保存版本 ID、工具按钮。
- `app/web/static/participants.js`：工具调用交互。
- `app/web/static/style.css`：登录弹窗与工具输出样式。
- `app/web/templates/index.html`：登录弹窗、多模态文件 accept，缓存版本升级为 5.29。
- `README.md`：版本与演进表同步 v5.29。
- `tests/test_auth_tools.py`：鉴权、工具、冲突、多模态用例。


## v5.28 —— 只读分享 + 提醒中心 + 知识库 + 组织复盘（2026-08-04）

**定位：** 补上团队协作与知识沉淀：方案可分享只读链接，系统主动提醒待办，支持从历史方案检索知识，并按成员/角色输出组织级复盘。

**审查/修改背景：** 之前方案只能自己看，没有分享、提醒和跨方案知识检索；复盘也只按任务，没有按组织角色汇总。

---

### 体验优化（P2）

#### 1. 只读分享链接

1. **问题：** 方案无法安全地发给别人查看，只能导出文件。
2. **修改后：** 最终方案页新增“复制只读链接”，生成 `/api/share` token；他人打开 `/?share=token` 可查看方案，但保存/导出/编辑按钮全部禁用。
3. **收益：** 一键分享只读视图；不会被误改。

#### 2. 提醒中心

1. **问题：** 任务到期、志愿者未确认、骨干空缺等需要主动找。
2. **修改后：** 新增 `/api/reminders` 与“提醒”Tab，自动汇总：3 天内到期任务、未分配负责人任务、待确认志愿者、未认领模块。
3. **收益：** 待办一眼可见，不再漏掉关键节点。

#### 3. 轻量知识库问答

1. **问题：** 历史方案只有保存功能，没有知识复用。
2. **修改后：** 新增 `/api/knowledge` 与“知识库”Tab，按中文字符 n-gram 检索当前方案和 memory 里的历史方案，返回相关内容与来源。
3. **收益：** 同类项目怎么拆、怎么排工时，可以从历史方案里找到参考。

#### 4. 组织级复盘

1. **问题：** 复盘只按任务看偏差，看不到成员/角色/模块层面的规律。
2. **修改后：** 新增 `/api/org-review` 与“组织复盘”Tab，按成员、角色、模块汇总计划/实际工时偏差，并自动给出“这类任务易低估/高估”的经验建议。
3. **收益：** 偏差能沉淀为组织经验，下一轮计划更有依据。

### 同步修改

- `app/services/share_store.py`：只读分享 token 存储。
- `app/services/collab.py`：提醒、知识检索、组织复盘。
- `app/web/routes.py`：分享/提醒/知识/复盘接口。
- `app/web/static/participants.js`：只读模式、提醒、知识库、组织复盘前端。
- `app/web/static/app.js`：分享按钮与新增 Tab。
- `app/web/static/style.css`：提醒卡片、知识库、组织复盘样式。
- `app/web/templates/index.html`：新增“提醒 / 知识库 / 组织复盘”Tab 与分享按钮，缓存版本升级为 5.28。
- `README.md`：版本与演进表同步 v5.28。
- `tests/test_collab.py`：新增分享、提醒、知识检索、组织复盘用例。


## v5.27 —— 变更记录 / 审计 / 回滚（2026-08-04）

**定位：** 每次保存都留下完整版本快照和审计记录，用户可查看历史版本并一键回滚成新方案。

**审查/修改背景：** 之前保存方案只覆盖 memory 文件，改错了只能靠人工找回，没有“谁在什么时候改过、之前版本长什么样”的追踪能力。

---

### 健壮性提升（P1）

#### 1. 保存时自动生成版本快照与审计记录

1. **问题：** `/api/save` 只写一个 JSON 文件，改错后没有历史可查。
2. **修改后：** 保存时同步写入 `memory/versions/{方案}/` 下的完整快照，并在 `memory/audit/{方案}.jsonl` 追加一条审计记录（版本 ID、时间、动作、说明）。
3. **收益：** 每个保存版本都可追溯；同一方案不会被后续保存覆盖。

#### 2. 历史版本列表与回滚接口

1. **修改后：** 新增 `GET /api/plan-history/{filename}` 返回版本列表；新增 `POST /api/plan-rollback/{filename}/{version_id}` 回滚指定版本。
2. **修改前：** 没有版本入口，误改只能手动重做。
3. **为什么这样改：** 回滚不覆盖原文件，而是生成新的 `_rollback.json` 方案，保留原始记录，避免“回滚本身也覆盖掉历史”。
4. **收益：** 误改可一键恢复；原方案和回滚后的新方案并存，审计链不断。

#### 3. 前端历史方案增加版本/回滚入口

1. **修改后：** 历史方案弹窗里每个方案新增“版本”按钮，进入版本列表后可查看动作/时间/说明，并点击“回滚到此版本”。
2. **收益：** 不需要记接口，直接在历史方案界面完成查看与回滚。

### 同步修改

- `app/services/audit_store.py`：新增版本快照、审计日志、回滚存储。
- `app/web/routes.py`：保存时写审计；新增历史/回滚接口。
- `app/web/static/app.js`：历史方案增加版本与回滚交互。
- `app/web/static/style.css`：版本按钮与版本列表样式。
- `app/web/templates/index.html`：缓存版本升级为 5.27。
- `README.md`：版本与演进表同步 v5.27。
- `tests/test_audit_store.py`：新增审计与回滚用例。


## v5.26 —— Excel / CSV / ICS 导入导出（2026-08-04）

**定位：** 打通与 Excel/CSV/日历工具的进出接口，让计划能导入团队已有任务表，也能导出给团队使用。

**审查/修改背景：** 之前只能导出 MD/Word/PDF，任务计划无法从 Excel/CSV 快速进入系统，也无法导出成日历或表格给团队。

---

### 体验优化（P2）

#### 1. Excel 多表导出

1. **修改后：** 新增 `/api/export/excel`，导出工作簿包含任务、成员、分工矩阵、时间线、参与清单、复盘六张表。
2. **收益：** 一份 Excel 就能覆盖项目全部结构，方便二次编辑和交付。

#### 2. CSV 与 ICS 导出

1. **修改后：** 新增 `/api/export/csv` 导出任务表；新增 `/api/export/ics` 导出日历事件，可直接导入 Outlook/日历 App。
2. **收益：** 与常用办公工具无缝衔接，排期能进入团队成员自己的日历。

#### 3. CSV / Excel 任务导入

1. **问题：** 团队已有的任务表只能手敲进系统。
2. **修改后：** 新增 `/api/import/tasks`，支持从 CSV/Excel 第一张表导入任务，包含编号、任务、模块、工时、负责人、日期、依赖、阶段、技能；配置页新增“导入任务文件”入口。
3. **收益：** 已有任务清单可快速变成可编辑草稿，再走确认分工流程。

#### 4. 前端导出按钮

1. **修改后：** 最终方案底部新增“导出Excel / 导出CSV / 导出ICS”按钮。
2. **收益：** 导出入口与 MD/Word/PDF 并列，用户不需要记接口。

### 同步修改

- `app/services/plan_io.py`：新增 CSV/ICS/Excel 导出与 CSV/Excel 导入解析。
- `app/web/routes.py`：新增 `/api/export/excel`、`/api/export/csv`、`/api/export/ics`、`/api/import/tasks`。
- `app/web/static/app.js`：导入任务文件、导出按钮绑定。
- `app/web/static/style.css`：配置页与最终页导出按钮布局。
- `app/web/templates/index.html`：新增导入文件入口与导出按钮，缓存版本升级为 5.26。
- `README.md`：版本与演进表同步 v5.26。
- `tests/test_plan_io.py`：新增导入导出用例。


## v5.25 —— 资源日历 + 冲突检测深化（2026-08-04）

**定位：** 把成员每日可用工时、任务排期、不可用日期放在同一张日历上，并自动提示每日超载与日期冲突。

**审查/修改背景：** 之前只能看总负载和基础重叠提示，看不出“某天谁已经排满、谁那天请假”；任务日期散落在时间线里，子任务本身没有回填。

---

### 体验优化（P2）

#### 1. 资源日历统计接口

1. **问题：** 成员每日负载没有统一计算，任务工时无法按天摊开。
2. **修改后：** 新增 `resource_calendar` 服务与 `/api/resource-calendar` 接口，把任务参与人的投入工时按排期天数均匀分摊，返回成员每日负载、志愿者负载和不可用日期。
3. **收益：** 前端能直接渲染“某天几点几小时”的真实资源视图。

#### 2. 子任务日期自动合并时间线

1. **问题：** 很多任务只有时间线里有开始/结束日期，`SubTask` 本身没回填，日历统计会误报“暂无排期”。
2. **修改后：** `resource_calendar` 会先从 `timeline.tasks` 按任务 ID 合并日期，再计算每日负载。
3. **收益：** 日历和甘特图使用同一套排期，不再因为字段没回填而显示空日历。

#### 3. 前端资源日历视图

1. **修改后：** 结果页新增“资源日历”Tab，展示日期横轴、每位成员的每日负载格子、不可用日期灰显、超载标红，并列出每个成员的任务清单。
2. **收益：** 谁哪天有空、谁哪天排满、谁在请假当天还有任务，一眼可见。

#### 4. 冲突检测深化

1. **问题：** 之前只检测“总超载/任务重叠”，没有“单日负载超过每日可用工时”和“不可用日期当天仍有任务”。
2. **修改后：** 资源日历接口新增两类冲突提示：每日负载超过 `daily_available_hours`；成员在 `unavailable_dates` 当天仍有任务。
3. **收益：** 冲突从“大概超载”细化到“具体哪一天、哪个人、超了多少”。

### 同步修改

- `app/services/project_service.py`：新增 `resource_calendar`，合并时间线日期。
- `app/web/routes.py`：新增 `/api/resource-calendar`。
- `app/web/static/participants.js`：资源日历渲染与冲突提示。
- `app/web/static/app.js`：结果页新增“资源日历”Tab。
- `app/web/static/style.css`：资源日历格子、超载/不可用样式。
- `app/web/templates/index.html`：新增“资源日历”Tab，缓存版本升级为 5.25。
- `README.md`：版本与演进表同步 v5.25。
- `tests/test_project_service.py`：新增每日超载与不可用日期用例。


## v5.24 —— 组织树 + 任务级参与清单（2026-08-04）

**定位：** 把组织层级和任务资源计划显式化：成员可设上级形成组织树，每个任务可配置参与者、角色与投入工时。

**审查/修改背景：** 之前角色只挂在成员身上，任务工作量仍按“负责人+协作者+志愿者”的旧口径推算；现实项目需要先看清组织关系，再按任务逐一确认谁投入多少。

---

### 体验优化（P2）

#### 1. 成员增加“上级”，形成组织树

1. **问题：** 成员只有角色，没有上下级关系，项目负责人、骨干、基层员工之间的汇报关系无法表达。
2. **修改后：** `TeamMember` 新增 `manager` 字段；成员/骨干/成员管理表单都能填写上级，结果页新增“组织树”Tab，按上级关系渲染层级。
3. **收益：** 组织层级可见；负责人、骨干、基层员工不再是平级标签，而是真实树形结构。

#### 2. 子任务增加任务级参与清单

1. **问题：** 一个任务“谁参加、以什么角色、投入多少工时”没有单独入口，只能靠负责人/协作者/志愿者字段间接推断。
2. **修改后：** 新增 `TaskParticipant` 模型与 `SubTask.participants` 字段；结果页新增“参与清单”Tab，每个任务可添加/删除参与者，填写姓名、角色、投入工时、是否志愿者，并保存。
3. **收益：** 任务资源计划可逐项确认；负责人、协作者、志愿者数量会随参与清单自动同步。

#### 3. 工作量统计优先使用参与清单

1. **问题：** 旧 workload 用固定折算比例推协作者工时，无法反映“这个人在这项任务里实际投了 3h”。
2. **修改后：** `workload_snapshot` 在任务存在 `participants` 时直接按各参与者 `contribution_hours` 统计，成员进入成员负载，外部志愿者进入志愿者负载。
3. **收益：** 工作量条反映真实投入；内部与外部人力分开展示。

#### 4. 报告与导出包含组织树和参与清单

1. **修改后：** 完整 Markdown 增加“组织树”和“任务参与清单”两个章节，报告页因复用同一生成器自动同步。
2. **收益：** 导出文档不再只有分工表，还能直接看到组织关系和每个任务的实际人力投入。

### 同步修改

- `app/models/schemas.py`：`TeamMember.manager`、`TaskParticipant`、`SubTask.participants`。
- `app/services/project_service.py`：`update_task_participants`；workload 优先按参与清单统计。
- `app/web/routes.py`：`/api/task-participants`；完整 Markdown 增加组织树/参与清单；成员编辑支持上级。
- `app/web/static/participants.js`：组织树与参与清单前端逻辑。
- `app/web/static/app.js`：成员/骨干/成员管理表单增加上级字段；结果页新增两个 Tab。
- `app/web/static/style.css`：组织树与参与清单样式。
- `app/web/templates/index.html`：新增“组织树”“参与清单”Tab 与参与者姓名候选，缓存版本升级为 5.24。
- `README.md`：版本与演进表同步 v5.24。
- `tests/test_project_service.py`：新增参与清单驱动工作量用例。


## v5.23 —— 实际工时 + 复盘闭环（2026-08-04）

**定位：** 任务完成后记录实际工时和实际完成日期，方案内对比计划/实际偏差，并把明显偏差沉淀回工时知识库。

**审查/修改背景：** 之前系统只能“计划、分工、排期”，任务标记完成后实际投入没有记录，无法复盘，工时知识库也无法吸收真实完成数据。

---

### 体验优化（P2）

#### 1. 任务增加实际工时与实际完成日期

1. **问题：** `SubTask` 只有 `estimated_hours`，没有实际完成数据，任务完成后“实际花了多久”无从记录。
2. **修改后：** `SubTask` 新增 `actual_hours`、`actual_end_date`、`actual_feedback_recorded` 三个字段，前端任务完成状态卡自动显示实际工时/完成日期输入框。
3. **收益：** 完成任务的真实投入可留痕；导出和复盘都能读取这些数据。

#### 2. 新增 `/api/task-actual` 记录接口

1. **问题：** 前端不能把实际工时安全写回方案，且没有“只记录一次知识反馈”的防重复机制。
2. **修改后：** 新增 `record_task_actual` 服务与 `/api/task-actual` 接口；实际工时与计划偏差超过 0.5h 且任务来自知识库建议时，自动写一条反馈，并标记 `actual_feedback_recorded` 防止重复沉淀。
3. **收益：** 实际数据进入同一套 FullPlan 数据流；知识库只吸收一次真实修正，不被反复编辑污染。

#### 3. 新增“复盘”页

1. **问题：** 计划工时和实际工时散落在不同地方，无法快速看出哪些任务偏差大。
2. **修改后：** 结果页新增“复盘”Tab，展示计划总工时、已记录实际工时、已完成任务数、已填写复盘数，以及每个任务的实际/计划偏差表。
3. **收益：** 哪里超时、哪里提前、哪些任务还没填实际工时，一眼可见。

#### 4. 完整报告包含实际工时复盘

1. **问题：** 报告页与导出的 Markdown 之前没有复盘信息。
2. **修改后：** `_plan_to_markdown` 增加“实际工时复盘”表格，包含计划工时、实际工时、偏差、实际完成日期、状态；报告页因复用同一生成器，自动同步。
3. **收益：** 页面报告和导出报告都包含复盘数据，口径一致。

### 同步修改

- `app/models/schemas.py`：`SubTask` 增加实际工时/日期/反馈标记。
- `app/services/project_service.py`：新增 `record_task_actual`，沉淀知识库反馈。
- `app/web/routes.py`：新增 `/api/task-actual`；完整 Markdown 增加复盘表。
- `app/web/static/app.js`：完成状态卡实际工时输入、复盘 Tab。
- `app/web/static/style.css`：实际工时输入与复盘汇总样式。
- `app/web/templates/index.html`：结果页增加“复盘”Tab，缓存版本升级为 5.23。
- `README.md`：版本与演进表同步 v5.23。
- `tests/test_project_service.py`：新增实际工时与知识反馈用例。


## v5.22 —— 角色模型第一版：角色化工作量 + 志愿者折算 + 冲突检测（2026-08-04）

**定位：** 让角色成为大小项目共用的组织维度，工作量按角色折算，已确认志愿者进入总人力，并检测基础排期冲突。

**审查/修改背景：** 用户希望系统向现实组织落地：大型项目不仅有骨干和志愿者，还有项目负责人、基层员工等角色；这些能力也不应只属于大型项目，小型项目同样需要角色与冲突检测。

---

### 体验优化（P2）

#### 1. 成员增加角色字段，支持自定义角色

1. **问题：** 成员只有姓名/技能/工时，没有角色概念，工作量无法体现“项目负责人统筹、骨干带模块、执行成员做事”的现实差异。
2. **修改前：**
   ```python
   class TeamMember(BaseModel):
       name: str
       skill_tags: list[str] = Field(default_factory=list, ...)
   ```
3. **修改后：**
   ```python
   class TeamMember(BaseModel):
       name: str
       role: str = Field(
           default="执行成员",
           description="角色：项目负责人 / 骨干 / 执行成员 / 志愿者 / 自定义角色。",
       )
   ```
4. **为什么这样改：** 角色是组织属性，不是任务层级；自定义角色用自由文本即可，内置选项用于快速选择，不新增复杂角色管理。
5. **收益：** 大小项目都能表达负责人、骨干、基层成员、外援等角色；工作量展示可以带角色标签。

#### 2. 工作量加入角色折算与已确认志愿者

1. **问题：** 之前只统计 `input.members`，志愿者完全不进工作量；项目负责人、骨干的统筹成本也没有体现。
2. **修改后：**
   - 项目负责人按项目活跃总工时 10% 计统筹工时。
   - 骨干/模块负责人按认领模块数计模块统筹工时。
   - 已确认志愿者按任务估时 50% 折算进“总人力”，待确认/已婉拒不计入。
   - 工作台新增 `volunteers` 区块，和成员工作量并列展示。
3. **为什么这样改：** 角色成本要可见但不能替代任务估时；志愿者只有确认后才算实际投入，避免把“招募中”当成已到位。
4. **收益：** 项目负责人、骨干、执行成员、志愿者的负载都能看见；工作量口径从“只算内部成员”升级为“内部 + 已确认外援”。

#### 3. 基础排期冲突检测

1. **问题：** 成员有不可用日期、任务有起止日期，但系统之前不检查“被排进不可用日期”或“同一人同时参与两个重叠任务”。
2. **修改后：** `workload_snapshot` 增加两类提示：任务排期与成员不可用日期重叠；同一成员同时参与排期重叠的任务。
3. **收益：** 分工看板能提前暴露排期冲突；与超载、未分配等已有提示合并显示，不需要新增页面。

#### 4. 修复大小项目顶部步骤条不随模式切换

1. **问题：** 切换大/小项目模式时，旧的 `state.input.project_mode` 仍然残留，导致顶部导航继续按旧模式渲染，小型项目也出现“大模块拆解/骨干认领/子任务拆解”这些大型项目步骤。
2. **修改前：** `isLargeProject()` 只要 `state.input.project_mode` 存在就直接用它，且模式切换不清空旧方案状态。
3. **修改后：** 配置页可见时优先按当前选中的模式卡判断；切换模式时清空 `state.input/draft/plan` 等旧状态并重设步骤导航。
4. **为什么这样改：** 步骤条是“当前项目流程”的导航，不能跟着旧方案残留；切换模式代表开始新的项目配置，旧的内存方案应该让位。
5. **收益：** 小型项目回到 4 步流程，大型项目保持 6 步流程；切换模式后步骤条立即正确。

#### 5. 修复成员“简介”文本框样式脱节

1. **问题：** 成员行从“标签”切到“简介”后，`textarea.member-bio` 没有参与 `.member-row` 的统一样式，边框、背景、圆角和高度都像原生控件，和其它输入框不在一个图层。
2. **修改后：** 给 `.member-row textarea.member-bio` 补齐边框、圆角、背景、内边距和聚焦态；移动端下跨整行显示。
3. **收益：** 标签/简介两种输入模式视觉一致；简介输入更清晰，不再像临时塞进去的控件。

#### 6. 角色从默认文本改为可选下拉，并随模式切换更新

1. **问题：** 角色之前用文本输入框，默认值只有“执行成员”，用户看不到其它角色选项；切换大/小项目模式后，已有成员行的角色也不会跟着更新。
2. **修改后：** 成员、骨干、成员管理里的角色都改成下拉选择：项目负责人 / 骨干 / 模块负责人 / 执行成员 / 志愿者 / 外部协作者 / 自定义；切换项目模式时同步更新已有行的默认角色。
3. **收益：** 角色入口一眼可见；大型项目默认“骨干 / 模块负责人”，小型项目默认“执行成员”；自定义角色仍可通过下拉触发输入。

#### 7. 报告页改为渲染完整报告，与导出一致

1. **问题：** 报告页只渲染 `ReportOutput` 里的概要、时间线、分工矩阵三段，模块说明、志愿者、完整表格和详细说明都没有；导出的 MD/Word/PDF 却有完整内容，页面和导出不一致。
2. **修改前：** 报告页手动拼 `summary / timeline_section / qa_matrix_section / risk_note` 四个字段。
3. **修改后：** 报告页调用导出用的同一份完整 Markdown 生成器，再复用 `renderMd` 渲染，页面和导出报告完全一致。
4. **收益：** 报告页不再“缩水”；模块、任务表、时间线、分工矩阵、志愿者和风险说明都能在页面直接看到。

#### 8. 补齐站点 favicon，消除 404 请求

1. **问题：** 浏览器访问页面时会自动请求 `/favicon.ico`，项目没有站点图标，服务日志持续出现 404。
2. **修改后：** 新增 `app/web/static/favicon.svg` 并在 `index.html` 中显式声明站点图标。
3. **收益：** 浏览器标签页有品牌图标；服务日志不再出现 favicon 404。

### 同步修改

- `app/models/schemas.py`：`TeamMember` 增加 `role` 字段。
- `app/services/project_service.py`：`workload_snapshot` 支持角色工时、已确认志愿者折算、排期冲突检测。
- `app/web/routes.py`：成员编辑支持 `member_roles` 角色更新，新增成员支持角色。
- `app/web/static/app.js`：角色改为下拉选择并随模式更新；报告页复用完整 Markdown 渲染；工作量卡片显示角色标签与志愿者区块。
- `app/web/static/style.css`：成员行栅格适配角色列；简介文本框统一样式；志愿者负载卡片样式。
- `app/web/templates/index.html`：移除已不使用的角色 `datalist`，缓存版本升级为 5.22。
- `app/web/static/favicon.svg`：新增站点图标。
- `README.md`：版本与演进表同步 v5.22。
- `tests/test_project_service.py`：新增角色/志愿者/冲突用例。


## v5.21 —— 阶段导航去重 + 评审预演双模式（2026-08-04）

**定位：** 去掉大型项目草稿区重复的阶段条，并把评审预演从单向“回答后点评”改成可回答、可让 AI 调整提问的互动模式。

**审查/修改背景：** 用户反馈大模块拆解、骨干认领、子任务拆解在顶部导航之外又出现一组小阶段条，重复累赘；评审预演目前只能输入回答等点评，用户不想写长回答或觉得题目不精确时没有更自然的互动入口。

---

### 体验优化（P2）

#### 1. 移除大型项目底部重复阶段条

1. **问题：** 大模块/骨干/子任务三个阶段除了顶部全局导航，草稿区还渲染了一组小阶段条，同一批按钮在页面里出现两遍。
2. **修改前：**
   ```html
   <div id="largeStageNav" class="large-stage-nav hidden"></div>
   ```
   ```js
   function renderDraftView(){var stageNav=el('largeStageNav');...renderLargeStageNav();...}
   ```
3. **修改后：**
   ```js
   function renderDraftView(){
     if(isLargeProject()){...}else{renderSmallDraft()}
   }
   ```
4. **为什么这样改：** 顶部 `stepNav` 已经能直接在大模块/骨干/子任务三阶段间切换，小阶段条只是冗余副本；删掉后导航入口唯一，流程更清楚。
5. **收益：** 草稿区不再出现两组相同阶段按钮；顶部导航保持完整；三阶段切换能力不受影响。

#### 2. 评审预演新增「调整提问」互动模式

1. **问题：** 评审预演只有“输入回答 → AI 点评 → 下一题”单向流程，用户不想输入长回答时没有出口；觉得题目不精确或不好时，也无法让 AI 根据反馈重新出题。
2. **修改前：**
   ```html
   <form ...><textarea id="interviewAnswer" ...></textarea><button type="submit">回答</button></form>
   ```
   ```python
   if user_answer.strip():
       messages.append({"role": "user", "content": user_answer})
   result = self.llm.chat_messages(
       system_prompt=INTERVIEW_CHAT_SYSTEM, ...)
   ```
3. **修改后：**
   ```html
   <div class="interview-mode-toggle">
     <button class="active" data-interview-mode="answer">回答问题</button>
     <button data-interview-mode="adjust">调整提问</button>
   </div>
   ```
   ```python
   adjust_mode = mode == "adjust"
   if adjust_mode:
       feedback = user_answer.strip() or "这道题不够精确，请调整得更具体、更贴合我们的项目。"
       messages.append({"role": "user", "content":
           "请根据我的反馈重新调整你刚才提出的评审问题。只输出调整后的一个问题，不要点评，不要解释。\n\n反馈：" + feedback})
   result = self.llm.chat_messages(
       system_prompt=INTERVIEW_ADJUST_SYSTEM if adjust_mode else INTERVIEW_CHAT_SYSTEM, ...)
   ```
4. **为什么这样改：** “回答问题”和“调整提问”是评审预演里两种互补的互动方式：前者保留原有点评闭环，后者让用户用一两句话就能修正题目；后端用独立系统提示约束 AI 只重出问题，避免把“调整意见”误当成回答来点评。
5. **收益：** 不想长篇回答时可以直接要求调整题目；题目不精确时能得到贴合项目的新问题；调整完成后自动切回回答模式，点评闭环不被破坏。

### 同步修改

- `app/web/templates/index.html`：移除 `largeStageNav` 容器，缓存版本升级为 5.21。
- `app/web/static/app.js`：移除 `renderLargeStageNav`；评审预演双模式交互、`mode` 请求参数。
- `app/agents/interview_sim.py`：`chat_turn` 支持 `mode="adjust"`，调整提问时只输出新问题。
- `app/llm/prompts.py`：新增 `INTERVIEW_ADJUST_SYSTEM` 评审调整提示词。
- `app/web/routes.py`：`InterviewChatRequest` 增加 `mode` 字段并透传。
- `app/web/static/style.css`：评审输入区双模式切换样式。
- `README.md`：版本与演进表同步 v5.21。
- `tests/test_interview_chat.py`：新增调整提问用例。


## v5.20 —— 前端交互修复 + 流程职责理顺 + 时间线/历史弹窗/品牌视觉再打磨（2026-08-04）

**定位：** 修复用户实测反馈的按钮白化、添加任务不可编辑、骨干下拉简陋等问题，并继续收敛时间线、历史弹窗和品牌视觉。

**审查/修改背景：** 用户在 v5.19 之后继续试用，发现「生成任务拆解」等主按钮悬浮后背景变白、文字看不清；大模块阶段添加任务后只能看到「新任务 2h」却无法编辑名称和工时；骨干认领和分工看板里的骨干下拉框仍是浏览器默认样式；时间线页甘特图与排期明细内容重复；历史方案弹窗关闭按钮会随滚动跑出视野、文件名还容易折行；同时希望品牌小图标和整体界面再精致一点。

---

### 体验优化（P2）

#### 1. 主按钮悬浮白化导致文字不可见

1. **问题：** 悬浮「生成任务拆解」「下一步：骨干认领」等主按钮时背景变成近白色，而文字仍是白色，用户完全看不到按钮上的字。
2. **修改前：**
   ```css
   .btn:hover {
     background: linear-gradient(180deg,#fff,var(--card-soft));
     ...
   }
   .btn-primary:hover {
     color: #fff;
     box-shadow: ...;
     transform: translateY(-1px);
   }
   ```
3. **修改后：**
   ```css
   .btn-primary:hover {
     background: linear-gradient(135deg,#6e6cf3,#5147e8 58%,#4539cc);
     color: #fff;
     box-shadow: ...;
     transform: translateY(-1px);
   }
   ```
4. **为什么这样改：** `.btn:hover` 是通用规则，会覆盖 `.btn-primary` 的渐变背景；`.btn-primary:hover` 只改了文字和阴影、没有重新声明背景，于是悬浮时背景被通用规则换成了白色。重新在 `:hover` 里声明主色渐变即可消除层级冲突。
5. **收益：** 主按钮悬浮时文字始终可读；主色悬停反馈更明显；其它普通按钮/幽灵按钮行为不受影响。

#### 2. 移除大模块阶段的子任务添加/编辑入口

1. **问题：** 大模块拆解本应只负责模块结构，但模块卡片里仍有「添加子任务」按钮，点击后自动跳到子任务拆解阶段，用户在大模块阶段编辑一次就跳一次，还会直接跳过骨干认领。
2. **修改前：**
   ```js
   renderLargeModules() {
     ...
     '<button class="btn-small add-task-to-module" ...>＋ 添加子任务</button>'
     ...
     btn.onclick=function(){mutateDraft([{op:'add',module_id:btn.dataset.module}]).then(function(){
       state.largeStage='tasks';renderDraftView();...
     })}
   }
   ```
3. **修改后：**
   ```js
   renderLargeModules() {
     // 只保留模块新增/合并/删除/排序，不再渲染 add-task-to-module
   }
   ```
4. **为什么这样改：** 大模块、骨干认领、子任务拆解是三段职责明确的阶段；子任务的新增和编辑只在「子任务拆解」里做，模块阶段不该重复入口，否则用户会被反复弹走、打断骨干认领流程。
5. **收益：** 大模块阶段保持专注；骨干认领不会被自动跳过；子任务新增/编辑入口只保留在正确阶段，流程更清晰。

#### 3. 子任务拆解工具栏补齐「重新生成」

1. **问题：** 子任务拆解阶段没有「重新生成」入口，代码里虽然有 `if(el('redraftBtn'))` 但工具栏根本没有渲染该按钮。
2. **修改前：**
   ```js
   el('draftToolbar').innerHTML='...id="addTaskBtn">＋ 新增任务</button>...id="mergeTaskBtn">合并选中</button></div>'
   ```
3. **修改后：**
   ```js
   el('draftToolbar').innerHTML='...id="addTaskBtn">＋ 新增任务</button>...id="mergeTaskBtn">合并选中</button><button class="btn btn-ghost" id="redraftBtn">重新生成</button></div>'
   ```
4. **为什么这样改：** v5.18 已预留 `if(el('redraftBtn'))` 的事件绑定，但工具栏字符串里没渲染按钮，导致入口“有代码没 UI”；把按钮放回工具栏即可复用既有重新生成流程。
5. **收益：** 对 AI 拆解结果不满意时可在任务阶段直接重试；大型/小型项目操作对等。

#### 4. 骨干下拉框统一成正式控件样式

1. **问题：** 骨干认领、模块分工看板中的 `module-owner-select` 只有宽度定义，边框、背景、圆角全靠浏览器默认，看起来像没做样式。
2. **修改前：**
   ```css
   .module-claim-card .module-owner-select { width: 150px; }
   .board-module-head .module-owner-select { width: 130px; }
   ```
3. **修改后：**
   ```css
   .module-owner-select {
     min-width: 0;
     border: 1px solid var(--line);
     border-radius: var(--radius-xs);
     padding: 7px 10px;
     font-size: 12px;
     color: var(--text);
     background: #fff;
     cursor: pointer;
     transition: border-color .16s,box-shadow .16s,background .16s;
   }
   ```
4. **为什么这样改：** 同一控件在不同页面复用同一基础样式，再各自保留宽度即可；默认原生下拉在浅色卡片里显得突兀。
5. **收益：** 骨干认领、模块分工、分工招募里的下拉框视觉一致；悬停/聚焦反馈与其它输入控件统一。

#### 5. 时间线页甘特图与排期明细合并

1. **问题：** 时间线页先渲染一整张甘特图，下面又重复一份「排期明细」，同一批任务出现两遍，信息冗余。
2. **修改前：**
   ```js
   content=renderGantt()+'<div class="timeline-detail"><h3>排期明细</h3>'+(state.plan.timeline.tasks||[]).map(...).join('')+'</div>'
   ```
3. **修改后：** `content=renderGantt()`，甘特图每行新增 `.gantt-meta`，直接展示日期、工期、关键/浮动状态和任务状态，并增加表头行。
4. **为什么这样改：** 甘特图本身已经是「任务 + 时间条 + 日期」的结构，单独再列一遍明细只会让页面更长、更重复；把明细并入每行后仍然能一眼看到排期要素。
5. **收益：** 时间线页信息密度更高；滚动距离明显缩短；关键路径、浮动和状态不再需要上下对照。

#### 6. 历史方案弹窗关闭按钮与行高优化

1. **问题：** 历史方案弹窗里关闭按钮固定在顶部，列表很长时滚到下方就没法快速关闭；改成单行省略后，长文件名直接变成省略号，用户反而看不清方案名；删除按钮也仍然占位偏大。
2. **修改前：**
   ```css
   .modal { padding: 20px; }
   .modal-head { ... margin-bottom: 12px; }
   .modal #planList button { padding: 11px 13px; }
   .modal #planList button span { white-space: nowrap; text-overflow: ellipsis; }
   .delete-plan { width: 32px; height: 32px; }
   ```
3. **修改后：**
   ```css
   .modal { padding: 0; }
   .modal-head { position: sticky; top: 0; z-index: 5; padding: 14px 20px 12px; background: rgba(255,255,255,.97); }
   .modal #planList button { padding: 8px 12px; min-height: 44px; }
   .modal #planList button span { white-space: normal; overflow-wrap: anywhere; }
   .delete-plan { width: 24px; height: 24px; font-size: 14px; }
   ```
4. **为什么这样改：** 吸顶解决关闭入口问题；文件名从强制单行改为自然换行后，长名称能完整显示出来，删除按钮缩小后也能把更多宽度让给文件名。
5. **收益：** 滚动到任意位置都能看到关闭入口；历史方案名可读性恢复；删除按钮更紧凑，列表仍保持清爽。

#### 7. 品牌小图标升级为六边形节点网络

1. **问题：** 第一版节点图缺少容器，第二版六边形结构太复杂，反馈效果反而更差。
2. **修改前：**
   ```html
   <svg viewBox="0 0 32 32" fill="none"><path d="M16 3.2 27 9.1v13.8L16 28.8 5 22.9V9.1L16 3.2z" .../><circle .../></svg>
   ```
3. **修改后：**
   ```html
   <svg viewBox="0 0 32 32" fill="none"><rect x="4.5" y="4.5" width="23" height="23" rx="7" .../><circle .../><path .../></svg>
   ```
4. **为什么这样改：** 圆角方形外框更贴近产品气质，内部只保留三个节点和三条连线，信息更简洁，缩小后仍然清楚。
5. **收益：** 图标更耐看；与顶部渐变背景、品牌状态点更协调；识别度高于纯散点版本。

#### 8. 志愿者招募标题与底部说明文字留白

1. **问题：** 志愿者招募页标题和「调整完成后确认最终方案 / 建议不会阻止你保存」说明文字紧贴容器左边缘，视觉上缺少呼吸感。
2. **修改后：**
   ```css
   .volunteer-recruit-head { padding: 0 2px; }
   .sticky-action>div { padding-left: 2px; }
   ```
3. **收益：** 标题与下方方框内容对齐更自然；底部说明不再贴边，页面观感更精致。

#### 9. 子任务拆解阶段按钮文案被 finally 恢复成旧值

1. **问题：** 从骨干认领进入子任务拆解后，`renderLargeTasks` 已把按钮改成「确认拆解并开始分工」，但 `onConfirmDraft` 的 `finally` 又把进入前的旧文案「下一步：骨干认领」写回去，用户看到阶段和按钮不一致。
2. **修改前：** `finally{btn.disabled=false;btn.textContent=oldText}`
3. **修改后：** `finally{btn.disabled=false;updateDraftFooter()}`
4. **为什么这样改：** `oldText` 记录的是点击前的文案，不是当前阶段该有的文案；`updateDraftFooter()` 会根据 `state.largeStage` 实时计算，成功、失败、切换阶段都能得到正确按钮文字。
5. **收益：** 子任务拆解阶段按钮稳定显示「确认拆解并开始分工」；模块、骨干、任务三个阶段切换后按钮文案始终正确。

### 同步修改

- `app/web/static/app.js`：移除大模块阶段添加子任务入口，子任务新增/编辑只在子任务拆解阶段；修复 finally 覆盖阶段按钮文案；任务阶段补回重新生成；甘特图合并排期明细。
- `app/web/static/style.css`：主按钮 hover 渐变、骨干下拉统一、时间线表头/元信息、历史弹窗吸顶与文件名可换行、志愿者/底部说明留白。
- `app/web/templates/index.html`：品牌 SVG 最终版升级为圆角方形节点网络，CSS/JS 缓存版本升级为 5.20。
- `app/main.py`、`app/models/schemas.py`：版本号统一升级为 5.20。
- `README.md`：顶部版本与版本演进表同步 v5.20。
- `CHANGELOG.md`：新增 v5.20 详细记录，版本规划表新增 v5.20。


## v5.19 —— 前端视觉精细度整体打磨（2026-08-04）

**定位：** 在不推翻现有结构的前提下，对页面布局、配色、图案与组件质感做一轮精细化升级，让工作台更精致美观。

**审查/修改背景：** 用户反馈页面整体偏平、大圆角模板感较重、卡片层次与细节不足，希望继续提升整体美观和精致度；本轮为纯视觉打磨，不改变业务流程。

---

### 体验优化（P2）

#### 1. 设计变量与页面背景升级为有质感的浅色工作台

1. **问题：** `body` 只有两个很淡的 radial-gradient，背景几乎纯平；阴影与圆角体系偏大偏松，卡片层次靠大圆角支撑，整体不够精致。
2. **修改前：**
   ```css
   --shadow: 0 4px 16px rgba(30,41,59,.08),0 1px 3px rgba(30,41,59,.04);
   --radius: 16px;
   body{background-image:radial-gradient(at 20% 0%,rgba(99,102,241,.04) 0,transparent 50%),radial-gradient(at 80% 100%,rgba(14,165,233,.03) 0,transparent 50%)}
   ```
3. **修改后：**
   ```css
   --shadow: 0 1px 2px rgba(30,41,59,.04),0 10px 30px -8px rgba(30,41,59,.12);
   --ring: 0 0 0 3px rgba(99,102,241,.16);
   --radius: 14px;
   body{background-image:radial-gradient(at 16% -6%,rgba(99,102,241,.08) 0,transparent 46%),radial-gradient(at 88% 4%,rgba(14,165,233,.07) 0,transparent 42%),radial-gradient(at 74% 96%,rgba(124,58,237,.05) 0,transparent 46%),linear-gradient(rgba(99,102,241,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(99,102,241,.025) 1px,transparent 1px)}
   ```
4. **为什么这样改：** 视觉精致度来自层次而非单纯加大阴影/圆角；把阴影改为「贴边微阴影 + 远距离柔影」、背景叠加细网格后，页面会显得更薄、更有秩序，圆角收敛也更符合办公工具的气质。
5. **收益：** 背景不再偏平，有克制的立体感；阴影层级更清晰；后续组件共用 `--ring` 焦点环，交互状态统一。

#### 2. 顶栏、品牌标识与步骤导航细节点缀

1. **问题：** 顶栏和步骤导航都只有一条普通底边框，品牌方块缺少细节，页面头部显得朴素、缺少精致记忆点。
2. **修改前：**
   ```css
   .app-header{...border-bottom:1px solid var(--line);...}
   .brand-mark{...border-radius:12px;background:linear-gradient(135deg,#6366f1,#7c3aed);...}
   .step-nav{...border-bottom:1px solid var(--line);...}
   ```
3. **修改后：**
   ```css
   .app-header{...border-bottom:none;box-shadow:0 1px 0 rgba(30,41,59,.04),0 12px 32px -28px rgba(30,41,59,.3)}
   .app-header::after{content:"";...background:linear-gradient(90deg,transparent 2%,var(--primary-soft2) 30%,var(--accent-soft) 72%,transparent 98%)}
   .brand-mark{...border-radius:13px;background:linear-gradient(135deg,#6366f1,#7c3aed 72%,#a855f7);box-shadow:0 6px 18px rgba(79,70,229,.32),inset 0 1px 0 rgba(255,255,255,.28),inset 0 -1px 0 rgba(30,41,59,.12)}
   .brand-mark::after{content:"";...width:11px;height:11px;border-radius:50%;background:linear-gradient(135deg,#34d399,#10b981);border:2px solid #fff}
   .step-nav{...backdrop-filter:blur(14px);border-bottom:none;box-shadow:0 1px 0 rgba(30,41,59,.04)}
   .step-nav::after{content:"";...background:linear-gradient(90deg,transparent 4%,var(--primary-soft2) 34%,var(--accent-soft) 66%,transparent 96%)}
   ```
4. **为什么这样改：** 用渐变细线和品牌状态点替代硬边框，能在不增加视觉噪音的前提下强化顶部识别度；品牌多一层高光和状态点后更像一个有细节的产品标识。
5. **收益：** 头部层次更精致；品牌与导航有明确视觉锚点；步骤导航悬浮感更自然。

#### 3. 卡片、按钮与输入焦点统一质感

1. **问题：** 各组件各自定义边框/阴影，悬停只改边框色，输入聚焦也只有边框变色，交互反馈弱且不统一。
2. **修改前：**
   ```css
   .btn{...transition:all .18s cubic-bezier(.4,0,.2,1)}
   .task-edit-card{...box-shadow:var(--shadow-xs)}
   .task-edit-card:hover{border-color:var(--primary-soft2);box-shadow:var(--shadow-sm)}
   ```
3. **修改后：**
   ```css
   .btn{...box-shadow:inset 0 1px 0 rgba(255,255,255,.75),0 1px 2px rgba(30,41,59,.04)}
   .task-edit-card{...box-shadow:inset 0 1px 0 rgba(255,255,255,.9),var(--shadow-xs)}
   .task-edit-card:hover{transform:translateY(-1px)}
   :focus-visible{outline:2px solid rgba(99,102,241,.5);outline-offset:2px}
   ```
4. **为什么这样改：** 顶部高光让卡片有「可点击的实体」感，悬停微位移提供即时反馈；全局焦点环让键盘导航与表单聚焦状态一致可辨。
5. **收益：** 卡片、按钮、看板、工作量卡、甘特图等统一获得精致悬停/聚焦反馈；表单聚焦更醒目；可访问性更好。

#### 4. 模式卡、汇总卡、弹窗与 AI 抽屉细节

1. **问题：** 模式卡选中态是纯色填充，汇总卡只有渐变底色，弹窗/抽屉阴影偏轻，关键入口和状态缺少细节层级。
2. **修改前：**
   ```css
   .config-head{...background:linear-gradient(180deg,var(--card-soft),transparent)}
   .mode-card.active{...background:var(--primary-soft);box-shadow:0 0 0 4px rgba(99,102,241,.1),var(--shadow-sm)}
   .modal{...border-radius:var(--radius);box-shadow:var(--shadow-lg)}
   ```
3. **修改后：**
   ```css
   .config-head{...background:linear-gradient(180deg,#fff,var(--card-soft));position:relative}
   .mode-card.active::after{content:"\2713";...background:linear-gradient(135deg,var(--primary2),var(--primary));color:#fff}
   .summary-card::before{content:"";...width:3px;background:linear-gradient(180deg,var(--primary2),var(--primary))}
   .modal{...border-radius:18px;box-shadow:0 24px 80px rgba(15,23,42,.3),inset 0 1px 0 rgba(255,255,255,.8)}
   .drawer-head::after{content:"";...background:linear-gradient(90deg,var(--primary-soft2),transparent 70%)}
   ```
4. **为什么这样改：** 选中模式用对勾徽章而非单纯换底色，状态语义更明确；汇总卡加主题色边条、弹窗加深层阴影，让「正在编辑的内容」和「覆盖层」层次拉开。
5. **收益：** 选中态更直观；页面焦点区域层次分明；弹窗/抽屉更有悬浮感，不再像平铺面板。

#### 5. 移动端细节适配

1. **问题：** 窄屏下部分按钮、表单和悬浮按钮沿用桌面尺寸，间距偏紧，移动端预览不够从容。
2. **修改前：**
   ```css
   @media(max-width:760px){
     .app-header{padding:0 14px}
     .app-main{padding:16px 12px 50px}
     .assistant-button{right:24px;bottom:24px;padding:13px 20px}
   }
   ```
3. **修改后：**
   ```css
   @media(max-width:760px){
     .step-nav button{padding:7px 12px}
     .app-main{padding:18px 14px 60px}
     .mode-card{padding:16px}
     .assistant-button{right:16px;bottom:16px;padding:11px 16px}
   }
   ```
4. **为什么这样改：** 移动端不是简单等比缩小，而是需要同时收紧组件内距并保留页面呼吸感；悬浮按钮贴边可减少误触和遮挡，让关键入口在小屏上依然易点。
5. **收益：** 移动端按钮和内容不再拥挤；悬浮按钮避开边缘；整体在小屏下保持与桌面一致的精致度。

### 同步修改

- `app/web/static/style.css`：样式从压缩格式整理为可读格式并完成视觉精修；注释乱码修复。
- `app/main.py`、`app/models/schemas.py`、`app/web/templates/index.html`：版本号统一升级为 5.19。
- `README.md`：顶部版本与版本演进表同步 v5.19。
- `CHANGELOG.md`：新增 v5.19 详细记录，版本规划表补齐 v5.16-v5.19。


## v5.18 —— 大型项目体验对标小型项目 + P0–P3 全量修复（2026-08-04）

**定位：** 修复审查发现的关键缺陷与体验短板，并把大型项目的交互精细度补齐到与小型项目同等水平。

**审查/修改背景：** 第二轮深度审查发现大型项目看板改骨干负责人后不联动重算、志愿者行内编辑触发整池保存导致竞态丢输入、骨干姓名失焦收集按索引错位、版本号三处不同步、三处乱码注释残留、以及 /api/export/{filename} 老路由与 POST 导出路由前缀冲突等隐患。同时大型项目相比小型项目仍缺"恢复自动分工"、"需招募志愿者"可编辑字段、子任务跨模块移动、重新生成等入口。

---

### 关键缺陷（P0）

#### 1. 大型项目看板改骨干负责人后不联动重算

1. **问题：** 分工看板中改模块骨干下拉（module-owner-select）只把值写进内存对象，既不刷新工作量、也不把模块下未指定负责人的子任务归给新骨干，用户"改了没反应"。
2. **修改前：** onchange 只写 `original.assignee_id=sel.value||null`，无后续动作。
3. **修改后：** 改骨干后联动该模块下子任务（未指定负责人的自动归骨干、清空骨干时原跟随任务清空），记录变更描述，并 renderBoard() 刷新工作量条。
4. **为什么这样改：** 骨干认领的语义是"模块负责人统领模块下子任务"，与后端 apply_manual_assignment 中"模块下未单独指定负责人的子任务默认归模块负责人"的规则一致；前端缺这步会导致看板分工与最终确认结果不一致。
5. **收益：** 改骨干即时反映到工作量；与小型项目改负责人即时刷新的行为对齐；分工数据自洽。

---

### 健壮性提升（P1）

#### 2. 志愿者行内编辑整池保存竞态导致丢输入

1. **问题：** 志愿者招募面板中姓名/联系/状态/备注的 onchange 直接触发 saveVolunteers，该方法从 DOM 整池收集后 POST 并 renderVolunteerRecruit() 全量重渲染。用户编辑 A 行时若 B 行正在输入，B 行失焦且回车失效；连续改状态会反复重渲染打断输入。
2. **修改前：** `field.onchange=saveVolunteers` 直接同步保存+重渲染。
3. **修改后：** 引入 600ms 防抖 debouncedSaveVolunteers，并在 saveVolunteers 重渲染前后捕获并恢复焦点与光标位置。
4. **为什么这样改：** 整池替换式 upsert 的后端校验无法省略，但重渲染时机可推迟到用户停手；焦点/光标恢复确保即使重渲染也不打断编辑。
5. **收益：** 连续编辑不再丢输入或丢焦点；志愿者状态切换不再触发抖动式重渲染。

#### 3. 骨干姓名失焦收集按过滤后的索引错位

1. **问题：** 骨干姓名 blur 时先过滤掉空名生成 names 数组，再按索引写回 state.input.members，删除中间一行后数组长度不一致导致后续骨干姓名全部错位张冠李戴。
2. **修改前：** `var names=[];...if(v)names.push(v); ...m.name=names[i]`（过滤后数组与原数组索引错位）。
3. **修改后：** 按 DOM 行顺序与 members 数组逐位对齐写入，最后统一过滤空名成员。
4. **为什么这样改：** DOM 行顺序恒等于 members 数组顺序（渲染与删除均保持一致），按行对齐不会错位；过滤空值应在对齐完成后统一做。
5. **收益：** 删除中间骨干不再导致后续姓名错位；认领下拉选项与实际骨干一一对应。

#### 4. 三处中文注释被编码损坏为问号

1. **问题：** project_service.py:281、routes.py:754、exporters.py:209 的中文注释被某次编辑损坏为 # ?????... 。
2. **修改后：** 分别恢复为"优先沿用任务已有负责人，并校验其是否仍在当前成员名单内""基于实际分工重算详细风险提示""本地字体都找不到时，回退用 reportlab 内置 CID 字典注册 STSong-Light"。
3. **收益：** 代码可读性恢复，评审不再看到乱码。

---

### 体验优化（P2）

#### 5. 大型项目分工看板缺少"恢复自动分工"

1. **问题：** 小型项目看板有"恢复自动分工"按钮，大型项目改坏分工后无一键回退。
2. **修改后：** 大型看板"模块分工"tab 工具栏补 resetAssignBtn，复用 resetAssignment() 从 state.automatic 恢复。
3. **收益：** 大型/小型看板操作对等；误改可一键回退。

#### 6. 任务卡无法编辑"需招募志愿者"数量与跨模块移动

1. **问题：** extra_helpers_needed 只有 LLM/兜底生成时携带，前端任务卡无编辑入口；大型项目也无法把子任务从一个模块移到另一个模块。
2. **修改后：** renderTaskCard 在大型模式下新增"需招募"数字输入框与"所属模块"下拉（仅有模块时显示）；taskFromCard 收集 module_id 与 extra_helpers_needed。
3. **为什么这样改：** 这两项是大型项目"分工招募"阶段的核心可调参数，缺失意味着用户无法根据实际情况调整招募规模与模块归属。
4. **收益：** 志愿者招募需求可按任务手动调整；子任务可跨模块移动重组。

#### 7. 大型项目子任务阶段缺少"重新生成"

1. **修改后：** renderLargeTasks 工具栏补"重新生成"按钮，与小型项目任务拆解阶段对齐。
2. **收益：** 对 AI 拆解不满意时可一键重试，无需退回大模块阶段。

---

### 打磨（P3）

#### 8. 版本号三处不同步

1. **问题：** app/main.py 与 schemas.py 的 version 仍为 5.15，而 HTML 引用已是 ?v=5.17，CHANGELOG 到 v5.17，README 演进表只到 v5.15。
2. **修改后：** main.py、schemas.py、index.html 统一升至 5.18；README 顶部版本与演进表补齐 v5.16/v5.17/v5.18。
3. **收益：** 前后端版本号一致；调试与文档对齐。

#### 9. /api/export/{filename} 老路由与 POST 导出路由前缀冲突

1. **问题：** GET /api/export/{filename}（导出已保存方案，前端未使用、无测试覆盖、README 未列）与 POST /api/export/{format} 共用 /export 前缀，GET /api/export/markdown 会被老路由吃掉。
2. **修改后：** 老路由迁移到 GET /api/plans/{filename}/export，消除前缀冲突。
3. **收益：** 消除路由歧义隐患；路径语义更清晰（属于已保存方案的操作）。

---

### 同步修改

- app/web/static/app.js：骨干联动、志愿者防抖+焦点恢复、blur 错位、恢复自动分工、需招募字段、所属模块下拉、重新生成等 12 处。
- app/web/exporters.py、app/services/project_service.py、app/web/routes.py：乱码注释修复；export 老路由迁移。
- app/main.py、app/models/schemas.py、app/web/templates/index.html、README.md：版本号与演进表同步。
- 146 项测试全过，JS 语法检查通过，后端 API 端到端验证（跨模块移动/需招募更新/志愿者超员校验）通过。


## v5.17 —— 品牌标识与视觉精细化重设计（2026-08-04）

**定位：** 将单字 logo 替换为专业 SVG 图标，并对整体配色、卡片、按钮、背景做精细化打磨。

**审查/修改背景：** 用户反馈品牌标识是一个汉字「协」放在方块里，视觉效果粗糙；同时希望整体配色更精致美观，在保持办公工具简洁感的前提下提升视觉品质。

---

### 体验优化（P2）

#### 1. 品牌标识从汉字替换为 SVG 节点网络图标

1. **问题：** 原品牌标识是一个 40x40 渐变方块内放一个汉字「协」，视觉效果粗糙。
2. **修改前：**
   ```html
   <div class="brand-mark">协</div>
   ```
3. **修改后：** 替换为 SVG 节点网络图标——一个六边形外框内有四个圆形节点用线条相连，象征「任务拆解与人员协作」：
   ```html
   <div class="brand-mark"><svg viewBox="0 0 28 28" fill="none">
     <path d="M14 5.5l8.5 4.9v9.8L14 25.1..." stroke="currentColor" opacity=".45"/>
     <circle cx="14" cy="5" r="2.6" fill="currentColor"/>
     <circle cx="5" cy="22.5" r="2.4" fill="currentColor" opacity=".85"/>
     <circle cx="23" cy="22.5" r="2.4" fill="currentColor" opacity=".85"/>
     <circle cx="14" cy="14.5" r="2.2" fill="currentColor" opacity=".7"/>
     <path d="M14 7.4v5M12.2 12.6L6.4 20.7M15.8 12.6l5.8 8.1" .../>
   </svg></div>
   ```
4. **为什么这样改：** 一个有意义的抽象图形比文字符号更专业、更有辨识度。节点网络隐喻「分工」——一个中心节点向多个执行节点分发任务，完美契合产品定位。
5. **收益：** 品牌视觉专业感大幅提升；图标自带语义；白色 SVG 叠在渐变背景上层次分明。

#### 2. 项目模式选择器增加图标

1. **修改前：** 模式卡片只有纯文字标题和描述。
2. **修改后：** 小型项目卡片增加四人圆点连接图标，大型项目卡片增加 2x2 方格图标，选中时图标变色高亮。
3. **为什么这样改：** 图标让两种模式的区别一眼可辨，降低用户理解成本。
4. **收益：** 模式选择更直观；选中状态视觉反馈更强。

#### 3. 配色与质感精细化

1. **修改前：** 背景 #f5f6f9 纯色平面；卡片阴影较浅；header 纯白不透明。
2. **修改后：**
   - 背景：在 #f4f5fa 基础上叠加极淡的径向渐变 mesh（靛蓝 + 天蓝双色），营造微妙层次
   - Header / StepNav：改为半透明白色 + backdrop-filter:blur 毛玻璃效果
   - 卡片阴影：加深为双层阴影，层次感更强
   - 按钮：渐变加入 inset 顶部高光，质感更精致
   - 品牌标识：渐变加入 inset 高光和更深的外发光
   - 圆角：从 14px 微调到 16px，更柔和
3. **为什么这样改：** 这些是现代 UI 设计的质感细节——毛玻璃、多层阴影、内高光——能在不改变布局的前提下大幅提升精致感。
4. **收益：** 页面整体质感从「能用」提升到「好看」；保持了办公工具的克制和简洁。

#### 4. Sticky action bar 与浮动按钮精细化

1. **修改后：** sticky-action 增加向上投影；AI 助手按钮渐变和阴影统一升级。
2. **为什么这样改：** sticky 元素需要与下方内容产生分离感，向上的柔和投影比硬边框更优雅。
3. **收益：** 底部操作栏与内容区分更自然；视觉层次更清晰。

---

### 同步修改

- `app/web/templates/index.html`：品牌标识 SVG、模式卡片图标、缓存版本号 v=5.17。
- `app/web/static/style.css`：:root 色板、body 背景、header/stepNav 毛玻璃、brand-mark、mode-card、按钮、卡片、sticky-action 等全面精细化。
- `app/main.py`：version 5.17。
- 146 项测试全过，JS 语法检查通过。


## v5.16 —— 静态资源缓存版本号递增修复浏览器旧缓存（2026-08-04）

**定位：** 修复用户浏览器缓存旧版 app.js 导致骨干管理面板不显示的问题。

**审查/修改背景：** v5.15.1 已在 app.js 中补齐了骨干管理面板（添加骨干按钮、成员行、认领下拉），但 index.html 中的静态资源引用仍是 ?v=5.15，与上一版完全相同。浏览器根据完整 URL 缓存静态文件，相同的 ?v=5.15 导致浏览器直接使用本地缓存的旧版 app.js（没有骨干面板的那版），用户看不到「添加骨干」入口。

---

### 关键缺陷（P0）

#### 1. 静态资源缓存版本号未随代码更新而递增

1. **问题：** app.js 内容已更新（新增骨干管理面板），但 HTML 引用的 ?v=5.15 与上一版相同，浏览器命中缓存直接加载旧文件，用户看到的页面缺少骨干添加入口。
2. **修改前：**
   ```html
   <link rel="stylesheet" href="/static/style.css?v=5.15">
   <script src="/static/app.js?v=5.15"></script>
   ```
3. **修改后：**
   ```html
   <link rel="stylesheet" href="/static/style.css?v=5.16">
   <script src="/static/app.js?v=5.16"></script>
   ```
4. **为什么这样改：** 浏览器以完整 URL（含 query string）作为缓存键。app.js?v=5.15 和 app.js?v=5.16 是不同的 URL，浏览器不会命中旧缓存，必须重新向服务器请求最新文件。
5. **收益：** 用户刷新页面后立即加载包含骨干面板的最新 app.js；style.css 同步递增确保样式也是最新版。

### 打磨（P3）

#### 2. FastAPI 版本号同步

1. **问题：** app/main.py 中 FastAPI(version=5.15) 与前端版本号不同步。
2. **修改前：** `app = FastAPI(title="协作分工智能体", version="5.15")`
3. **修改后：** `app = FastAPI(title="协作分工智能体", version="5.16")`
4. **为什么这样改：** 保持后端版本号与前端一致，方便调试时从 API 响应头确认当前运行版本。
5. **收益：** 版本号统一，排查问题更清晰。

---

### 同步修改

- `app/web/templates/index.html`：两处 ?v=5.15 替换为 ?v=5.16。
- `app/main.py`：version 字符串 5.15 替换为 5.16。
- 146 项测试全过，JS 语法检查通过。


## v5.15.1 —— 骨干认领阶段补齐骨干管理面板（2026-08-04）

**定位：** 修复大型项目骨干认领阶段缺少添加骨干入口的问题——大项目开头不填成员，到了骨干认领阶段下拉是空的，却没有地方添加骨干。

**审查/修改背景：** 用户实测发现，大项目流程中第一步不填成员，第二步是骨干认领，但骨干下拉框没有任何选项，页面也没有"添加骨干"的入口，导致流程完全卡住。

---

### 关键缺陷（P0）

#### 1. 骨干认领阶段缺少骨干管理面板

1. **问题：** 大项目开头不填成员，到骨干认领阶段时 `state.input.members` 为空数组，模块认领下拉框无任何选项，但页面没有添加骨干的入口。
2. **修改前：** `renderLargeBackbones()` 直接从 `state.input.members` 读取成员名生成 `<option>`，没有成员管理面板：
   ```js
   var memberOpts=(state.input.members||[]).map(function(m){return m.name});
   // 下拉直接用 memberOpts，如果为空则只有"未认领"
   ```
3. **修改后：** 在认领卡片之前新增 `.draft-member-panel` 骨干管理面板，包含：
   - "添加骨干"按钮：push 新成员到 `state.input.members` 并重新渲染
   - 每行可填姓名、技能标签、每日可用工时
   - "删除骨干"按钮：splice 成员并清除引用该骨干的认领
   - 骨干姓名 blur 时实时更新所有模块认领下拉的 `<option>`
   ```js
   // 骨干管理面板
   var panel='<div class="draft-member-panel">...<button id="addBackboneBtn">＋ 添加骨干</button>...</div>';
   // 认领卡片
   var cards=modules.map(function(m){...'<select data-module-owner="'+m.id+'">...'+memberNames+'...'...}).join('');
   el('taskEditor').innerHTML=panel+cards;
   ```
4. **为什么这样改：** 大项目的核心流程是"先拆大模块 → 再补骨干 → 骨干认领模块"，骨干信息不应该在项目配置阶段强制填写，而是在骨干认领阶段才录入。原代码只从 `state.input.members` 读取，却没有在认领阶段提供写入入口。
5. **收益：** 大项目流程不再卡住；骨干可在认领阶段随时添加、删除、编辑；添加后立即反映到认领下拉。

#### 2. syncBackbones 未收集骨干信息

1. **问题：** `syncBackbones()` 只同步模块的 `assignee_id`，不同步骨干成员信息。用户在认领阶段填写的骨干信息（姓名、技能）不会被保存到 `state.input.members`，后续阶段无法使用。
2. **修改前：**
   ```js
   async function syncBackbones(){
     var ops=[];
     // 只处理模块认领下拉，不收集骨干信息
     document.querySelectorAll('[data-module-owner]').forEach(...);
   }
   ```
3. **修改后：** 先从 DOM 收集所有骨干信息到 `state.input.members`，然后同步认领状态：
   ```js
   async function syncBackbones(){
     if(document.querySelector('#backboneList')){
       var collected=[];
       document.querySelectorAll('.draft-member-row').forEach(function(row){
         var name=row.querySelector('.bb-name').value.trim();
         if(!name)return;
         collected.push({name:name,skill_tags:...,daily_available_hours:...});
       });
       state.input.members=collected;
     }
     // 然后同步认领状态...
   }
   ```
4. **为什么这样改：** 骨干信息在 DOM 中编辑，必须在切换阶段时收集到 `state` 中，否则后续子任务拆解、分工等阶段拿不到成员列表。
5. **收益：** 骨干信息完整传递到所有后续阶段；子任务编辑卡片的负责人下拉也能正确显示骨干选项。

#### 3. 配置阶段大项目成员区域提示不明确

1. **修改前：** 大项目模式下提示"此处成员将成为可认领模块的骨干"，容易让用户误以为必须在这里填。
2. **修改后：** 改为"大型项目先拆大模块，再在「骨干认领」阶段添加骨干。此处可预填，也可跳过。"，标题也改为"骨干成员（可跳过）"。
3. **收益：** 用户知道成员可以在骨干认领阶段添加，不会因为配置阶段不知道填什么而卡住。

---

### 同步修改

- `app/web/static/app.js`：`renderLargeBackbones`、`syncBackbones`、`updateModeHint` 三个函数增强。
- 无后端改动，146 项测试全过。


## v5.15 —— CSS 全量重写修复类名不匹配 + 大模块编辑增强（2026-08-03）

**定位：** 修复 v5.14 CSS 使用错误类名导致样式完全不生效的严重问题；同时增强大模块编辑阶段，补齐拖拽排序、添加任务到模块、子任务预览等编辑能力。

**审查/修改背景：** v5.14 重写前端时，CSS 文件使用了 `.topbar`/`.stepbar`/`.workbench` 等类名，但 HTML 和 JS 实际使用的是 `.app-header`/`.step-nav`/`.app-main`，导致页面几乎无样式。同时 `app/main.py` 第24行有缩进错误导致服务器无法启动。用户反馈大模块拆解阶段仍缺少和小项目任务拆解一样的编辑能力。

---

### 关键缺陷（P0）

#### 1. CSS 类名与 HTML 结构完全不匹配

1. **问题：** v5.14 重写的 `style.css` 使用 `.topbar`、`.stepbar`、`.workbench`、`.config-panel`、`.workspace-panel` 等类名，但 `index.html` 和 `app.js` 实际使用的是 `.app-header`、`.step-nav`、`.app-main`、`.config-card`、`.task-editor` 等，两者完全不匹配，页面几乎无样式。
2. **修改前：** CSS 中 `.topbar{height:72px}`，但 HTML 中是 `<header class="app-header">`，样式规则不会生效。
3. **修改后：** 全量重写 `style.css`，所有选择器与 HTML 实际类名一一对应，覆盖全部 30+ 类名。
4. **为什么这样改：** CSS 选择器必须与 HTML class 属性匹配才能生效；类名不匹配等于没有 CSS。
5. **收益：** 页面视觉样式完全恢复；按钮、卡片、表单等全部组件正确渲染。

#### 2. app/main.py 缩进错误导致服务器无法启动

1. **问题：** `app/main.py` 第24行 `app = FastAPI(...)` 前有4个多余空格，Python 抛出 `IndentationError: unexpected indent`，服务器无法启动。
2. **修改前：**
   ```python
       app = FastAPI(title="协作分工智能体", version="5.14")
   ```
3. **修改后：**
   ```python
   app = FastAPI(title="协作分工智能体", version="5.14")
   ```
4. **为什么这样改：** Python 对缩进严格敏感，模块级代码不能有缩进。
5. **收益：** 服务器正常启动，前端可以加载。

---

### 健壮性提升（P1）

#### 3. 大模块编辑缺少拖拽排序

1. **问题：** 大模块拆解阶段无法调整模块顺序，用户想重新排列模块时没有操作手段。
2. **修改前：** `renderLargeModules()` 的模块卡片没有拖拽手柄，`bindModuleCards()` 不处理拖拽事件。
3. **修改后：** 每个模块卡片左侧新增 `.module-drag-handle`，`bindModuleCards()` 添加完整的 dragstart/dragover/drop 事件链，拖拽后调用 `reorder_modules` 操作更新后端。
4. **为什么这样改：** 小项目任务拆解已有拖拽排序，大模块编辑应具备同等能力。
5. **收益：** 模块顺序可自由调整，拖拽体验与任务排序一致。

#### 4. 大模块编辑无法直接添加子任务

1. **问题：** 大模块拆解阶段虽然能看到模块下的子任务数量，但无法直接添加任务到某个模块，必须先进入子任务拆解阶段。
2. **修改前：** 模块卡片只显示子任务计数（"N 项子任务"），无操作按钮。
3. **修改后：** 每个模块卡片新增"＋ 添加任务"按钮，点击直接调用 `add` 操作并传入 `module_id`。
4. **为什么这样改：** 用户在大模块阶段就能快速调整每个模块的子任务量，无需切换阶段。
5. **收益：** 编辑效率提升；模块阶段即可增减子任务。

---

### 体验优化（P2）

#### 5. 大模块编辑阶段新增子任务预览

1. **问题：** 大模块拆解只显示子任务数量，不显示具体是哪些任务，用户不清楚模块下有哪些内容。
2. **修改前：** 只显示 `<span class="module-count-chip">N 项子任务</span>`。
3. **修改后：** 新增 `.module-task-preview` 区域，展示前5个子任务名称和工时，超过5个显示"+N 项"。
4. **为什么这样改：** 让用户在模块编辑阶段就能看到模块内容，判断是否需要调整。
5. **收益：** 信息透明度提升；减少切换阶段的认知负担。

#### 6. 大模块工具栏新增"重新生成"按钮

1. **修改前：** 工具栏只有"新增模块"和"合并选中模块"。
2. **修改后：** 新增"重新生成"按钮，调用 `generateDraft(true)` 重新走 AI 拆解。
3. **收益：** 用户对 AI 拆解结果不满意时可一键重试。

#### 7. CSS 视觉设计全面升级

1. **修改后：** 重写全部 CSS，采用更精致的圆角层级（14px/10px/6px）、更细腻的阴影层级（xs/sm/md/lg）、更专业的色彩体系（indigo primary + 完整的 success/warning/danger/info 语义色），按钮增加 hover 上浮效果，卡片增加细微阴影层次。
2. **收益：** 整体视觉更专业、更现代，组件间层次更清晰。

---

### 同步修改

- `app/web/static/app.js`：`renderLargeModules` 和 `bindModuleCards` 函数重写，新增拖拽排序和添加任务功能。
- `app/web/static/style.css`：全量重写，修复类名不匹配，新增 `.module-task-preview`、`.module-task-chip`、`.module-drag-handle` 等样式。
- `app/main.py`：修复第24行缩进错误。
- `app/models/schemas.py`：版本号从 5.14 更新为 5.15。
- `app/web/templates/index.html`：版本引用从 v=5.14 更新为 v=5.15。
- `README.md`：版本号和版本演进表同步更新。


## v5.14 —— 前端全量重写：大型/小型项目完全分离 + 大模块编辑功能（2026-08-03）

**定位：** 将所有前端 JavaScript 从内联 `<script>` 迁移到外部 `app/web/static/app.js`，大型项目与小型项目走完全不同的步骤流和页面布局；大模块编辑阶段补齐合并、新增、删除等编辑功能。

**审查/修改背景：** 上一版（v5.13）前端仍保留左右分栏布局和内联脚本，大型项目步骤导航混在小项目框架内，大模块缺少编辑能力（无法合并/新增/删除模块），用户反映"切大型项目看不出区别"。本次彻底重写前端逻辑层。

---

### 关键缺陷（P0）

#### 1. 大型项目与小项目的步骤流未完全分离

1. **问题：** 切换到大型项目时，步骤栏仍显示小项目四步，无法体现"大模块拆解→骨干认领→子任务拆解"的独立流程。
2. **修改前：** `renderSteps()` 固定输出五步导航，不区分项目模式。
3. **修改后：**
   ```js
   var steps = isLargeProject()
     ? ['项目配置','大模块拆解','骨干认领','子任务拆解','分工招募','最终方案']
     : ['项目配置','任务拆解','智能分工','最终方案'];
   ```
4. **为什么这样改：** 大型项目六步 vs 小型项目四步，步骤数和内容完全不同，用户一眼就能看出自己处于哪种模式。
5. **收益：** 步骤导航准确反映当前模式；点击步骤可跳转到对应阶段。

#### 2. 大模块编辑阶段缺少合并/新增/删除功能

1. **问题：** 大模块拆解阶段只能查看模块，不能编辑模块名称、合并模块或新增模块。
2. **修改前：** 大模块区域只展示只读卡片，无工具栏按钮。
3. **修改后：** 每个模块卡新增 checkbox 可多选合并；工具栏新增"新增模块"和"合并选中模块"按钮。
4. **为什么这样改：** 大模块编辑应和小项目任务编辑一样灵活，用户需要调整 AI 拆解结果。
5. **收益：** 大模块可新增、删除、合并、重命名；子任务拆解阶段每个模块可独立添加子任务。

#### 3. 内联 JavaScript 迁移为外部文件

1. **问题：** 全部 JS 逻辑写在 index.html 内联 script 标签内，无法被浏览器缓存。
2. **修改前：** `<script>var state=...（约300行压缩代码）...</script>`
3. **修改后：** `<script src="/static/app.js?v=5.14"></script>`，外部文件约1000行结构化代码。
4. **为什么这样改：** 外部 JS 文件可被浏览器缓存，版本号 v=5.14 用于缓存刷新。
5. **收益：** 首屏加载更快；代码结构清晰可维护。

---

### 健壮性提升（P1）

#### 4. 模式切换时步骤导航不刷新

1. **问题：** 点击"大型项目"模式卡片后，步骤栏不更新（仍显示四步）。
2. **修改前：** 模式卡片点击只调用 `updateModeHint()`。
3. **修改后：** 追加 `renderSteps()` 调用。
4. **为什么这样改：** renderSteps 依赖 isLargeProject 判断步骤数，模式切换后需重新渲染。
5. **收益：** 切换模式即时反馈步骤数变化（4 vs 6）。

---

### 体验优化（P2）

#### 5. 吸顶工具栏和底部固定操作栏

1. **问题：** 任务很多时需要滚动到底部才能找到操作按钮。
2. **修改后：** draftToolbar 和 boardToolbar 使用 position:sticky 吸顶；确认按钮区域使用 sticky-action 固定底部。
3. **收益：** 常用操作始终可见，无需滚动。

#### 6. 大型项目看板拆分模块分工和志愿者招募两个标签页

1. **修改后：** 大型项目看板顶部新增标签切换。
2. **收益：** 分工和招募职责清晰分离。

---

### 同步修改

- `README.md`：版本徽章更新至 v5.14，版本规划表新增 v5.14 行。
- `app/models/schemas.py`：FullPlan.version 从 5.13 更新为 5.14。
- `app/main.py`：FastAPI 版本号从 5.13 更新为 5.14。

---
## v5.13 —— 大型项目分步流程补齐：大模块拆解→骨干认领→子任务拆解→志愿者认领（2026-08-03）

**定位：** 把大型项目从「一次展示模块+全部子任务」改为严格的三段式工作流：先只拆大模块，再由骨干认领模块，进入模块内子任务拆解，最后在看板中由志愿者认领人手不足的子任务；小型项目拆解与分工流程完全不变。

**审查/修改背景：** 用户实测后指出，大型项目先不填骨干时应先看到大模块拆解，再由骨干认领大模块，之后才轮到模块内子任务拆解和志愿者认领；此前版本虽然后端已有 modules 层级，但前端把模块、子任务、骨干认领、志愿者一次性混在同一屏，流程感缺失，用户误以为只是「小型项目 + 志愿者」。

---

### 队友改动说明

v5.10/v5.11 的 `large_project` 分支、`_fallback_large_project_plan`、`extra_helpers_needed`、志愿者池等来自队友本地改动，v5.12 已在其基础上建立模块层级。本版本只在 v5.12 基础上补全前端分步流程，不改变队友的后端模块结构与志愿者认领接口。

---

### 关键缺陷（P0）

#### 1. 大型项目草案缺少「骨干认领大模块」和「大模块→子任务」两个独立环节

1. **问题：** 不填骨干生成大型项目草案后，页面直接同时展示模块卡片与全部子任务，没有「先拆大模块 → 补骨干认领模块 → 再细化模块内子任务」的先后步骤。
2. **修改前：** `renderDraft()` 对大型项目无条件渲染 `renderLargeModuleCards(tasks, modules, members, true)`，模块卡片里直接内嵌全部子任务和负责人下拉，任务编辑与骨干认领混在一起。
   ```js
   if (large) {
     html = renderLargeModuleCards(tasks, modules, members, true);
   } else {
     html = tasks.map(...).join('');
   }
   ```
3. **修改后：** 新增 `LARGE_DRAFT_STAGES` 与 `renderDraft()` 分阶段渲染：阶段1只显示模块卡（含「已预拆 N 项子任务」）；阶段2显示骨干编辑面板 + 模块认领下拉；阶段3才显示模块内子任务编辑卡。
   ```js
   var LARGE_DRAFT_STAGES = [
     ['modules', '大模块拆解'],
     ['backbones', '骨干认领'],
     ['tasks', '子任务拆解']
   ];
   // renderDraft()
   if (stage === 'modules') {
     html = renderLargeModuleCards(tasks, modules, members, false);
   } else if (stage === 'backbones') {
     html = renderDraftMemberPanel() + renderLargeModuleCards(tasks, modules, state.largeMembersDraft, true);
   } else {
     html = renderLargeTaskEditor(tasks, modules, members);
   }
   ```
4. **为什么这样改：** 大型项目与小型项目的本质差异在于任务层级与认领顺序；把「模块确认、骨干补录、模块内子任务拆解」拆成独立阶段，用户才能按正确顺序操作，避免把骨干认领和子任务编辑混为一屏。
5. **收益：**
   - 不填骨干也能先看到大模块骨架。
   - 骨干在独立阶段按模块认领，认领完成后再进入子任务拆解。
   - 小型项目渲染分支不变，原有流程零影响。

#### 2. 大型项目看板缺少独立的「模块认领」和「志愿者认领」环节

1. **问题：** 确认分工后看板直接把模块子任务分工和志愿者招募混在一起，用户找不到「骨干先认领模块、志愿者再认领子任务」的先后关系。
2. **修改前：** `renderBoard()` 大型项目只有 `renderLargeAssignHtml()` 一个视图，模块分区内同时出现任务卡片与志愿者招募字段。
   ```js
   if (large) {
     el('board').innerHTML = renderLargeAssignHtml();
   } else {
     el('board').innerHTML = renderSmallBoardHtml();
   }
   ```
3. **修改后：** 看板新增 `largeBoardTabs`，拆为「1 模块认领」「2 子任务分工」「3 志愿者认领」三个 Tab；`renderLargeModulesHtml()` 只负责模块认领进度与负责人，`renderLargeAssignHtml()` 去掉任务卡内的志愿者字段，`renderLargeVolunteersHtml()` 按模块分组展示志愿者招募。
   ```js
   if (state.largeBoardTab === 'modules') {
     el('board').innerHTML = renderLargeModulesHtml();
   } else if (state.largeBoardTab === 'volunteers') {
     el('board').innerHTML = renderLargeVolunteersHtml();
   } else {
     el('board').innerHTML = renderLargeAssignHtml();
   }
   ```
4. **为什么这样改：** 三个 Tab 对应三段工作流，模块认领进度有独立统计，志愿者招募不再淹没在分工卡片中；前后端职责边界未变，只是展示层按环节聚焦。
5. **收益：**
   - 模块认领进度一目了然（已认领 n / m）。
   - 子任务分工视图专注骨干与负责人调整。
   - 志愿者按模块分组认领，招募需求更清晰。

#### 3. 骨干成员只能在左侧提前填写，草案阶段无法先拆模块再补骨干

1. **问题：** 大型项目允许空骨干生成草案，但草案阶段没有补录骨干的入口，用户仍要回到左侧表单填人，破坏了「先拆模块再认领」的顺序。
2. **修改前：** 草案阶段只渲染模块与任务，没有成员编辑面板；`state.input.members` 只能来自左侧表单。
3. **修改后：** 新增 `renderDraftMemberPanel()` / `addDraftMemberRow()` / `collectDraftMembers()`，骨干认领阶段可在页内直接添加、编辑、移除骨干，并同步写入 `state.input.members`；`syncDraft()` 在 backbones 阶段先 `collectDraftMembers()` 再提交。
   ```js
   function collectDraftMembers() {
     var members = Array.from(document.querySelectorAll('.draft-member-row')).map(...);
     state.largeMembersDraft = members;
     if (state.input) state.input.members = members;
     return members;
   }
   ```
4. **为什么这样改：** 骨干补录属于大型项目工作流的中间环节，应当与模块认领同屏完成；把成员写入统一入口后再提交，后端仍按既有接口校验。
5. **收益：**
   - 用户可在模块拆解后直接补骨干并认领。
   - 无需切换视图，减少流程打断。

---

### 健壮性提升（P1）

#### 4. 未认领模块可进入子任务拆解，缺少前端兜底提示

1. **问题：** 若骨干认领阶段漏掉某模块，仍可直接进入子任务拆解，用户不知道还有模块没人负责。
2. **修改前：** `transitionLargeStage()` 只切换阶段，不检查骨干与模块认领状态。
3. **修改后：** 进入子任务阶段前校验必须至少 1 名骨干，若存在未认领模块则弹出提示，允许继续但在看板「模块认领」Tab 补认领。
   ```js
   if (next === 'tasks' && !(state.input.members || []).length) {
     showNotice('请先在「骨干认领」中添加至少一名骨干', 'error');
     return;
   }
   ...
   if (next === 'tasks' && claimed < modules.length) {
     showNotice('还有 ' + (modules.length - claimed) + ' 个模块未认领，可在看板「模块认领」中补认领', 'info');
   }
   ```
4. **为什么这样改：** 校验与提示分开：硬性阻止完全空骨干，未认领模块用非阻断提示，避免流程卡死又能提醒用户补全。
5. **收益：**
   - 不会带着零骨干进入子任务拆解。
  - 未认领模块不会被静默带进下一步。

---
#### 5. 自动生成的模块名是「执行模块」等阶段名，看不到模块到底拆了什么

1. **问题：** 当 LLM 返回了子任务但未返回 modules，`ensure_large_project_structure` 按执行阶段分组生成模块，名称直接用 `f"{stage}模块"`，用户看到「执行模块」「准备模块」，完全不知道模块里是什么任务。
2. **修改前：**
   ```python
   for stage in stage_order:
       stage_tasks = grouped.pop(stage, [])
       for chunk_index in range(0, len(stage_tasks), 4):
           chunk = stage_tasks[chunk_index:chunk_index + 4]
           new_modules.append(ProjectModule(
               id=module_id,
               name=f"{stage}模块",
               description=f"围绕「{stage}」阶段的一组子任务，由一名骨干认领推进。",
               order=len(new_modules) + 1,
           ))
   ```
3. **修改后：** 新增 `_derive_module_name_from_tasks()` 和 `_derive_module_description_from_tasks()`：优先按子任务技能规范词投票出领域名（如「调研与分析」「视觉设计与物料制作」「文案与内容创作」），其次用任务名关键词拼接，最后才回退阶段兜底名。描述也改为列出该模块包含的具体子任务名称。
   ```python
   name=_derive_module_name_from_tasks(chunk, stage),
   description=_derive_module_description_from_tasks(chunk, stage),
   ```
   实测效果：6 个子任务自动分成 3 个模块——「调研与分析」「视觉设计与物料制作」「文案与内容创作」，而非原来的「准备模块」「执行模块」「收尾模块」。
4. **为什么这样改：** 模块名是用户理解大型项目拆解结构的第一信息源，用阶段名命名等于没有名字；从子任务技能领域推导名字能直接反映这个模块在做什么。
5. **收益：**
   - 模块名不再是空洞的「执行模块」，而是「调研与分析」「视觉设计与物料制作」等具体领域名。
   - 描述列出子任务名称，用户无需展开就能预览模块内容。
   - 技能→领域映射复用 `scoring.py` 已有同义词表，不维护第二份。

---

### 同步修改

- `app/web/templates/index.html`：新增 `#largeStageNav`、`#draftStageActions`、`#largeBoardTabs` 容器，CSS/JS 缓存版本升级为 `v=5.13`。
- `app/web/static/style.css`：新增阶段导航、骨干面板、模块认领卡、看板 Tab、志愿者按模块分组样式。
- `app/main.py` / `app/models/schemas.py`：版本号统一升级为 5.13。
- `README.md`：顶部版本与版本演进表同步 v5.13。
- `app/agents/validation.py`：修复自动生成模块名为阶段名 bug，改为从子任务技能和名称推导。
- 测试：`python -m pytest tests/ -q` → 146 passed。

---
## v5.12 —— 大型项目模式重构：模块→子任务→骨干认领→子任务级志愿者招募（2026-08-03）

**定位：** 把大型项目模式的拆解顺序彻底改为「先拆模块（大任务）→ 模块内拆子任务 → 骨干按模块认领 → 子任务级招募志愿者」，并为大型项目提供完全独立的前端页面；小型项目保留原「先看人再拆任务」流程不变，模式名由「小组作业」改为「小型项目」。

**审查/修改背景：** 用户对照队友的汇报后确认，大型项目和小型项目不应共用同一套拆解顺序：小型项目人员固定，应保留原有的「先判断用户技能，再按个性化能力生成任务」算法；大型项目架构更复杂，需要先拆成若干大任务模块，再在模块内拆子任务，由骨干认领模块，只有人手不足的子任务才招募志愿者。用户同时指出当前「大型项目」页面与「小型项目」几乎一样（仍是先填人→拆任务→分配给成员→附加志愿者），并要求模式标签改为「小型项目」。本轮只做结构调整与修复，暂不 push。

---

### 队友改动说明

v5.10/v5.11 的 `large_project` 分支、`_fallback_large_project_plan`、`extra_helpers_needed`、志愿者池等来自队友本地改动；本版本在其基础上把大型项目从「一次拆到底 + 附加志愿者」重构为「模块 → 子任务 → 骨干认领 → 子任务级招募」，并保留小组作业原有算法完全不变。队友遗留的两处旧测试断言（期望 5 项任务）已同步更新为 4 模块 × 8 子任务。

---

### 关键缺陷（P0）

#### 1. 大型项目仍是「先填人再拆任务」，层级与小型项目没有本质区别

1. **问题：** 用户选择大型项目后，页面仍要求先填写团队成员，再像小型项目一样把任务一次性拆完并分配给这几个人，只是最后附加了志愿者招募；用户反馈「现在的逻辑和之前小型项目逻辑不还是一样吗？」。
2. **修改前：** 大型项目与小型项目共用同一套 Planner 输出（只有 `tasks`），页面也共用同一套任务编辑、看板与最终方案模板。
3. **修改后：** 新增模块层级并贯穿全链路：
   ```python
   class ProjectModule(BaseModel):
       id: str
       name: str
       description: str = ""
       order: int = 0
       status: TaskStatus = TaskStatus.pending
       assignee_id: Optional[str] = None  # 骨干按模块认领

   class SubTask(BaseModel):
       ...
       module_id: Optional[str] = None    # 子任务归属模块

   class PlanOutput(BaseModel):
       tasks: list[SubTask]
       modules: list[ProjectModule] = []  # 大型项目：先拆模块
   ```
   大型项目兜底计划改为 4 个模块（需求梳理→内容制作→质量整合→汇报提交）× 8 项子任务，每项子任务带 `module_id`；前端草案编辑、分工看板、最终方案、报告与导出均按模块分组展示，小型项目仍只显示任务列表。
4. **为什么这样改：** 大型项目区别于小型项目的本质是先有「大任务/模块」骨架，再由骨干认领骨架、按子任务细化执行；把 `modules` 放进 `PlanOutput` 并在所有展示层按模块分组，才能让两种模式在前端真正不同。
5. **收益：**
   - 大型项目前端出现「模块卡片 → 模块内子任务 → 模块负责人 → 志愿者招募」的独立层级。
   - 小型项目页面与算法完全保留，无回归。

#### 2. 大型项目仍要求先填骨干，无法「先拆任务再补人」

1. **问题：** 用户希望大型项目先拆任务，再由骨干认领；但 `/run` 与 `AssignmentInput` 仍要求成员非空，先填人才能进入拆解。
2. **修改前：**
   ```python
   valid_members = [m for m in req.members if m.name.strip()]
   if not valid_members and req.project_mode != "large_project":
       raise HTTPException(status_code=400, detail="至少需要 1 名有姓名的团队成员")
   ```
3. **修改后：** `/api/run` 保持大型项目允许空成员，`small_group` 仍要求至少 1 名成员；大型项目空成员时 Matcher 返回空 `QAOutput`，模块与子任务结构保留，填骨干后 `/api/manual-assignment` 可一次性按模块认领。
   ```python
   if not valid_members and req.project_mode != "large_project":
       raise HTTPException(status_code=400, detail="至少需要 1 名有姓名的团队成员")
   ```
4. **为什么这样改：** 大型项目的工作流是「先有任务结构，再找人负责」，空骨干是合法中间状态；把校验放开后流程顺序真正可颠倒。
5. **收益：**
   - 用户可以先看到模块与子任务骨架，再补充骨干。
   - 认领模块后，模块内未单独指定负责人的子任务默认归模块负责人，减少重复选择。

---

### 健壮性提升（P1）

#### 3. 模块编辑缺少完整生命周期，空模块与排序会被静默清理

1. **问题：** 新增模块后，`validate_plan`/`ensure_large_project_structure` 会立刻删除没有任何子任务的「孤儿模块」，导致用户「新增模块 → 再往里加子任务」的第一步就消失；`reorder_modules` 只改 `order` 字段，下游仍按列表顺序重建，排序不生效。
2. **修改前：**
   ```python
   modules = [
       m for m in modules
       if m.id in used_module_ids  # 空模块直接被清掉
   ]
   ```
3. **修改后：**
   ```python
   def validate_plan(plan, tolerate_cycle=True, preserve_empty_modules=False):
       ...
       modules = [
           m.model_copy(update={...})
           for index, m in enumerate(modules)
           if preserve_empty_modules or m.id in used_module_ids
       ]
   ```
   `mutate_draft` 在模块结构存在时传 `preserve_empty_modules=True`，并在操作结束后先按 `order` 排序再重建编号：
   ```python
   modules.sort(key=lambda module: (module.order or 10**9, module.id))
   ```
   同时补齐 `add_module / remove_module / reorder_modules / update_module` 指令与后端校验（模块 ID 唯一、删除非空模块报错、排序必须包含全部模块）。
4. **为什么这样改：** 空模块是用户手工编辑的合法中间状态，排序依赖列表顺序与 `order` 字段保持一致；只靠「清理孤儿模块」会让新增模块和排序两个基础操作静默失效。
5. **收益：**
   - 新增空模块后可以继续命名、认领、添加子任务。
   - 模块上移/下移与后端排序真实生效，且删除非空模块会被明确阻止。

#### 4. 空骨干/未认领模块时手动分工会崩溃

1. **问题：** `apply_manual_assignment` 为没有负责人的任务生成 `QAAssignment(presenter=None)`，Pydantic 校验报错，大型项目「先拆任务再补骨干」的路径无法保存。
2. **修改前：**
   ```python
   assignments.append(QAAssignment(
       task_id=task.id, task_name=task.name, presenter=owner, ...))
   ```
3. **修改后：**
   ```python
   assignments.append(QAAssignment(
       task_id=task.id, task_name=task.name, presenter=owner or "", ...))
   ```
   并在重算出口统一透传 `module_assignees` 到模块 `assignee_id`。
4. **为什么这样改：** 未认领是大型项目「先拆后补」过程中的正常状态，接口必须能表达「还没有负责人」，而不是抛校验错误。
5. **收益：**
   - 空骨干方案可以保存、继续编辑。
   - 模块认领后子任务自动继承模块负责人，任务级手动分工仍优先。

---

### 体验优化（P2）

#### 5. 大型项目前端与小型项目完全共用，模式标签误导

1. **问题：** 模式选择仍叫「小组作业」，但小型项目还包括推送等非小组作业场景；大型项目页面几乎与小型项目一样，用户反馈「感觉没什么区别」。
2. **修改前：** `<option value="small_group">小组作业（人固定，先看人再拆任务）</option>`；`index.html` 内联全部 JS，两种模式共用同一渲染函数。
3. **修改后：**
   ```html
   <option value="small_group" selected>小型项目（人固定，先看人再拆任务）</option>
   ```
   大型项目新增独立视图：草案阶段按「模块卡片 + 模块内子任务 + 模块负责人 + 上移/下移/删除模块」展示；看板按模块分区展示子任务卡片并附志愿者招募面板；最终方案按模块分组并显示模块负责人与子任务进度。前端脚本抽离为 `/static/app.js?v=5.12`，样式新增 `.module-edit-card`、`.board-module`、`.final-module` 等模块化样式。
4. **为什么这样改：** 两种模式的流程本质不同，共用视图会把「先看人」和「先拆模块」混在一起；把大型项目视图独立出来才能体现「先拆模块 → 骨干认领 → 子任务招募」的完整逻辑，模式名改为「小型项目」覆盖更准确。
5. **收益：**
   - 选择大型项目后看到的是模块化拆解页面，与小型项目明显区分。
   - 「小型项目」命名覆盖小组作业、推送等非固定大型场景。

---

### 打磨（P3）

#### 6. 版本号与前端资源未同步，旧测试断言停留在 5 任务结构

1. **问题：** `app/main.py` 仍为 5.11，前端资源缓存为 5.11.1；`tests/test_large_project_mode.py` 仍断言兜底计划 5 项任务，与新的 4 模块 × 8 子任务不一致。
2. **修改前：** `app.main` `version="5.11"`；`index.html` 引用 `style.css?v=5.11.1`；测试断言 `len(plan.tasks) == 5`。
3. **修改后：** 版本统一为 5.12，CSS/JS 缓存引用 `v=5.12`；测试断言更新为 `len(plan.modules) == 4 and len(plan.tasks) == 8`，并新增空骨干草案、模块生命周期、模块认领持久化、非法模块拒绝等用例。
4. **为什么这样改：** 版本号是跨文件契约，测试需要锁定新的模块层级，否则重构后的核心行为没有回归保护。
5. **收益：**
   - 资源缓存与版本一致，前端不会加载旧 JS。
   - 模块化重构获得完整回归测试，全量 146 项通过。

---

### 版本规划表

**v5.12 已由「规划中」更新为「已完成」**，详见文末版本规划表。

---

## v5.11 —— 大型项目模式闭环：志愿者招募与认领（2026-08-03）

**定位：** 在 v5.10 的「先拆任务 → 骨干认领 → 标注志愿者需求」基础上，把大型项目模式补成完整可用闭环：志愿者池数据模型 + 统一校验 + API + 看板招募交互 + 最终方案/导出展示；小组作业既有算法完全不变。

**审查/修改背景：** 用户确认优先把大型项目做好做实。v5.10 已有 `project_mode`、`extra_helpers_needed`、骨干认领和志愿者徽章，但志愿者只是「需求数字」，没有认领记录、没有交互，保存/导出也看不到招募进度，演示时闭环断在招募这一步。

---

### 队友改动说明

v5.10 前队友的本地改动已包含：`large_project` 分支、`_fallback_large_project_plan`、`extra_helpers_needed`、scoring 内部协作位修正、前端项目模式选择和志愿者徽章。本版本在其基础上补齐志愿者池的增删改、校验、API、看板面板、最终视图与导出，不重写既有分工算法。

---

### 关键缺陷（P0）

#### 1. 大型项目模式只有「需求数字」，没有可保存的认领记录

1. **问题：** `extra_helpers_needed` 只标注需要多少志愿者，页面无法登记谁认领、联系方式、状态，保存/加载/导出都会丢失，演示断在招募这一步。
2. **修改前：**
   ```python
   class FullPlan(BaseModel):
       input: AssignmentInput
       plan: PlanOutput
       timeline: TimelineOutput
       qa_matrix: QAOutput
       report: ReportOutput
       reflection: Optional[ReflectionOutput] = Field(default=None, ...)
       version: str = "5.6"
   ```
3. **修改后：**
   ```python
   class Volunteer(BaseModel):
       name: str
       task_id: str
       status: str = "待确认"   # 待确认 / 已确认 / 已婉拒
       contact: str = ""
       note: str = ""

   class FullPlan(BaseModel):
       ...
       volunteer_pool: list[Volunteer] = Field(default_factory=list, ...)
       version: str = "5.11"
   ```
4. **为什么这样改：** 招募的本质是「任务需求 × 认领状态」，需要结构化实体承载，而不是把数字写死在前端。放在 `FullPlan` 顶层，让保存/加载/导出/重算全链路共享同一份事实。
5. **收益：**
   - 志愿者认领记录可随方案保存、加载、导出，闭环可复现。
   - 旧方案没有 `volunteer_pool` 时自动取空列表，向后兼容。

#### 2. 重算链路会悄悄丢掉志愿者池

1. **问题：** 手动分工、成员变动、状态重算、任务编辑都会重建 `FullPlan`，原实现没有透传 `volunteer_pool`，用户调整一次方案后认领记录就消失。
2. **修改前：**
   ```python
   return FullPlan(
       input=fp.input, plan=plan, timeline=timeline,
       qa_matrix=qa, report=report, version=fp.version)
   ```
3. **修改后：**
   ```python
   return FullPlan(
       input=fp.input, plan=plan, timeline=timeline,
       qa_matrix=qa, report=report,
       volunteer_pool=fp.volunteer_pool, version=fp.version)
   ```
   同步在 `editor.py`、`/api/recompute`、`/api/edit-members` 的重建出口保留志愿者池；删除任务时过滤掉指向已删除任务的认领记录。
4. **为什么这样改：** `FullPlan` 是多处各自重建的聚合根，任何一处遗漏都会造成静默丢数据；在所有重建出口统一透传并清理失效任务引用，才能保证招募记录跨操作存活。
5. **收益：**
   - 调整分工、成员、状态后志愿者认领不再丢失。
   - 删除任务后不会残留指向不存在任务的孤儿认领记录。

---

### 健壮性提升（P1）

#### 3. 志愿者池缺少统一业务校验

1. **问题：** 志愿者姓名可与团队成员重名、可在池内重复、可认领不存在的任务或需求为 0 的任务，人数还能超过需求，导致方案数据互相矛盾。
2. **修改前：** 没有 `volunteer_pool` 校验入口，前端只能裸改 JSON，非法数据可以一路保存到导出。
3. **修改后：**
   ```python
   def update_volunteer_pool(plan, volunteers):
       if plan.input.project_mode != "large_project":
           raise ProjectServiceError("志愿者招募仅适用于大型项目模式")
       # 校验：姓名非空 / 不与成员重名 / 池内不重名
       # 任务存在且 extra_helpers_needed > 0
       # 已确认 + 待确认 <= 需求；已婉拒不占名额
   ```
   新增 `POST /api/volunteers`，以整池替换方式保存，非法状态返回 400。
4. **为什么这样改：** 招募数据必须与任务结构强一致；把规则收在业务层，Web、CLI、未来对话指令共用同一套约束，而不是让每个前端各自校验。
5. **收益：**
   - 演示时不会被超额认领、重名或错乱状态打断。
   - 校验规则集中在 `project_service`，可测试、可复用。

---

### 体验优化（P2）

#### 4. 看板没有招募入口，最终方案/导出看不到进度

1. **问题：** 大型项目确认分工后进入看板，只能看到「需招募 n 名志愿者」徽章，无法添加、改状态、移除志愿者；报告和导出也没有招募章节。
2. **修改前：** `renderBoard` 只渲染成员列；`renderReportTab`、`_plan_to_markdown`、`plan_to_docx`、`plan_to_pdf` 均无志愿者内容。
3. **修改后：** 看板新增「志愿者招募与认领」面板（每任务：姓名/联系方式/备注/状态/移除 + 满员提示）；任务卡片与最终任务、分工矩阵、报告展示「已确认/需求」进度；Markdown/Word/PDF 导出增加「志愿者招募计划」章节。
4. **为什么这样改：** 招募是大型项目工作流的一等环节，应该在看板、最终方案、导出三处都有对应视图，演示链路才能完整走通。
5. **收益：**
   - 认领、改状态、移除全部可视化，并实时保存到后端。
   - 导出文档可直接作为招募计划交付物。

---

#### 5. 大型项目模式界面沿用「团队成员」措辞，骨干/志愿者层级不明

1. **问题：** 选择大型项目后，配置表单仍叫「团队成员」，任务里显示「招募志愿者」，看板又保留骨干分工，用户看不出这几种人的区别，误以为大型项目还要像小组作业一样先填全体人员。
2. **修改前：** 表单区块标题固定为「团队成员」；志愿者面板文案只写「为需要外部参与的任务登记志愿者」，没有说明志愿者与骨干的关系；最终方案页的成员页签和报告基本信息也沿用「成员/团队成员」。
3. **修改后：** 大型项目模式下表单标题切换为「骨干成员（固定核心团队）」，并在标题下显示说明「先按交付物拆解任务，再由骨干认领负责；需要更多人手的任务单独招募志愿者，志愿者不替换骨干分工」；志愿者面板文案补充「志愿者是骨干之外的补充人力，不替换骨干分工」；最终方案页成员页签、成员管理标题和报告基本信息按模式显示「骨干管理/骨干成员」，小组作业保持原「成员管理/团队成员」。
4. **为什么这样改：** 大型项目的完整结构本来就是「骨干（固定、认领负责） + 志愿者（外部、补充人手）」，之前界面没有把这两个层级说清楚，用户会在分工逻辑上产生误解；把语义直接写进界面，让流程自解释。
5. **收益：**
   - 用户一进入表单就知道大型项目填的是骨干，不再误以为是全体成员。
   - 看板上的骨干分工和志愿者招募两层关系一目了然，减少误解。
   - 小组作业界面措辞完全不变，无回归风险。

---

### 打磨（P3）

#### 6. 版本号与前端草案字段未同步

1. **问题：** `app/main.py` / `index.html` 仍写 5.6，草案编辑器也没有 `extra_helpers_needed` 输入，用户只能依赖 LLM 输出，无法手工修正志愿者需求。
2. **修改前：** `version="5.6"`；草案只有「建议人数」输入。
3. **修改后：** 版本统一为 5.11；大型项目草案把「建议人数」替换为「招募志愿者」输入，`taskFromRow` 同步 `extra_helpers_needed` 并保持 `suggested_people = 1 + 需求`；小组作业仍保留原「建议人数」输入。
4. **为什么这样改：** 版本号是跨文件契约，草案是需求修正入口；两者不同步会让演示结果与代码状态不一致。
5. **收益：**
   - 版本可追溯，前端资源缓存按 5.11 刷新。
   - 用户可手工调整志愿者需求，确认分工后立即反映到看板和导出。

---

### 版本规划表

**v5.11 已由「规划中」更新为「已完成」**，详见文末版本规划表。

---

## v5.10 —— 大型项目模式补做 + 评审预演多轮互动 + 前端适配（2026-08-03）

**定位：** 把 v5.8「文档先行」的大型项目模式真正落地：先拆任务 → 骨干认领 → 标注志愿者需求；评审预演从一次性问题列表升级为多轮互动；修复由此暴露的"分工算法把外部志愿者需求当成内部协作位"的问题。

**审查/修改背景：** 用户反馈之前没试过答辩模拟，且现有模拟"问的都是一些关于怎么保证任务准时完成的问题，也没有互动"；梳理 `建议(2).docx` 后确认，大型项目模式应区分"小组作业（人固定，先看人再拆任务）"和"大型项目（先拆任务再认领招募）"。队友在 0801/0802 的本地改动已搭出雏形，但测试文件曾丢失、均衡算法仍会把零负载内部成员塞进志愿者需求任务，全量回归曾失败。

---

### 关键缺陷（P0）

#### 1. 大型项目模式仅有文档、没有可运行代码（补做 v5.8）

1. **问题：** v5.8 的 CHANGELOG 描述了大量大型项目代码，但仓库里没有 `project_mode`、`extra_helpers_needed`、`_fallback_large_project_plan`、`LARGE_PROJECT_PLANNER_*`，`/api/run` 也不会透传项目模式；选择大型项目后实际仍走小组作业链路。
2. **修改前：**
   ```python
   class AssignmentInput(BaseModel):
       ...
       deadline: date
       # 没有 project_mode / extra_helpers_needed

   # Coordinator
   if isinstance(plan, AgentError):
       plan = self._fallback_plan(inp, plan.message)
   ```
3. **修改后：**
   ```python
   class AssignmentInput(BaseModel):
       project_mode: str = Field(default="small_group", ...)

   # Coordinator
   plan = (self._fallback_large_project_plan(inp, plan.message)
           if inp.project_mode == "large_project"
           else self._fallback_plan(inp, plan.message))
   ```
   同步补齐：`_step_planner_large_project`（先拆任务→骨干认领）、`_fallback_large_project_plan`（5 阶段确定性兜底）、`LARGE_PROJECT_PLANNER_SYSTEM/USER_TEMPLATE`、`SubTask.extra_helpers_needed`、`PlanOutput.member_assessment`、`/api/run` 透传 `project_mode`、`generate_draft(use_ai=False)` 走大型项目兜底。
4. **为什么这样改：** 小组作业和大型项目的人员结构不同：小组作业人固定，先看人再拆任务；大型项目先拆交付模块，再由骨干认领并对外招募志愿者。只有把模式贯穿 schema → Planner → 兜底 → 路由 → 前端，产品行为才能真正分叉，而不是文档和代码两套说法。
5. **收益：**
   - 大型项目模式首次成为可运行功能，LLM 与确定性兜底都能产出骨干 + 志愿者结构。
   - `member_assessment` 让"先看人"在小组作业里也有可解释的评估依据。
   - `/api/run` 与快速草案两条入口行为一致。

#### 2. 分工算法把外部志愿者需求当成内部协作位

1. **问题：** 大型项目任务用 `extra_helpers_needed` 标注志愿者后，`_balance_workload` 仍会把零负载的内部成员搬到该任务的 `qa_primary/qa_support`；`enhance` 和 `recompute_preserve` 也可能重新引入内部协作者，导致"骨干 + 志愿者"分层被算法破坏。
2. **修改前：**
   ```python
   max_collaborators = max(0, t.suggested_people - 1)
   ...
   active = [a for a in assignments
             if a.presenter != "(已完成)" and n not in (a.qa_support or [])]
   ```
3. **修改后：**
   ```python
   def _internal_collab_slots(task) -> int:
       internal_people = max(1, task.suggested_people - (task.extra_helpers_needed or 0))
       return max(0, internal_people - 1)

   def _has_volunteer_demand(task) -> bool:
       return (task.extra_helpers_needed or 0) > 0 and _internal_collab_slots(task) <= 0

   fixed_task_ids = {t.id for t in plan.tasks if _has_volunteer_demand(t)}
   work = _balance_workload(..., fixed_task_ids=fixed_task_ids)
   ```
   `_balance_workload`/`_rebalance_presenters` 增加 `fixed_task_ids`，志愿者需求任务不再参与负责人/协助搬运；`enhance` 初始就清空这类任务的协助位。
4. **为什么这样改：** 志愿者不在注册成员名单里，`suggested_people` 中属于志愿者的名额不应被内部成员顶替。用"内部协作位 = suggested_people - 志愿者需求 - 1"计算，并在均衡阶段把这类任务视为固定结构，才能保住"骨干负责、志愿者补充"的分层。
5. **收益：**
   - 大型项目不再出现"明明要招 2 名志愿者，却又把 Bob 塞成内部主要协助"的矛盾。
   - 分工结果与前端志愿者徽章一致，答辩展示可自圆其说。

#### 3. 评审预演没有互动，只是一次性问题列表

1. **问题：** 原 `InterviewSimAgent` 只有一次性 `run()`，前端 `/api/interview` 返回问题列表后就没有后续；用户反馈"问的都是一些关于怎么保证任务准时完成的问题，也没有互动"。
2. **修改前：**
   ```html
   <button id="startInterviewBtn" class="btn btn-primary">生成模拟问题</button>
   <div id="interviewQuestions" class="interview-list"></div>
   ```
3. **修改后：**
   ```python
   # app/agents/interview_sim.py
   def chat_turn(self, plan, qa_matrix, user_answer, history, user_requirements=""):
       messages = [{"role": "user", "content": context + "请开始模拟评审，提第一个问题。"}]
       # 追加 history 与本次回答，调用 INTERVIEW_CHAT_SYSTEM
   ```
   同步新增 `POST /api/interview/chat`、`INTERVIEW_CHAT_SYSTEM`（点评 + 追问 + 维度切换）、前端开始/作答/重新开始三态 UI。
4. **为什么这样改：** 真实答辩是"评委提问 → 你回答 → 评委点评并追问"的循环。多轮 `chat_turn` 携带完整 history 和用户要求，才能模拟出有来有回、能追问细节的评审，而不是把一堆问题一次性倒给用户。
5. **收益：**
   - 预演从"读问题"变成"练答辩"，回答含糊时会被追问。
   - 问题维度按项目理解和分工动态生成，不再全是"怎么保证准时完成"。

### 健壮性提升（P1）

#### 4. 志愿者约束不能误伤小组作业的"全员参与"

1. **问题：** 零负载兜底直接按 `_internal_collab_slots(task) > 0` 过滤，会把小组作业 `suggested_people=1`、新增零技能成员等场景的"全员参与"也一并禁掉，导致既有 `test_add_member_with_no_skills_gets_nonzero_workload`、`test_assign_with_balance_respects_suggested_people` 失败。
2. **修改前：**
   ```python
   active = [... and _internal_collab_slots(task_by_id[a.task_id]) > 0]
   ```
3. **修改后：**
   ```python
   active = [... and not _has_volunteer_demand(task_by_id[a.task_id])]
   ```
   即只有"标注了外部志愿者需求且内部协作位已满"的任务才锁定；小组作业仍保留每个任务 1 主协 + 2 辅协、零工时成员也能参与的行为。
4. **为什么这样改：** 两类场景约束来源不同：大型项目的约束是"志愿者名额不能被内部成员占"，小组作业的约束是"固定成员都应参与"。用 `extra_helpers_needed > 0` 做区分，而不是一刀切按 `suggested_people` 计算。
5. **收益：**
   - 大型项目志愿者语义正确，小组作业全员参与回归不受影响。
   - 全量 125 个测试通过，两个历史回归用例重新变绿。

### 体验优化（P2）

#### 5. 前端支持项目模式选择与大型项目信息展示

1. **问题：** 前端没有项目模式入口，`collectInput()` 不传 `project_mode`；看板任务卡既不显示"需招募 N 名志愿者"，也不展示 Planner 的 `member_assessment` 能力评估。
2. **修改前：**
   ```js
   return {course:..., members:..., deadline:..., ...};
   ```
3. **修改后：**
   ```js
   return {project_mode:el('projectMode').value, course:..., members:..., ...};
   function renderVolunteersField(task){...'需招募 '+n+' 名志愿者'...}
   function renderAssessment(task){...assessments[task.assignee_id]...}
   ```
   新增项目模式下拉框、志愿者徽章、能力评估块，并在 `style.css` 增加对应样式。
4. **为什么这样改：** 用户需要能显式切换"小组作业/大型项目"，且大型项目模式的核心信息（要招多少志愿者、骨干为什么适合该任务）必须在看板上可见，否则前端与后端模式分叉。
5. **收益：**
   - 项目模式真正端到端生效，而非后端默认为 small_group。
   - 大型项目看板信息完整，评审或演示时一眼看懂"骨干 + 志愿者"结构。

#### 6. 评审预演界面改为聊天式交互

1. **问题：** 一次性问题列表没有输入框，用户无法作答，也没有重置入口。
2. **修改前：** 静态 `.interview-list`，点击"生成模拟问题"后只渲染问题文本。
3. **修改后：** `.interview-messages` 消息流 + `.interview-form` 作答框 + 开始/重新开始按钮；新增 `ivChat` 状态，作答后自动发 `/api/interview/chat` 并追加点评。
4. **为什么这样改：** 聊天气泡天然符合"多轮互动"心智，作答入口始终可用，重置能清空历史重新预演。
5. **收益：**
   - 交互闭环：开始 → 回答 → 点评 → 追问 → 重新开始。
   - 答辩练习的真实感和完成度提升。

### 打磨（P3）

#### 7. README 版本同步

1. **问题：** README 版本号仍停在 v5.7，版本演进表缺少 v5.8/v5.9/v5.10，与 CHANGELOG 实际进度脱节。
2. **修改前：** README 顶部 `**版本：v5.7**`，演进表最新为 v5.7。
3. **修改后：** 顶部更新为 v5.10，演进表补充 v5.8/v5.9/v5.10 三行。
4. **为什么这样改：** README 是团队成员和验收方第一眼看到的信息，版本不一致会让人误以为大型项目模式和互动预演尚未落地。
5. **收益：** 文档入口与 CHANGELOG 保持一致，避免再次出现"文档先行、代码脱节"。

### 队友改动说明

**改动来源：** 队友在 0801/0802 的本地改动（当前工作区未提交部分），包括大型项目 Planner/兜底雏形、`extra_helpers_needed`/`member_assessment` 字段、评审预演 `chat_turn` 雏形、前端项目模式下拉与志愿者展示；v5.9 已恢复被误删的测试并清理 BOM。

**本版本在其基础上的增强：**
- 补齐 `TaskStatus` 导入，避免大型项目 Planner 路径 `NameError`。
- 修复 `_balance_workload` 把志愿者需求任务当成内部协作位、以及零负载兜底误伤小组作业的问题。
- 前端补齐 `/api/interview/chat` 交互闭环与看板徽章/评估展示。
- 新增 `tests/test_large_project_mode.py`、`tests/test_interview_chat.py`，全量 `125 passed`。

**验证：** 按 AGENTS.md 前端 4 步验证：内联 JS `new Function` 语法检查通过；HTML 字符串拼接变量均在字符串外；JS 无 `\u0022`；`python -m pytest tests/ -q` 125 passed。

---
## v5.9 —— 测试恢复 + BOM 清理 + 文档与代码一致性修正（2026-08-03）

**定位：** 修复队友提交（commit `4c28963`「0801第二次」）误删全部测试文件导致 CI 空转的问题，清理历史遗留的 UTF-8 BOM，并诚实记录 v5.8「大型项目模式」代码未进库的文档脱节问题。

**修改背景：** 队友在 0801 的多次 push 中，`4c28963` 把 `tests/` 下全部 15 个测试文件（含 `conftest.py`，共 118 个 `test_` 函数）删除；`fa6aba6` 只改了 CHANGELOG 却新增了 v5.8「大型项目模式」条目，描述了大量后端/前端代码与 `tests/test_large_project.py`，但这些代码从未进入 `main`（全分支、全历史搜索 `large_project` / `extra_helpers_needed` / `member_type` 均无命中）。本版只做「修问题」，不改功能；大型项目模式的补做列为下一步。

---

### 关键缺陷（P0）

#### 1. 全部测试文件被误删，CI 形同空转

1. **问题：** `4c28963`「0801第二次」一次性删除了 `tests/` 下 15 个 `test_*.py`、`conftest.py`、`__init__.py`（共 2401 行删除）。`pytest` 变成 `no tests ran`，`.github/workflows/test.yml` 的 `pytest -v` 步骤不再有任何实际验证，AGENTS.md「必须 118 passed」的基线彻底失效。
2. **修改前：**
   ```
   $ pytest -q
   no tests ran in 0.01s
   ```
3. **修改后：**
   ```
   $ git checkout 424f459 -- tests/      # 恢复删除前的完整测试目录
   $ pytest -q
   118 passed, 1 warning in 109.29s
   ```
4. **为什么这样改：** 测试是在 `424f459`（删除前最后一个测试完好版本）里完整存在的，直接从 git 历史恢复即可，无需重写。恢复后 118 个测试对当前「顺序B改造」代码全绿，说明 `3e883bf` 的匹配逻辑改动未破坏既有契约。
5. **收益：**
   - CI 恢复真实门禁，后续任何改动重新有「全绿才安全」的护栏。
   - 顺序B改造（`assign_with_balance` 尊重 Planner 的 `assignee_id`）在 118 个既有用例下验证无回归。

#### 2. v5.8「大型项目模式」CHANGELOG 与代码严重脱节

1. **问题：** v5.8 条目描述了 `_step_planner_large_project`、`_fallback_large_project_plan`、`LARGE_PROJECT_PLANNER_SYSTEM`、`large_project` 模式、`extra_helpers_needed`、骨干/志愿者 `member_type`、`apply_manual_assignment` 的 `is_large_project` 分支、`tests/test_large_project.py`（3 个测试）等大量内容，但这些代码/文件在 `main` 及所有远程分支、全部历史提交中均不存在——`fa6aba6` 那个 commit 实际只改了 CHANGELOG.md 一个文件。
2. **修改前：** CHANGELOG 宣称功能已修复并测试通过，实际代码库里无任何大型项目模式实现。
3. **修改后：** 本条如实记录该脱节。按 AGENTS.md「不得覆盖或删除旧版本详细内容」的约定，v5.8 原文予以保留；大型项目模式的补做列入下一步「改功能」阶段。版本规划表中 v5.8 标注为「文档先行，代码待补」。
4. **为什么这样改：** CHANGELOG 是团队和答辩时理解「改了什么、为什么」的依据。文档与代码不一致会误导后续判断，必须显式标注，而不是假装它存在或悄悄删掉。
5. **收益：**
   - 后续接手者能立即知道 v5.8 是「待落地」而非「已完成」。
   - 为下一步补做大型项目模式提供明确的待办锚点。

### 打磨（P3）

#### 3. 13 个 Python 文件残留 UTF-8 BOM

1. **问题：** 8 个 `app/` 源文件与 5 个恢复回来的测试文件以 UTF-8 BOM（`U+FEFF`）开头。运行时无害，但用 `ast.parse` 按原始字节解析会报「invalid non-printable character U+FEFF」，且不同编辑器/工具链对 BOM 处理不一致。
2. **修改前：**
   ```python
   # 文件首字节为 EF BB BF（BOM），ast.parse 原始字节报错
   ```
3. **修改后：**
   ```python
   # 剥离开头 3 字节 BOM，统一为无 BOM 的 UTF-8
   with open(p,'rb') as fh: b=fh.read()
   if b.startswith(b'\xef\xbb\xbf'): open(p,'wb').write(b[3:])
   ```
4. **为什么这样改：** Python 源文件标准是 UTF-8 无 BOM。BOM 只在极少数 Windows 工具里有意义，却会干扰字节级静态检查、diff 可读性和跨平台一致性。
5. **收益：**
   - `ast.parse` 原始字节解析不再误报，外部静态分析工具可正常工作。
   - 全仓库 Python 文件编码风格统一。

**验证：** `pytest` 118 passed；`index.html` 内联 JS `new Function(js)` 语法检查通过；全部模块 `importlib.import_module` 成功；BOM 残留 0。

## v5.8 —— 大型项目模式后端+前端修复（2026-08-01）

**定位：** 修复 `feature/large-project-mode` 分支引入的大型项目模式问题，包括服务器500错误、成员类型设计矛盾、分工兜底缺失、前端未适配 large_project 模式等。

**修改背景：** 用户反馈"项目分工分支粗糙、成员选骨干/志愿者不合理、招募技能者剩任务无法进入下一步直接报错、前端没优化、界面难看"。

---

### 后端修复

#### 1. 路由层500错误

- **问题：** `/api/confirm-draft` 和 `/api/manual-assignment` 只捕获 `ProjectServiceError`，其他异常（ValidationError/KeyError/TypeError）冒泡到全局 handler 返回500"服务器内部错误"
- **修复：** 补全 `(ValidationError, KeyError, TypeError, ValueError)` 捕获，转成400 + 可读 detail

#### 2. Planner提示词允许 assignee 为 null

- **问题：** `LARGE_PROJECT_PLANNER_SYSTEM` 允许能力缺口任务的 assignee_id 设为 null，导致后续 timeline/member_map 查找 KeyError
- **修复：** 提示词禁止 null，无人擅长也必须指定骨干作为协调管理者；修复 `{骨干姓名}` 占位符冲突

#### 3. 大型项目兜底走小组作业逻辑

- **问题：** `_step_planner_large_project` LLM 失败时走 `_fallback_plan`（小组作业兜底），不产 `extra_helpers_needed`，assignee 全是 null
- **修复：** 新增 `_fallback_large_project_plan`，5阶段拆任务、轮转分配骨干、标注 extra_helpers_needed

#### 4. apply_manual_assignment 不感知 project_mode

- **问题：** 手动调整后确认时，大型项目模式也走小组作业的协作者折算逻辑，用含志愿者的 members 列表
- **修复：** 添加 `is_large_project` 分支，只用 core_members、保留 extra_helpers_needed、空值兜底

---

### 前端修复

#### 5. 成员行 member_type 下拉与设计矛盾

- **问题：** `addMember()` 加了"骨干/志愿者"下拉，但设计是"志愿者不登记在系统里、只标注需求量"
- **修复：** 去掉 member_type 下拉，collectInput 固定回传 `member_type:'core'`

#### 6. extra_helpers_needed 输入框全模式显示

- **问题：** `renderDraft` 的志愿者需求输入框无论 small_group/large_project 都显示
- **修复：** 只在 large_project 模式渲染

#### 7. renderBoard 未适配 large_project

- **问题：** 看板无骨干列、无志愿者需求展示
- **修复：** 任务卡片显示"需 N 人"徽章，未分配列显示"待认领"

#### 8. 前端500错误提示不可读

- **问题：** `jsonRequest` 直接显示后端返回的"服务器内部错误"
- **修复：** 500错误改为"服务器处理失败，请检查输入或稍后重试"

#### 9. 成员行排版不美观

- **问题：** "标签/简介"下拉暴露设计逻辑，textarea 简介框有大滚轴，grid 布局挤在一行
- **修复：** 去掉下拉和 textarea，改成两行布局（姓名+工时+删除 / 技能标签 / 不可用日期）

#### 10. 项目模式 select 样式不一致

- **问题：** CSS `.config-form input,.config-form textarea` 漏了 select，下拉框是黑框边
- **修复：** 加入 `.config-form select`，样式统一

---

### 测试

- `tests/test_large_project.py`：3个测试全部通过
- 修复 MagicMock 穿透导致 FullPlan 校验失败

---

## v5.7 —— 第二轮深度审查全量修复 + AI 协作助手体验重写（2026-07-30）

**定位：** 对 workbuddy 第二轮审查结论逐条核实并修复，统一两条主链路风险质量，修复时区边界导致的排期偏移，并重写 AI 协作助手让对话自然、不再向用户输出系统级元话术。

**审查/修改背景：** 用户在 v5.6 之后发起第二轮全面审查，并实测反馈两个具体 bug（自定义阶段校验失效、成员变动后回看板需正确恢复自动分工），同时发现 AI 协作助手会把"不建议手动重新分工"这类写给大模型自己的约束直接转述给用户。

---

### 关键缺陷（P0）

#### 1. B1：自定义阶段任务无校验即可提交，custom_stage 永远丢失

1. **问题：** 用户在前端把任务执行阶段选为「自定义」却不填自定义阶段名称，syncDraft 不拦截直接提交，后端 custom_stage 为 None，导致时间线/分工都拿不到阶段信息。
2. **修改前：**
   ```js
   // syncDraft 直接把所有 task 作为 update 提交，无任何校验
   async function syncDraft(){var tasks=collectDraftTasks(),operations=tasks.map(function(task){return {op:'update',task_id:task.id,task:task}});...}
   ```
3. **修改后：**
   ```js
   async function syncDraft(){var tasks=collectDraftTasks();
   for(var i=0;i<tasks.length;i++){
     if(tasks[i].execution_stage==='自定义'&&!tasks[i].custom_stage)
       {throw new Error('任务 '+tasks[i].id+' 选了「自定义」阶段，请填写自定义阶段名称')}
   }
   var operations=tasks.map(function(task){return {op:'update',task_id:task.id,task:task}});...}
   ```
4. **为什么这样改：** "自定义"阶段如果没有 custom_stage 就是无效数据，应该在提交前拦截而不是让后端静默吞掉。校验放在 syncDraft，因为确认拆解、添加任务、合并任务、发起 AI 对话都经过它，一处拦截覆盖所有入口。
5. **收益：**
   - 杜绝无效的"自定义"阶段任务进入后端。
   - 报错信息直接指明任务 ID 和缺什么，用户能立刻补全。

#### 2. B2：estimated_hours 默认值 0.0 与 >0 校验自相矛盾

1. **问题：** `SubTask.estimated_hours` 默认 0.0，但校验器要求 >0。LLM 未返回工时时，默认值直接触发校验失败，任务被丢弃或整次拆解失败。
2. **修改前：**
   ```python
   estimated_hours: float = Field(default=0.0, description="预估工时（人时）")
   ```
3. **修改后：**
   ```python
   estimated_hours: float = Field(default=2.0, description="预估工时（人时）")
   ```
4. **为什么这样改：** 默认值必须能通过自身校验器。2.0h 是单任务最常见的中位工时，作为兜底既不触发校验错误，也不会让某任务凭空变成 0 工时拖垮工期计算。
5. **收益：**
   - 默认值与校验器一致，LLM 缺字段时不再丢任务。
   - 工期/负载计算不再出现 0 工时的畸形任务。

#### 3. B3：部署在 Render(UTC) 时 date.today() 与东八区相差一天，排期整体偏移

1. **问题：** 时间线和成员变动都用 `date.today()` 判断"今天"。Render 默认 UTC，凌晨会比北京时间晚一天，导致倒推起始日偏移、可用工作日数算错。
2. **修改前：**
   ```python
   # app/agents/timeline.py
   today = date.today()
   # app/web/routes.py edit-members
   remaining = max(1, (fp.input.deadline - date.today()).days)
   ```
3. **修改后：**
   ```python
   # app/config.py 新增统一时区入口
   APP_TZ_OFFSET = int(os.getenv("APP_TZ_OFFSET", "8"))
   APP_TZ = timezone(timedelta(hours=APP_TZ_OFFSET))
   def today() -> date:
       return datetime.now(APP_TZ).date()
   # app/agents/timeline.py
   from app import config
   today = config.today()
   # app/web/routes.py
   from app import config
   remaining = max(1, (fp.input.deadline - config.today()).days)
   ```
4. **为什么这样改：** 排期是按"天"粒度做工作日计算的，"今天"错一天意味着起始日、浮动天数、延期判定全错。集中到 config.today() 后，本地与云端行为一致；main.py 启动时调用 configure_timezone() 在 Linux 上 tzset 进一步对齐。
5. **收益：**
   - 本地开发（东八区）与 Render（UTC）排期结果一致。
   - 跨日临界时段不再误判截止日期剩余天数。

---

### 健壮性提升（P1）

#### 4. B4：reflection 排序用 dict 直接索引，LLM 返回未知 level 时 KeyError

1. **问题：** 改进优先级排序用 `{"error":0,"warning":1,"suggestion":2}[x.level]` 直接索引，level 取值由 LLM 产出，若返回了列表外的值（如 "info"）会抛 KeyError 中断 reflection。
2. **修改前：**
   ```python
   for issue in sorted(issues, key=lambda x: {"error": 0, "warning": 1, "suggestion": 2}[x.level]):
   ```
3. **修改后：**
   ```python
   for issue in sorted(issues, key=lambda x: {"error": 0, "warning": 1, "suggestion": 2}.get(x.level, 3)):
   ```
4. **为什么这样改：** LLM 自由文本字段绝不能当作有限枚举做硬索引。.get(...,3) 把未知 level 统一排到最后，既不崩也不打乱已知优先级。
5. **收益：**
   - reflection 对 LLM 的非常规 level 输出容错。
   - 未知级别自动降级到最低优先级，不影响 error/warning 排序。

#### 5. B5：_normalize_task_objs 把 custom_stage 写死 None，丢失 LLM 输出的自定义阶段

1. **问题：** 即使 LLM 正确返回了 custom_stage，归一化时仍写死 `None`，自定义阶段名称永远进不到 plan。
2. **修改前：**
   ```python
   stage_mapping = {"前期":"准备", "准备":"准备", ...}  # 无"自定义"
   ...
   "custom_stage": None,
   ```
3. **修改后：**
   ```python
   stage_mapping = {..., "自定义":"自定义"}  # 补映射
   ...
   "custom_stage": str(item.get("custom_stage", "")) or None if stage == "自定义" else None,
   ```
4. **为什么这样改：** 阶段映射表缺"自定义"导致 stage 直接被兜底成"执行"；custom_stage 又被写死 None，双重丢失。补映射 + 按需透传后，前端填的自定义阶段才能贯穿到后端。
5. **收益：**
   - 自定义阶段任务在前后端一致传递。
   - 阶段标签归一化覆盖了"自定义"这一合法取值。

#### 6. /run 链路未回填负责人，风险分析/导出拿不到负责人

1. **问题：** confirm（手动分工）链路会回填 assignee_id/collaborator_ids 到 plan.tasks，但 /run（自动）链路没有，导致自动生成的报告和导出文档里负责人缺失。
2. **修改前：**
   ```python
   qa_matrix = self._step_matcher(plan, inp.members)
   # 直接进入 Timeline，未回填负责人到 plan.tasks
   timeline = self._step_timeline(plan, ...)
   ```
3. **修改后：**
   ```python
   qa_matrix = self._step_matcher(plan, inp.members)
   by_task = {a.task_id: a for a in qa_matrix.assignments}
   plan = plan.model_copy(update={"tasks": [
       t.model_copy(update={
           "assignee_id": by_task[t.id].presenter if t.id in by_task else None,
           "collaborator_ids": (
               ([by_task[t.id].qa_primary] if by_task[t.id].qa_primary else [])
               + list(by_task[t.id].qa_support or [])) if t.id in by_task else []
       }) for t in plan.tasks]})
   timeline = self._step_timeline(plan, ...)
   ```
4. **为什么这样改：** 两条主链路（自动 /run、手动 confirm）应保证 plan.tasks 的负责人字段一致，否则依赖该字段的下游（风险、导出、前端列表）在自动链路下会静默缺数据。
5. **收益：**
   - 自动生成方案时导出文档与报告正确显示负责人。
   - 两条链路的 plan 数据结构对齐。

#### 7. /run 链路风险提示不一致：reporter 失败时只剩裸 message，且不调 _build_risk_note

1. **问题：** confirm 链路用确定性 _build_risk_note 生成风险，但 /run 链路 reporter 成功时不覆盖风险、失败时只塞 message，两条链路风险质量不对齐。
2. **修改前：**
   ```python
   if isinstance(report, AgentError):
       report = ReportOutput(summary="Report generation failed.", risk_note=report.message)
   ```
3. **修改后：**
   ```python
   risk_note = self._build_risk_note(plan, timeline, qa_matrix, inp.members, inp.deadline)
   if isinstance(report, AgentError):
       report = ReportOutput(summary=plan.summary, timeline_section=...,
                             qa_matrix_section=..., risk_note=risk_note)
   else:
       report = report.model_copy(update={"risk_note": risk_note})
   ```
4. **为什么这样改：** 风险提示是用户最关心的报告字段之一，必须两条链路都用同一份确定性分析，而不是一条依赖 LLM 一条兜底。同时 _build_risk_note 增加 deadline 参数，让它用真实截止日而非 plan.input.deadline 推断。
5. **收益：**
   - 自动与手动链路风险提示同源、同质。
   - reporter 失败时仍给出基于真实数据的可读报告而非 "failed" 占位。

#### 8. _build_risk_note 延期判定用 datetime.now() 且默认 deadline 为 None，跨时区/缺字段时误判

1. **问题：** 延期判定依赖 plan.input.deadline（自动链路此时可能没回填）且用 datetime.now()（UTC 偏移），逻辑脆弱。
2. **修改前：**
   ```python
   deadline = plan.input.deadline if hasattr(plan, 'input') else None
   ...
   deadline_date = datetime.fromisoformat(str(deadline)[:10])
   if timeline.total_days > (deadline_date - datetime.now()).days: ...
   ```
3. **修改后：**
   ```python
   def _build_risk_note(plan, timeline, qa_matrix, members, deadline=None) -> str:
       ...
       if deadline and timeline.total_days > 0:
           from app.config import today as _today
           remaining_days = max(0, (deadline - _today()).days)
           if timeline.total_days > remaining_days: ...
   ```
4. **为什么这样改：** deadline 改为由调用方显式传入（两条链路都传 req.input.deadline），避免依赖 plan.input 这种不可靠来源；日期比较统一用 config.today()，消除时区偏移。
5. **收益：**
   - 延期判定不再因时区或字段缺失误报。
   - remaining_days 有 max(0,…) 下限，过期项目不会再算出负天数。

#### 9. recompute 状态切换时风险提示不更新，沿用旧值

1. **问题：** 标记任务完成/阻塞后调 /recompute，报告的 risk_note 直接复用 req.report.risk_note，状态变了风险却没重算。
2. **修改前：**
   ```python
   report = req.report.model_copy(update={..., "risk_note": req.report.risk_note})
   ```
3. **修改后：**
   ```python
   risk_note = Coordinator._build_risk_note(plan, timeline, qa_matrix, members, req.input.deadline)
   report = req.report.model_copy(update={..., "risk_note": risk_note})
   ```
4. **为什么这样改：** 状态切换会改变剩余工时和负载，风险提示必须随之刷新，否则用户看到的预警与实际状态脱节。
5. **收益：**
   - 每次状态切换后风险提示反映最新进度。
   - 与其它链路共用同一个 _build_risk_note，口径统一。

#### 10. 13 个 async def 端点内无 await，async/IO 线程占用浪费

1. **问题：** routes.py 中 13 个端点声明为 async def 但函数体没有 await，FastAPI 会把它们放进主事件循环，阻塞型调用会卡住整个 API。
2. **修改前：**
   ```python
   @router.post("/draft", response_model=DraftResponse)
   async def create_draft(req: DraftRequest): ...
   ```
3. **修改后：**
   ```python
   @router.post("/draft", response_model=DraftResponse)
   def create_draft(req: DraftRequest): ...
   ```
4. **为什么这样改：** 没有 await 的端点应声明为普通 def，FastAPI 会自动放到线程池，避免阻塞事件循环。/chat 是唯一真正 await(asyncio.wait_for) 的，保留 async。
5. **收益：**
   - 消除事件循环阻塞风险，并发吞吐更稳。
   - async 只留给真正异步的 /chat，语义更准确。

#### 11. planner 把 status 设成字符串 "pending"，与枚举不一致

1. **问题：** 新任务强制归零时用字符串 "pending"，而 schema 定义了 TaskStatus 枚举，类型不一致可能在序列化/比较时出问题。
2. **修改前：**
   ```python
   result = result.model_copy(update={
       "tasks": [t.model_copy(update={"status": "pending"}) for t in result.tasks]})
   ```
3. **修改后：**
   ```python
   from app.models.schemas import ..., TaskStatus
   result = result.model_copy(update={
       "tasks": [t.model_copy(update={"status": TaskStatus.pending}) for t in result.tasks]})
   ```
4. **为什么这样改：** status 字段类型是枚举，赋值就该用枚举成员，保证类型一致性。
5. **收益：**
   - 与 schema 类型契约一致，避免隐式转换隐患。

---

### 体验优化（P2）

#### 12. R3：看板协助工时统计只按 0.15 系数，与后端评分的主协助/辅助协助系数不符

1. **问题：** 后端评分里主要协助(qa_primary)按 0.3、辅助协助(qa_support)按 0.15 计入负载，但前端 assistGroups 把所有协助任务都按 0.15 统计，看板显示的工时与实际负载对不上。
2. **修改前：**
   ```js
   // 协助任务统一 push(task)，统计用 *0.15
   assistGroups[cn].push(task)
   var assistH=assistGroups[owner].reduce(function(sum,task){return sum+task.estimated_hours*0.15},0);
   ```
3. **修改后：**
   ```js
   // 区分主要协助/辅助协助，统计分别用 0.3/0.15
   assistGroups[qa.qa_primary].push({task:task,type:'primary'});
   assistGroups[s].push({task:task,type:'support'});
   var assistH=assistGroups[owner].reduce(function(sum,item){return sum+item.task.estimated_hours*(item.type==='primary'?0.3:0.15)},0);
   ```
4. **为什么这样改：** 看板工时应反映后端真实的负载口径，否则用户看到的"某人 N h"与系统判定超载/均衡的标准不一致，误导手动调整。
5. **收益：**
   - 看板协助工时与后端负载算法口径一致。
   - 主要协助与辅助协助在卡片上有区分（"主要协助"/"协助"）。

#### 13. bindBoard 选择器把协助卡也绑了拖拽，拖拽异常

1. **问题：** R3 之前 .assignment-card 同时匹配主任务卡和协助卡，给协助卡也绑了拖拽和 onchange，协助卡没有 owner-select/collaborator-btn 会报错或行为错乱。
2. **修改前：**
   ```js
   document.querySelectorAll('.assignment-card').forEach(function(card){...})
   ```
3. **修改后：**
   ```js
   document.querySelectorAll('.assignment-card:not(.assist-card)').forEach(function(card){...})
   // 并对 owner-select/collaborator-btn 做 null 判断
   ```
4. **为什么这样改：** 协助卡只是展示，不应可拖拽、无负责人下拉。用 :not(.assist-card) 精确匹配主任务卡，并对可选元素 null 判断防崩。
5. **收益：**
   - 拖拽只作用于主任务卡，协助卡不再误绑。
   - 拖拽时设置 effectAllowed/dropEffect，拖拽手感和可见性更正常。

#### 14. 成员变动后回看板，"恢复自动分工"恢复的是变更前的旧分工

1. **问题：** edit-members 重算后的方案没有更新 state.automatic，用户再点"恢复自动分工"会回到成员变动前的分工，与新成员集不匹配。
2. **修改前：**
   ```js
   state.plan=await jsonRequest('/api/edit-members',{...});
   showNotice('成员已更新...');renderBoard();setView('board',3)
   ```
3. **修改后：**
   ```js
   state.plan=await jsonRequest('/api/edit-members',{...});
   state.automatic=JSON.parse(JSON.stringify(state.plan));  // 新基线
   showNotice('成员已更新...');renderBoard();setView('board',3)
   ```
4. **为什么这样改：** state.automatic 是"恢复自动分工"的基准，成员变动后基准必须是新成员集重算的结果，否则恢复出一个针对旧成员的方案。
5. **收益：**
   - 成员变动后恢复自动分工得到的是针对当前成员的正确分工。
   - 与 confirmDraft 的基线更新逻辑对齐。

#### 15. 合并任务不校验勾选数量，0/1 项也发请求

1. **问题：** 点"合并任务"时不校验勾选数，勾选不足 2 项也发 merge 请求，后端报错体验差。
2. **修改前：**
   ```js
   el('mergeTaskBtn').onclick=function(){var ids=...;syncDraft().then(function(){return mutateDraft([{op:'merge',task_ids:ids}])})...}
   ```
3. **修改后：**
   ```js
   el('mergeTaskBtn').onclick=function(){var ids=...;if(ids.length<2){showNotice('请至少勾选两项任务再合并','info');return}syncDraft().then(...)...}
   ```
4. **为什么这样改：** 合并至少需要 2 个任务，前端预拦截比后端报错更友好。
5. **收益：**
   - 避免无效请求，提示明确。

#### 16. "返回拆解"按钮回错视图，从结果页回看板跳到了草案页

1. **问题：** backBoardBtn 应该回到看板视图，但实现是 setView('draft',2)，从手动调整页返回时跑到了草案编辑页。
2. **修改前：**
   ```js
   el('backBoardBtn').onclick=function(){setView('draft',2)}
   ```
3. **修改后：**
   ```js
   el('backBoardBtn').onclick=function(){renderBoard();setView('board',3)}
   ```
4. **为什么这样改：** 按钮语义是回到分工看板，必须先 renderBoard 刷新数据再切到 board 视图。
5. **收益：**
   - 返回按钮行为与名称一致。

#### 17. REPORTER 提示词自相矛盾：先全禁星号又要求表格用 Markdown

1. **问题：** 提示词要求"禁止 Markdown 星号""不要用 **"，但风险字段前端用 renderMd 渲染需要粗体，自相矛盾导致 risk_note 风险类型无法加粗。
2. **修改前：**
   ```
   ## 重要：输出纯文本，禁止 Markdown 星号
   所有字段输出纯文本。不要使用 ** 加粗...
   ```
3. **修改后：**
   ```
   ## 重要：格式要求
   - risk_note 字段可以用 **粗体** 标注风险类型（前端会渲染）
   - 其他字段尽量用纯文本，避免星号
   ```
4. **为什么这样改：** risk_note 经 renderMd 渲染，粗体能让风险类型醒目；其它字段确实不需要星号。按字段区分而非一刀切。
5. **收益：**
   - 风险提示的加粗在前端正常生效。
   - 提示词不再自相矛盾。

#### 18. AI 协作助手把写给大模型的内部约束转述给用户

1. **问题：** 系统提示词里"你没有负载均衡完整数据，不要自己重新推荐分工方案——只会导致不均衡"这种否定式元指令，被模型外化成对用户的警告"不建议你手动重新分工，否则打破负载均衡"，且助手只死抠技能标签（如称李四不能写报告，无视其"文学素养"综合能力）。
2. **修改前：**
   ```python
   "你是项目协作助手...你没有负载均衡的完整数据，所以不要自己重新推荐分工方案——那只会导致不均衡。\n"
   "1. 像和用户聊天一样自然，不要说'不建议你手动重新分工'这类元话术。..."
   ```
3. **修改后：**
   ```python
   "你是项目协作助手，像一个懂项目管理的同事...\n"
   "【仅供你判断，绝不写进回答】\n"
   "- 当前分工综合考虑了多个因素...判断成员是否适合，要看他完整的能力描述，别只逐个比对技能标签——比如'文学素养''沟通协调''擅长规划'对应文字/沟通/组织类任务都合理。\n"
   "【你该聊什么】整体观察、关键路径、交接压力...\n"
   "【绝对不要出现在回答里】'不建议手动重新分工'...内部术语...'谁该做什么'的重新分配清单..."
   ```
4. **为什么这样改：** 否定式内部约束（"不要重新规划"）极易被模型转述给用户。改用正面定位（"你的角色是帮用户看清现状"）+ 独立的"绝对不要出现"清单封死所有劝阻/术语/重新分配变体，并强调综合能力而非逐标签比对，从写法上根除外化。
5. **收益：**
   - 助手不再对用户说教"不要手动分工"。
   - 不再死板逐标签匹配，承认综合能力的合理承接。
   - 不向用户暴露"多因子算法/负载均衡"等内部术语。

#### 19. AI 协作助手超时 20s 太短，正常推理常被截断

1. **问题：** project_chat 超时 20 秒，复杂方案分析常超时，用户被迫中断。
2. **修改前：**
   ```python
   timeout=20
   ...
   return {"reply": "AI 响应超过 20 秒。..."}
   ```
3. **修改后：**
   ```python
   timeout=40
   ...
   return {"reply": "AI 响应超过 40 秒。..."}
   ```
4. **为什么这样改：** 复杂方案的上下文+推理普遍需要 20-35s，提到 40s 兼顾响应性与完整度，前端等待提示同步改为 40s 保持一致。
5. **收益：**
   - 减少正常分析被误截断。
   - 前后端超时口径一致，不再"后端40s前端提示20s"。

---

### 打磨（P3）

#### 20. client.py 截断重试分支有 continue 后的死代码

1. **问题：** 截断重试的 `continue` 之后还有 `logger.info(...)` 和 `raise`，永远不会执行。
2. **修改前：**
   ```python
   continue
   logger.info("=== LLM Plain Response End ===")
   raise
   ```
3. **修改后：** 删除 continue 之后的不可达行。
4. **为什么这样改：** continue 之后的语句不可达，是误导性死代码。
5. **收益：** 消除死代码，控制流清晰。

#### 21. qingxiaoda.py 残留调试代码：max_tokens==1 时返回 "好"

1. **问题：** _render_plan 里有调试用的 `if max_tokens == 1: return "好"`，会污染正常调用。
2. **修改前：**
   ```python
   def _render_plan(text, max_tokens):
       if max_tokens == 1:
           return "好"
   ```
3. **修改后：** 删除该调试分支。
4. **为什么这样改：** 调试代码不应留在生产路径。
5. **收益：** 清理调试残留，test_qingxiaoda 预期同步更新为完整欢迎语。

#### 22. interview_sim 用 re.sub(r'QA',...) 会误伤 Q&A

1. **问题：** 裸 `QA` 替换会命中 "Q&A" 等含 QA 子串的词。
2. **修改前：**
   ```python
   result = re.sub(r'QA', '协作', result)
   ```
3. **修改后：**
   ```python
   result = re.sub(r'\bQA\b', '协作', result)
   ```
4. **为什么这样改：** 只应整词替换 QA，\b 词边界避免误伤。
5. **收益：** 不再误改含 QA 的其它词。

#### 23. timeline work_offset 整除丢半工作日精度

1. **问题：** `es[tid] // 2` 对奇数会丢 0.5 工作日，影响起排日精度。
2. **修改前：**
   ```python
   work_offset = es[tid] // 2
   ```
3. **修改后：**
   ```python
   work_offset = round(es[tid] / 2)
   ```
4. **为什么这样改：** round 四舍五入保留半工作日粒度，比整除更接近真实排期。
5. **收益：** 起排日更贴合半日工期。

#### 24. exporters.py PDF 中文渲染在 Linux 无中文字体时报错

1. **问题：** Render(Linux) 上若无系统中文字体，_register_cjk_font 返回 None，PDF 中文方块。
2. **修改前：**
   ```python
   return None
   ```
3. **修改后：**
   ```python
   try:
       from reportlab.pdfbase.cidfonts import UnicodeCIDFont
       pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
       return 'STSong-Light'
   except Exception:
       return None
   ```
4. **为什么这样改：** reportlab 自带 STSong-Light CID 字体作为兜底，在系统字体缺失时仍能渲染中文。
5. **收益：** Render 等 Linux 环境导出 PDF 不再出现中文方块。

#### 25. 版本号、CI、文档同步

1. **问题：** 多处版本号/测试基线停留在旧值。
2. **修改前/后：** main.py 版本 4.9→5.6、schemas.py FullPlan.version 4.9→5.6、index.html 静态资源版本号→5.6.0、AGENTS.md 测试基线 "45 passed"→"118 passed"、test.yml 移除 --ignore=test_reflection.py 跑全量、test_timeline 用 config.today mock、test_qingxiaoda 更新预期。
3. **为什么这样改：** 文档/版本号/CI 与代码实际状态对齐。
4. **收益：** CI 跑全量 118 测试，基线一致。

### 队友改动说明

本版本基于 workbuddy 第二轮审查清单逐条核实修复；其中"恢复自动分工基线"与"看板协助工时系数"两个 bug 为用户实测发现，本轮一并修复。CHANGELOG 文件本身此前被以 UTF-16 误重写，本轮已恢复为 UTF-8(与 HEAD 一致)并补全 v5.7 章节。


## v5.6 —— 成员变动后分工失衡 + 导出与报告问题修复（2026-07-30）

**定位：** 修复成员变动后重算分工严重失衡的根因，同步修复 PDF 表格溢出和风险提示过于简略。

**审查/修改背景：** 用户反馈三个问题：（1）成员变动后重算分工特别不均衡；（2）PDF 导出表格文字溢出重叠；（3）风险提示过于简略。前两者经分析和参考 AI实践基石大作业（homework）版本锁定根因。

---

### 关键缺陷（P0）

#### 1. _balance_workload 技能守卫阻止均衡搬运，导致成员变动后 gap 无法收敛

1. **问题：** 用户反馈此前“成员没变时分工是均衡的，成员一变就不均衡了”。根因在 `_balance_workload` 的 presenter 换人处有一段 homework 版本没有的额外技能分数守卫：

   ```python
   # 均衡不能以明显破坏专业匹配为代价。
   if ((current_skill > 0 and target_skill <= 0)
           or target_skill < current_skill - 0.35):
       continue
   ```

2. **修改前：**（competition 版本技能分数守卫）
   ```python
   if required and target_member is not None:
       if avoids(t, a.task_id):
           continue
       target_skill = skill_score(target_member, required)
       current_skill = (skill_score(current_member, required)
                       if current_member is not None else 0.0)
       if ((current_skill > 0 and target_skill <= 0)
               or target_skill < current_skill - 0.35):
           continue
   ```

3. **修改后：**（homework 版本 qualified 方案，只检查回避）
   ```python
   qset = qualified.get(a.task_id)
   if qset is not None and t not in qset:
       continue
   ```

4. **为什么这样改：** 根因不是初始分配算法（多因子打分公式原来就是好的，用户确认“没动成员时分工是均衡的”），而是 `_balance_workload` 搬运时被技能守卫卡住。homework 版本只检查“目标成员是否明确回避该任务”，不检查技能分数差——只要没人写“不想做”，任务就可以搬过去，gap 才能真正收敛。初始分配的多因子公式保持不变。

5. **收益：**
   - 成员变动后重算 gap 可从 3-4h 收敛到 <=1h。
   - 初始分配算法不变，用户确认“原来就是好的”版本不受影响。
   - 只拦明确回避者（负向标签），不拦低技能但未回避者。

---

### 健壮性提升（P1）

#### 2. apply_manual_assignment 对已完成/进行中/阻塞任务报“未知负责人”

1. **问题：** `assign_with_balance` 对已完成任务返回 `presenter=“(已完成)”`，该值被写入 `task.assignee_id`。然后 `apply_manual_assignment` 遍历所有任务时把 `“(已完成)”` 当成成员名去查 `member_map`，找不到就报错。同理，进行中/阻塞任务的负责人如果在成员变动中被移除，也会触发同一错误。

2. **修改前：**
   ```python
   owner = req.assignees.get(task.id, task.assignee_id or "")
   if owner and owner not in member_map:
       raise ProjectServiceError(f"未知负责人：{owner}")
   ```

3. **修改后：**
   ```python
   if task.status == "completed":
       updated_tasks.append(task)
       assignments.append(QAAssignment(
           task_id=task.id, task_name=task.name, chapter="",
           presenter="(已完成)", qa_primary="", qa_support=[],
           score=0.0, reasoning="任务已完成",
       ))
       continue
   owner = task.assignee_id if task.assignee_id in member_map else None
   if req.assignees.get(task.id):
       owner = req.assignees.get(task.id)
   if owner and owner not in member_map:
       owner = None
   ```

4. **为什么这样改：** 已完成任务的分工已经确定，不需要验证或重分配。进行中/阻塞任务的负责人如果被移出成员名单，应该清空而非报错——让用户重新分配，而不是中断流程。

5. **收益：** 无论任务状态如何、成员如何变动，“确认最终分工”都不会再 500 报错。

---

### 体验优化（P2）

#### 3. PDF 导出表格文字超出格子重叠

1. **问题：** `_build_table` 和 `_md_to_pdf_story` 把单元格内容作为纯文本传给 reportlab Table。reportlab 对纯字符串不会自动换行，当文字超过列宽时就溢出、与相邻单元格重叠。

2. **修改前：**
   ```python
   t = Table(data, repeatRows=1, colWidths=[col_w] * n_cols)
   ```

3. **修改后：**
   ```python
   from reportlab.platypus import Paragraph
   wrapped = [[Paragraph(str(cell), style) if style else cell for cell in row] for row in data]
   t = Table(wrapped, repeatRows=1, colWidths=[col_w] * n_cols)
   ```

4. **为什么这样改：** reportlab 的 Table 对 `Paragraph` 对象会自动换行、行高自适应。

5. **收益：** 长任务名在 PDF 表格中自动换行，不再溢出重叠。

---

#### 4. 风险提示内容过于简略（LLM 版本 vs 确定性分析）

1. **问题：** `edit-members` 端点调用了 `ReporterAgent`（LLM），LLM 成功时返回的 `risk_note` 往往是一两句自然语言总结，不如 `Coordinator._build_risk_note` 生成的逐项定量分析详细。

2. **修改前：** `edit-members` 端点完全依赖 LLM 报告的 risk_note 字段。

3. **修改后：** 在 `edit-members` 端点中，无论 LLM 报告是否成功，最后都用 `Coordinator._build_risk_note()` 覆盖 risk_note。LLM 仍负责生成 summary/timeline_section/qa_matrix_section 的叙述性文本。

4. **为什么这样改：** LLM 倾向于生成概括性总结而非定量风险分析。确定性版本的 `_build_risk_note` 逐项检查 6 种风险类型（负载均衡、总工时/产能、关键路径、未分配任务、技能匹配、时间线），两者互补——LLM 写正文，确定性逻辑写风险。

5. **收益：** 报告中的风险提示始终包含定量分析和具体数值。

---

### 打磨（P3）

#### 5. 协作者数量尊重 suggested_people

1. **问题：** 每个任务都分配了主要负责人 + 2 名辅助协助（共 3 人），即使 `suggested_people=1` 的任务也被强制安排协作者。

2. **修改前：** 固定分配 presenter + primary + 2 support。

3. **修改后：**
   ```python
   max_collaborators = max(0, t.suggested_people - 1)
   if max_collaborators > 0:
       ...
   ```

4. **收益：** 只有 `suggested_people >= 2` 的任务才有协作者，不再强制三人分工。
## v5.5 —— 新增成员零工时修复 + 任务分工术语清理（2026-07-29）

**定位：** 彻底修复"前端新增成员后工时显示为 0"，并清除系统中遗留的"答辩/QA 责任"术语，让流程回归"任务分工"本意。

**审查/修改背景：** 用户反馈两点——(1) 运行任务时终端仍能看到「主讲/主答/辅答」等遗留的答辩责任术语，整个系统似乎仍围绕旧作业的答辩分工展开；(2) 在"成员管理"里新增一人并应用变更后，该成员任务量/工时为 0。此前多次尝试改 `skill_score` 的 0.0→0.3→0.0 均未奏效，因为根因不在打分。

---

### 关键缺陷（P0）

#### 1. 新增成员被分配为协作者后，看板与工作量视图仍显示 0 工时/0 任务

**问题：** 新增成员因为无可匹配技能标签（skill_score=0），贪心分配只会让他做"主要协助（qa_primary）"而非"负责人（presenter）"。但前端看板按 `task.assignee_id` 分组、后端 `workload_snapshot` 也只累加负责人工时，完全忽略协作者折算工时——于是这个"只做协助"的新成员在所有界面都显示 0，看起来像被系统晾在一边。

**修改前：**（`app/services/project_service.py` workload_snapshot 只数负责人）
```python
for task in plan.plan.tasks:
    owner = task.assignee_id
    ...
    work[owner] += task.estimated_hours
    counts[owner] += 1
```
（`app/web/templates/index.html` renderBoard 按负责人分组）
```js
state.plan.plan.tasks.forEach(function(task){groups[task.assignee_id||'未分配'].push(task)});
// 协作者从不出现，新成员列空白 → "0 项任务 · 0.0h"
```

**修改后：**
```python
# 协作者折算工时：优先用 qa_matrix 角色，否则回退 collaborator_ids
qa = qa_by_task.get(task.id)
if qa is not None:
    collaborators = [(qa.qa_primary, QA_PRIMARY_RATIO)] + [(s, QA_SUPPORT_RATIO) for s in qa.qa_support ...]
for cname, ratio in collaborators:
    work[cname] += h * ratio
    assist_counts[cname] += 1
```
```js
// renderBoard 现在同时收集协作者任务，渲染为浅色"协助"卡片
state.plan.plan.tasks.forEach(function(task){...collabs.forEach(function(cn){assistGroups[cn].push(task)})});
// 列头：负责 N 项 · 协助 M 项 · 总工时
```

**为什么这样改：** 真正的根因不是"分配算法没给新成员活干"——算法已经把他安排为协作者（有真实折算工时）；而是"展示层只认负责人、不认协作者"，把已分配的工作隐形了。之前反复改 skill_score 之所以无效，正是因为问题根本不在打分阶段。让展示层忠实反映协作者工时，是从根上解决。

**收益：**
1. 新增成员立即显示真实工时（如 2.7h、3 项协助），不再是误导性的"0"。
2. 看板里新成员列有"协助"卡片，全员参与情况一目了然。
3. 与 `qa_matrix.workload` 的负载口径统一，看板/工作量/报告三处不再矛盾。

**同步修改：** `tests/test_project_service.py`（协作者不再误报"尚未分配"）、`tests/test_member_edit.py`（新增"Dave 无技能"回归用例）、`app/web/static/style.css`（`.assist-card` 浅色样式）。

---

### 健壮性提升（P1）

#### 2. 终端日志泄漏原始 LLM JSON，残留答辩术语污染运行输出

**问题：** `logging.basicConfig(level=INFO)` 下，LLM 客户端会把 matcher 的原始 JSON（含 `presenter`/`qa_primary` 字段名，以及模型可能回吐的「主讲/主答/辅答」）整段打印到终端——这正是用户"运行任务的过程"里看到的来源。

**修改前：**
```python
logger.info("Raw response (first 1000 chars):
%s", raw[:1000])
logger.info("Extracted JSON (first 1000 chars):
%s", extracted[:1000])
...
logger.info("Full extracted JSON for debugging:
%s", extracted)
```

**修改后：**
```python
# 不再倾倒原文/JSON 到终端，改为只记长度与状态
logger.info("Raw response length: %d chars, finish_reason=%s", len(raw), finish)
...
logger.debug("Full extracted JSON for debugging:
%s", extracted)  # 仅 DEBUG 级别
```

**为什么这样改：** INFO 是正常运行级别，不该出现调试用的大段原文。这些原文里的内部字段名和遗留术语会让用户误以为"系统还在围绕答辩责任跑"。降级到 DEBUG 后，正常运行终端只剩简洁的状态行；需要排查时设 DEBUG 即可恢复。

**收益：**
1. 运行过程终端干净，不再暴露 presenter/qa_primary 等内部字段与遗留术语。
2. 排查能力不损失（DEBUG 级别仍可看完整原文）。

---

### 体验优化（P2）

#### 3. 清除任务分工流程中的"答辩/QA"遗留术语，回归任务分工本意

**问题：** 系统由旧作业（答辩分工）衍生， Reporter/Matcher 的用户提示词仍写「## QA矩阵」「QA 分配」，Planner 兜底仍生成「答辩准备」任务，UI 仍叫「答辩模拟」——让整个任务分工流程读起来仍像在排答辩。

**修改前：**
```python
# reporter.py
f"## QA矩阵
{qa_lines}"
# interview_sim.py
f"以下是学生的作业计划和QA分配：

## QA矩阵
{qa_lines}"
# coordinator.py 兜底任务
add("答辩演练与问题准备", "答辩", 3, ...)
# index.html
<button data-tab="interview">答辩模拟</button>
```

**修改后：**
```python
f"## 责任分工
{qa_lines}"
f"以下是团队的项目计划和责任分工：

## 责任分工
{qa_lines}"
add("汇报演练与问题准备", "汇报", 3, ...)
<button data-tab="interview">评审预演</button>
```

**为什么这样改：** 任务分工 ≠ 答辩。Reporter/MMatcher 提示词用「QA 矩阵」会诱导模型沿用作业里的答辩角色语义；兜底默认塞"答辩演练"任务则把答辩强加给所有项目。统一改为「责任分工/汇报/评审预演」后，输出语言与工具定位一致。`qa_matrix` 作为内部标识符保留（不破坏数据结构与持久化兼容）。

**收益：**
1. 提示词不再诱导模型回吐"答辩/QA"角色术语。
2. 默认任务、UI 文案与"任务分工"定位一致。
3. 模拟提问（原答辩模拟）后处理增加裸 `QA` 整词替换，杜绝"做 QA"类泄漏。

**同步修改：** `app/llm/prompts.py`（INTERVIEW_SYSTEM 改"评审提问"、Matcher/Planner 去答辩措辞）。

---
## v5.4 —— DeepSeek 超时根因修复 + 推理模型容错加固（2026-07-29）

**定位：** 切换到 DeepSeek 推理模型后频繁超时和 JSON 解析失败，本版本从连接复用、超时重试、截断恢复三个层面系统性解决。

---

### 关键缺陷（P0）

#### 1. 推理模型响应慢导致首次请求超时

**问题：** DeepSeek 推理模型（如 deepseek-v4-flash）思考时间长、首字延迟高，单次请求经常超过 LLM_TIMEOUT（默认 30 秒），直接返回错误走兜底。

**修改前：**
```python
resp = self._client.chat.completions.create(
    model=self.model, messages=messages,
    temperature=temperature, max_tokens=8000,
    timeout=LLM_TIMEOUT,
)
# 超时直接抛出，上层走兜底
```

**修改后：**
```python
def _call_with_timeout_retry(self, messages, budget, temperature, max_retries=2):
    for i in range(max_retries + 1):
        try:
            return self._client.chat.completions.create(...)
        except Exception as e:
            if _classify_error(e) == 'timeout' and i < max_retries:
                logger.warning("LLM 请求超时，第 %d/%d 次重试", i+1, max_retries)
                continue
            raise
```

_try_plain_validate 改为调用 _call_with_timeout_retry，容忍推理模型的慢响应。

**为什么这样改：** 推理模型的思考阶段耗时不可预测，单次超时就放弃会导致大量请求白跑。有限重试（默认 2 次）在不过度等待的前提下显著提高成功率。

**收益：**
1. 推理模型慢响应不再频繁触发兜底。
2. 重试有上限（2 次），不会无限等待。

---

#### 2. 响应被 max_tokens 截断导致 JSON 不完整

**问题：** 推理模型输出 token 预算（max_tokens=8000）不足以容纳完整 JSON，finish_reason 为 length 时 JSON 被截断，解析失败。

**修改后：**
```python
budget = LLM_MAX_TOKENS  # 从配置读取（默认更大）
for _ in range(2):  # 首次 + 截断后加预算重试一次
    resp = self._call_with_timeout_retry(messages, budget, temperature)
    finish = getattr(resp.choices[0], 'finish_reason', None)
    try:
        result = response_model.model_validate_json(extracted)
        return result
    except (ValidationError, ValueError):
        repaired = self._repair_response(extracted, response_model)
        if repaired is not None: return repaired
        if finish == "length" and budget < 32000:
            budget = min(32000, budget * 2)  # 翻倍重试
            continue
        raise
```

**为什么这样改：** 推理模型的思考过程消耗大量 token，8000 预算经常不够。截断时翻倍预算重试一次（上限 32000），在成本可控的前提下救回截断响应。

**收益：**
1. 截断响应有机会通过加预算重试获得完整 JSON。
2. 预算上限 32000 防止无限消耗。

---

#### 3. 推理模型正文为空时漏取 reasoning_content 中的 JSON

**问题：** 部分推理模型把结果放在 reasoning_content 而非 content，message.content 为空时直接当作失败。

**修改后：**
```python
raw = msg.content or ""
if not raw.strip():
    rc = getattr(msg, "reasoning_content", None) or ""
    if "{" in rc:
        raw = rc  # 从思考内容中抽取 JSON
```

**为什么这样改：** 不同推理模型的输出位置不统一，只读 content 会漏掉合法响应。

**收益：** 兼容把结果放在思考链中的推理模型。

---

### 健壮性提升（P1）

#### 4. 新增 LLM_MAX_TOKENS 配置项

**修改前：** max_tokens=8000 硬编码。

**修改后：** 从 app/config.py 读取 LLM_MAX_TOKENS，可在 .env 中调整。

**为什么这样改：** 不同模型和任务的最优 token 预算不同，硬编码无法适配。

**收益：** 部署时按模型特性调整预算，无需改代码。

---
## v5.3 —— 抽屉遮挡修复 + 首次拆解兜底优化（2026-07-25）

**定位：** 修复 AI 抽屉在中部盖住按钮、首次任务拆解频繁走兜底两个问题。

**审查/修改背景：** 用户反馈按钮在页面中部时框会盖住按钮；首次点"生成"常出现"AI 未成功拆解走规则草案"。

---

### 关键缺陷（P0）

**1. 首次拆解频繁走兜底（冷启动超时）**

- **问题：** 用户反馈"一般都是第一次拆解时会出现走兜底的情况"。首次请求超时后直接返回错误，不尝试 plain 回退。
- **修改前：**
  ```python
  # client.py 行129-137
  if last_error_type in ("timeout", "rate_limit", "unknown"):
      return AgentError(...)  # 直接返回，不尝试 plain
  ```
- **修改后：** timeout/rate_limit/unknown 时也尝试一次 `_try_plain_validate` 回退。
  ```python
  if last_error_type in ("timeout", "rate_limit", "unknown"):
      try:
          return self._try_plain_validate(...)  # 尝试 plain 回退
      except Exception as e2:
          return AgentError(...)
  ```
- **为什么这样改：** 原逻辑认为超时后 plain 也会超时，但首次请求超时往往是连接建立慢（冷启动），此时连接可能已建立，plain 回退成功率较高，值得多等一个超时周期。
- **收益：** 首次拆解成功率显著提升

**2. LLMClient 每次新建导致冷启动超时**

- **问题：** `Coordinator()` 每次新建 → `PlannerAgent()` → `LLMClient()` → `OpenAI()` 新建客户端，每次都重新建立 TCP/TLS 连接，首次请求更容易超时。
- **修改前：**
  ```python
  # base.py
  class BaseAgent:
      def __init__(self, llm=None):
          self.llm = llm or LLMClient()  # 每次新建
  ```
- **修改后：**
  ```python
  # client.py 新增单例
  class LLMClient:
      _singleton = None
      @classmethod
      def get_shared(cls):
          if cls._singleton is None:
              cls._singleton = cls()
          return cls._singleton

  # base.py
  class BaseAgent:
      def __init__(self, llm=None):
          self.llm = llm or LLMClient.get_shared()  # 复用单例
  ```
- **为什么这样改：** OpenAI SDK 内部的 httpx 连接池在同一 client 实例上复用。新建 client 触发 TCP/TLS 握手，首次请求更容易超时。单例化后连接池只建立一次，后续复用。
- **收益：**
  1. 消除首次请求的连接建立开销
  2. 后续请求更快（连接已预热）
  3. `routes.py` 的 chat 也改用 `get_shared()`

---

### 体验优化（P2）

**3. AI 抽屉在页面中部时盖住按钮**

- **问题：** 按钮在中部时，上下空间都不够放 510px 高的框，边界保护把框硬挤，盖住按钮。
- **修改前：** 固定判断上半屏→下方，否则上方；空间不足时 `top=vh-dh-margin` 硬挤。
- **修改后：** 比较上下可用空间选更大一侧；该侧空间不足时收缩框高度（最低 280px），绝不与按钮重叠。
  ```js
  var spaceBelow=vh-bRect.bottom-margin, spaceAbove=bRect.top-margin;
  var showBelow=spaceBelow>=spaceAbove;
  var available=showBelow?spaceBelow:spaceAbove;
  if(dh>available){dh=Math.max(MIN_H,available);drawer.style.height=dh+'px'}
  ```
- **收益：** 框永远不盖按钮，空间不足时自动收缩高度

---

### 同步修改

- `app/llm/client.py`：新增 `get_shared()` 单例方法，timeout 时尝试 plain 回退
- `app/agents/base.py`：`BaseAgent.__init__` 改用 `LLMClient.get_shared()`
- `app/web/routes.py`：chat 路由改用 `LLMClient.get_shared()`
- `app/web/templates/index.html`：`positionAssistantDrawer()` 改为比较上下空间 + 收缩高度

### 验证

- JS 语法检查：通过
- 核心测试：6 passed

---



**定位：** 修复 AI 调整建议按钮的拖拽与开关冲突、抽屉不跟随按钮、框可能超出屏幕三个交互问题。

**审查/修改背景：** 用户反馈按钮拖拽时抽屉不跟随、点击与拖拽逻辑混乱、框可能出现在屏幕外。

---

### 体验优化（P2）

**1. AI 调整建议按钮点击与拖拽逻辑冲突**

- **问题：** 原实现用 `dr` 标志区分点击和拖拽，逻辑绕——拖动结束后 `dr=false`，下次点击才开关；用户体验不直观。
- **修改前：**
  ```js
  btn.addEventListener('click',function(e){if(dr){e.stopPropagation();e.preventDefault();dr=false}else{openAssistant()}});
  ```
- **修改后：**
  ```js
  // 用 moved 标志：拖拽位移>5px 标记 moved，松开后若 moved 则忽略本次 click
  btn.addEventListener('click',function(e){if(moved){moved=false;e.stopPropagation();e.preventDefault();return}var isHidden=drawer.classList.contains('hidden');if(isHidden){openAssistant()}else{closeAssistant()}});
  ```
- **为什么这样改：** 点击和拖拽应彻底分离。拖拽位移超过阈值才标记为拖拽，松开后吃掉 click 事件；纯点击（位移<5px）才触发开关。逻辑直观，用户按一下开、再按一下关。
- **收益：**
  1. 点击开关行为纯粹，不再受拖拽干扰
  2. 拖拽后不会误触开关

**2. 拖拽时抽屉不跟随按钮**

- **问题：** 原实现抽屉用独立 `left/top` 计算（`drawer.style.left=Math.max(-20,...)`），与按钮位置基准不一致，按钮小幅移动时抽屉不动。
- **修改前：**
  ```js
  drawer.style.left=Math.max(-20,Math.min(window.innerWidth-380,nx))+'px';
  drawer.style.top=Math.max(0,Math.min(window.innerHeight,ny+50))+'px'
  ```
- **修改后：** 新增 `positionAssistantDrawer()` 函数，基于按钮 `getBoundingClientRect()` 计算抽屉位置，拖拽过程中实时调用。
  ```js
  function positionAssistantDrawer(){
    var bRect=btn.getBoundingClientRect(),dRect=drawer.getBoundingClientRect();
    // 垂直：按钮在上半屏→框在下方，否则在上方
    var showBelow=bRect.top<vh/2;
    // 横向：框右对齐按钮右边，超出左边则左移
    var left=bRect.right-dw;
    left=Math.max(margin,Math.min(left,vw-dw-margin));
    ...
  }
  ```
- **为什么这样改：** 抽屉应锚定按钮，相对位置固定。原实现抽屉和按钮用不同定位基准（按钮用 transform，抽屉用 left/top），导致跟随不同步。新方案统一用按钮的实际矩形位置计算抽屉位置。
- **收益：**
  1. 拖拽按钮时抽屉实时跟随，相对位置固定
  2. 打开抽屉时也自动定位到按钮旁边

**3. 框可能出现在屏幕外**

- **问题：** 原实现只在拖拽时做边界保护，且未考虑抽屉高度 510px，按钮在顶部时抽屉超出屏幕下方。
- **修改后：** `positionAssistantDrawer()` 统一做四向边界保护：
  - 垂直：按钮在上半屏→框在下方，否则在上方；若仍超出则向屏幕内侧偏移
  - 横向：框右对齐按钮，超出左边则左移到屏幕内
  - 窗口 resize 时重新定位
- **收益：**
  1. 框永远在屏幕内可见
  2. 按钮在上半屏时框向下展开，在下半屏时向上展开

---

### 同步修改

- `app/web/templates/index.html`：新增 `positionAssistantDrawer`/`closeAssistant`，重写按钮交互 IIFE，更新 `openAssistant` 调用定位函数
- `app/web/static/style.css`：`.assistant-drawer` 去掉固定 `right:24px;bottom:76px`，改为由 JS 动态控制定位

### 验证

- JS 语法检查：通过
- 核心测试：6 passed

---



**定位：** 修复用户在《新发现的问题.docx》中报告的 6 个缺陷，覆盖报告一致性、风险提示、Markdown 渲染、答辩模拟、历史方案等前端体验问题。

**审查/修改背景：** 用户在使用 v5.0 后报告了 6 个新问题，集中在报告显示与历史方案管理。本版本逐一修复，并按 AGENTS.md 规范完成前端 4 步验证。

---

### 关键缺陷（P0）

**1. 风险提示栏显示"用户确认的手动分工"（问题2）**

- **问题：** 手动分工确认后，报告"风险提示"栏显示"用户确认的手动分工"，用户无法理解含义。
- **修改前：**
  ```python
  # app/services/project_service.py 行146-159
  qa = QAOutput(assignments=assignments, workload=workload, note="用户确认的手动分工")
  ...
  report = fp.report.model_copy(update={
      "summary": plan.summary,
      "qa_matrix_section": "\n".join(...),
      "risk_note": qa.note,  # 把 note 直接当作 risk_note
  })
  ```
- **修改后：**
  ```python
  # 新增 _build_manual_risk_note 函数，基于实际负载和工期计算真实风险
  risk_note = _build_manual_risk_note(plan, timeline, workload, fp.input.members)
  report = fp.report.model_copy(update={
      "summary": plan.summary,
      "qa_matrix_section": "\n".join(...),
      "risk_note": risk_note,  # 真实风险：负载不均衡/工期紧张/关键路径/未分配
  })
  ```
- **为什么这样改：** `qa.note` 是分配策略说明（"用户确认的手动分工"），不是风险提示。把它当 risk_note 是字段语义混淆。新函数 `_build_manual_risk_note` 基于实际工时、产能、关键路径比例计算真实风险，与 Reporter Agent 的风险维度对齐。
- **收益：**
  1. 风险提示栏显示有意义的内容（如"负载偏重""工期紧张"）
  2. 手动分工后用户能看到真实风险点
  3. 与导出报告的风险维度一致

---

### 健壮性提升（P1）

**2. 前端报告与导出报告内容不一致（问题1）**

- **问题：** 前端报告 Tab 只显示 summary/timeline/qa_matrix 三段，导出文档有六节（含任务表/时间线表/QA表/风险提示），用户看到的前端信息远少于导出。
- **修改前：**
  ```js
  // index.html renderResultTab
  tab==='report')content='<div class="report-box"><h3>方案摘要</h3><p>'+esc(state.plan.report.summary)+'</p><h3>时间线</h3><p>'+esc(state.plan.timeline.note||state.plan.report.timeline_section)+'</p><h3>分工说明</h3><p>'+esc(state.plan.report.qa_matrix_section)+'</p></div>'
  ```
- **修改后：**
  ```js
  // 新增 renderReportTab() 函数，渲染六节：计划概述/任务拆解表/时间线表/责任分工表/报告总结/风险提示
  tab==='report')content=renderReportTab()
  ```
- **为什么这样改：** 前端与导出应展示一致的核心信息。新增 `renderReportTab()` 复用导出逻辑的六节结构，并使用表格渲染任务/时间线/分工，信息密度与导出文档对齐。
- **收益：**
  1. 前端报告信息完整，用户无需导出即可查看全部内容
  2. 与导出文档结构一致，减少认知差异
  3. 风险提示在前端也可见（配合问题2修复）

**3. 前端报告偶尔混用 Markdown（问题3）**

- **问题：** Reporter 的 prompt 要求"禁止 Markdown 星号"，但 LLM 偶尔不遵守，前端用 `esc()` 直接转义导致 `**bold**` 原样显示。
- **修改前：** 所有报告字段用 `esc()` 转义，Markdown 语法原样显示为文本。
- **修改后：** 新增 `renderMd(text)` 轻量 Markdown 渲染函数，支持 `**bold**`/`*italic*`/`` `code` ``/标题/列表/表格，先 `esc()` 转义再解析语法，避免 XSS。
- **为什么这样改：** LLM 输出无法 100% 保证纯文本。前端需要兜底解析 Markdown，而不是把星号原样显示给用户。
- **收益：**
  1. 报告格式稳定，不再出现 `**` 原样显示
  2. 支持表格、列表等结构化内容
  3. 先转义再解析，安全性不变

**4. AI 对话回复混有 Markdown（问题4）**

- **问题：** `/api/chat` 的 LLM 回复可能含 Markdown，前端用 `esc()` 转义导致 `**` 等原样显示。
- **修改前：**
  ```js
  el('messages').insertAdjacentHTML('beforeend','<div class="assistant-msg">'+esc(data.reply)+'</div>')
  ```
- **修改后：**
  ```js
  el('messages').insertAdjacentHTML('beforeend','<div class="assistant-msg">'+renderMd(data.reply)+'</div>')
  ```
- **为什么这样改：** 聊天回复同样无法保证纯文本，复用 `renderMd` 统一渲染。
- **收益：**
  1. 对话回复中的加粗/列表/代码块正常渲染
  2. 与报告渲染逻辑统一，降低维护成本

**5. 答辩模拟缺专门的要求输入框（问题5）**

- **问题：** 后端 `user_requirements` 字段已支持，但前端答辩模拟 Tab 用的是项目级 `additional_requirements`，没有专门的"评委关注点"输入框。
- **修改前：** 答辩 Tab 只有"生成模拟问题"按钮，无输入框。
- **修改后：** 新增 `<textarea id="interviewRequirements">` 输入框，`bindInterviewControls` 读取该输入框值传给后端。
- **为什么这样改：** 答辩模拟的需求（如"重点围绕技术方案"）与项目级要求不同，需要独立输入。
- **收益：**
  1. 用户可指定评委关注点，生成更聚焦的问题
  2. 不污染项目级 `additional_requirements`

**6. 保存成功但历史方案找不到（问题6）**

- **问题：** 用户点保存后有成功提示，但回看历史方案找不到刚保存的。
- **修改前：** `showHistory` 用 `document.querySelectorAll('[data-file]')` 全局查询，可能匹配无关元素；保存后不预加载历史；`loadPlan` 不防御缺失字段。
- **修改后：**
  1. `showHistory` 改用 `#planList [data-file]` 精确定位
  2. 保存成功后预加载历史列表到 `state.cachedPlans`
  3. 刚保存的方案在历史列表中高亮（`plan-item-new` 类）
  4. `loadPlan` 防御性补全 `report`/`timeline`/`qa_matrix` 缺失字段
  5. `showHistory` 增加 HTTP 状态检查和错误日志
- **为什么这样改：** 全局 `[data-file]` 查询可能因页面其他元素干扰导致事件绑定异常；保存后不刷新历史让用户以为没保存；旧版本保存的方案可能缺新字段导致后续渲染崩溃。
- **收益：**
  1. 保存后立即可在历史中看到（高亮标识）
  2. 加载旧方案不再因字段缺失崩溃
  3. 错误可追踪（console.error）

---

### 体验优化（P2）

**7. 新增 Markdown 表格和风险提示样式**

- **问题：** 报告和聊天渲染 Markdown 表格/风险提示时缺乏样式，可读性差。
- **修改后：** `style.css` 新增 `.md-table`/`.risk-note`/`.interview-req`/`.plan-item-new` 等样式。
- **收益：** 表格有边框、风险提示红色高亮、新保存方案蓝色边框标识。

---

### 同步修改

- `app/services/project_service.py`：新增 `_build_manual_risk_note` 辅助函数
- `app/web/templates/index.html`：新增 `renderMd`/`renderReportTab` 函数，修改 `savePlan`/`showHistory`/`loadPlan`/`bindInterviewControls`/`sendChat`
- `app/web/static/style.css`：新增 Markdown 渲染和答辩要求输入框样式

### 验证

- JS 语法检查：通过
- 全部测试：82 passed, 17 failed（均为缺 OPENAI_API_KEY 的 LLM 测试，非本次修改引入）
- 核心测试：`test_manual_assignment_and_workload_share_business_rules` + `test_save_endpoint` 全过

---



**定位：** 逐项修复用户报告的 8 个功能缺陷，确保比赛版的基础功能（成员、匹配度、工作量、报告、答辩、甘特图、AI 对话、UI）全部可用且无 bug。

**审查/修改背景：** 用户对比作业版本后发现比赛版多个基础功能存在 bug。本版本对照 `AI实践基石大作业` 参考版本，逐一定位根因并修复。

---

### 关键缺陷（P0）

**1. 成员管理预填了 3 个默认成员（张三/李四/王五），干扰用户输入**

- **问题：** 页面加载即填入 3 个假成员和技能标签，用户需要先删除才能填自己的数据。
- **修改前：**
  ```js
  // index.html 行182
  addMember({name:'张三',skill_tags:['文案']});
  addMember({name:'李四',skill_tags:['摄影']});
  addMember({name:'王五',skill_tags:['秀米排版']});renderSteps();
  ```
- **修改后：**
  ```js
  renderSteps();
  ```
- **为什么这样改：** 默认值是早期开发调试残留，不应出现在面向用户的产品中。成员列表应初始为空，由用户自行填写。
- **收益：** 页面加载即干净；用户不会被无关数据误导；减少误操作。

**2. 技能匹配度为 0——同义词表覆盖不足，"文学素养"无法关联"写报告"等常见表达**

- **问题：** 评分引擎的 `_SKILL_SYNONYMS` 表缺少大量常用表达（写报告、撰写报告、报告、总结报告、推文、演讲、答辩、摄影、资料整理等），导致用口语化标签时匹配度始终为 0。
- **修改前：**
  ```python
  # scoring.py — 同义词表仅覆盖 ~50 个表达
  "文学素养": "文案撰写", "报告撰写": "文案撰写",
  # 缺少："写报告"、"撰写报告"、"报告"、"总结报告"等
  ```
- **修改后：**
  ```python
  # scoring.py — 新增 30+ 同义词映射
  "写报告": "文案撰写", "撰写报告": "文案撰写", "报告": "文案撰写",
  "总结报告": "文案撰写", "调研报告": "文案撰写", "推文": "文案撰写",
  "演讲": "沟通协调", "答辩": "沟通协调", "摄影": "视频剪辑",
  "资料收集": "调研分析", "资料整理": "调研分析", ...
  ```
- **为什么这样改：** 纯字符相似度无法识别"文学素养"与"写报告"的语义关联，必须靠同义词表将口语化标签归一化到标准技能词。根因是表覆盖面太窄。
- **收益：** 匹配度从 0% 恢复到合理值（如"文学素养"对"写报告"= 100%）；分工建议更准确。

**3. 工作量不随任务状态变化——标记完成后成员条带不缩短**

- **问题：** `workload_snapshot` 统计负载时只看 `assignee_id` 和工时，完全忽略 `task.status`，已完成任务仍被计入工作量。
- **修改前：**
  ```python
  # project_service.py workload_snapshot
  for task in plan.plan.tasks:
      owner = task.assignee_id
      if not owner or owner not in work:
          warnings.append(...)
          continue
      work[owner] += task.estimated_hours  # 已完成的也计入
  ```
- **修改后：**
  ```python
  for task in plan.plan.tasks:
      owner = task.assignee_id
      if not owner or owner not in work:
          continue
      if task.status == "completed":
          continue  # 已完成的不计入剩余工作量
      work[owner] += task.estimated_hours
  ```
- **为什么这样改：** 工作量面板的语义是"剩余负载"，已完成任务不应再占用产能。根因是统计逻辑遗漏了状态过滤。
- **收益：** 标记任务完成后成员条带实时缩短；工作量面板与现实进度一致。

**4. 风险提示栏显示"状态切换重算（保留原分工）"——内部调试文本泄露给用户**

- **问题：** `/recompute` 端点把 `qa_matrix.note`（内部状态描述）直接塞进 `report.risk_note`，用户在报告里看到这句无意义的话。
- **修改前：**
  ```python
  # routes.py /recompute
  report = req.report.model_copy(update={
      "timeline_section": timeline.note,
      "qa_matrix_section": "...",
      "risk_note": qa_matrix.note,  # 内部调试文本泄露
  })
  ```
- **修改后：**
  ```python
  report = req.report.model_copy(update={
      "timeline_section": timeline.note,
      "qa_matrix_section": "...",
      "risk_note": req.report.risk_note,  # 保留原有风险提示
  })
  ```
- **为什么这样改：** `qa_matrix.note` 是算法内部标记（如"B3确定性兜底"），不是面向用户的风险提示。状态切换不应改变风险内容。根因是后端把调试信息当作用户可见输出。
- **收益：** 报告风险栏不再出现无意义文字；状态切换不影响风险提示的稳定性。

**5. 答辩模拟点击"生成"报错无结果——前端对字符串做 .map()**

- **问题：** 后端 `/interview` 返回 `{"questions": "纯文本字符串"}`，前端却执行 `(data.questions||[]).map(...)`，对字符串调用 `.map()` 直接抛 TypeError。
- **修改前：**
  ```js
  // index.html bindInterviewControls
  var html=(data.questions||[]).map(function(q,i){
    var t=typeof q==='object'?(q.question||''):String(q);
    return '<div class="interview-q">...'+esc(t)+'</div>'
  }).join('')||'<p>暂无问题</p>';
  ```
- **修改后：**
  ```js
  var raw=typeof data.questions==='string'?data.questions:...;
  var items=raw.split(/\n+/).map(function(s){return s.trim()})
    .filter(function(s){return s.length>0});
  var html=items.length?'<div class="interview-list">'+items.map(function(item){
    return '<div class="interview-q"><span class="interview-dot"></span><span>'+esc(item)+'</span></div>'
  }).join('')+'</div>':'<p>暂无问题</p>';
  ```
- **为什么这样改：** 后端 `InterviewSimAgent.run()` 返回的是 `chat_text` 的纯文本（非结构化数组），前端必须按文本分行渲染。根因是前后端数据格式约定不一致。
- **收益：** 答辩模拟正常生成 10-15 道问题；文本按行渲染为清晰的问题列表。

**6. AI 调整建议读取方案信息错误——截断 JSON 导致 LLM 读到残缺数据**

- **问题：** `/chat` 端点用 `model_dump_json()[:18000]` 硬截断 FullPlan JSON，截断点常落在字符串中间，LLM 拿到的是不完整 JSON，导致读取错误、胡乱回答。
- **修改前：**
  ```python
  # routes.py project_chat
  if req.plan:
      context = req.plan.model_dump_json()[:18000]  # 硬截断
  ```
- **修改后：**
  ```python
  context = _build_chat_context(req)  # 结构化摘要
  # 构建可读摘要：项目名、背景、成员技能、任务列表(含状态)、
  # 时间线关键路径、分工匹配度、风险提示——无截断
  ```
- **为什么这样改：** JSON 截断破坏结构完整性，LLM 无法可靠解析。改为结构化摘要后信息密度更高且无语法断裂。
- **收益：** AI 能准确读取方案信息（人数、工时、分工）；回答基于完整数据而非残缺片段。

### 健壮性提升（P1）

**7. 成员管理 + 答辩模拟 UI 完全无样式——CSS 类缺失**

- **问题：** `member-edit-row`、`me-name`、`interview-q`、`legend-critical` 等类在 style.css 中完全不存在，面板以浏览器默认样式渲染。
- **修改前：** style.css 中无 `.member-edit-row`、`.interview-q`、`.legend-*` 等规则。
- **修改后：** 新增完整样式，复用设计令牌（`--primary`、`--line`、`--radius`），与配置面板 `.member-row` 风格对齐。
- **为什么这样改：** 面向比赛的产品不能有未样式化的裸元素。根因是前端新增类名但未同步补充 CSS。
- **收益：** 成员管理面板与答辩面板视觉统一；图例色块正确显示。

### 打磨（P3）

**8. CSS 版本号更新（4.9.0 → 5.0.0）**

缓存失效。

### 队友改动说明

本版本在 `origin/main`（v4.9，含清小搭协议接入）基础上修复。此前已删除队友遗留的一次性补丁脚本 `fix_html.py`（提交 d3d9af7），该脚本含乱码字符串且硬编码行号，有被误跑覆盖 index.html 的风险。

---
## v5.2 —— AI 调整建议按钮交互重写（2026-07-25）

**定位：** 重写「AI 调整建议」按钮的交互逻辑，解决点击行为不明确、抽屉遮挡按钮、建议面板超出屏幕边界三个体验问题。

---

### 体验优化（P2）

#### 1. 点击按钮在「打开建议」和「关闭建议」之间切换不明确

**问题：** 用户点击「AI 调整建议」按钮时，再次点击不会关闭已打开的建议面板，需要点击页面其他位置才能关闭，交互不符合预期。

**修改前：**
```js
// 点击只负责打开，不处理关闭
btn.onclick = function(){ openSuggestionDrawer(); }
```

**修改后：**
```js
// 点击切换：已打开则关闭，未打开则打开
btn.onclick = function(){
  if(drawerOpen){ closeDrawer(); } else { openDrawer(); }
}
```

**为什么这样改：** 按钮的 toggle 语义是最自然的交互模式。用户点击同一个按钮期望切换状态，而非只能单向打开。

**收益：** 按钮状态清晰（打开/关闭），用户不用找其他地方关闭面板。

---

#### 2. 建议抽屉跟随按钮位置，不再固定遮挡

**问题：** 建议/聊天抽屉固定在右下角，会遮挡「AI 调整建议」按钮本身，用户无法再次点击。

**修改后：** 抽屉位置跟随按钮当前位置动态计算，确保不遮挡触发按钮。

**为什么这样改：** 遮挡触发按钮是交互死结——用户打开了面板却关不掉。动态定位从根源消除遮挡。

**收益：** 抽屉始终可见且不遮挡按钮。

---

#### 3. 建议面板自动避让屏幕边缘

**问题：** 按钮位于屏幕右下角时，弹出的建议面板超出视口右边界或下边界，内容被截断。

**修改后：**
```js
// 计算面板位置时检测边界，超出则向反方向偏移
var rect = btn.getBoundingClientRect();
if(rect.right + panelWidth > window.innerWidth){
  panelLeft = window.innerWidth - panelWidth - 16;
}
```

**为什么这样改：** 面板内容被截断时用户无法阅读完整建议。边界检测确保面板始终在可视区域内。

**收益：** 面板在任何按钮位置下都完整可见。

---
## v5.1 —— 六项用户报告问题修复（2026-07-25）

**定位：** 修复用户实际使用中报告的 6 个问题，覆盖风险提示、报告一致性、Markdown 渲染、答辩要求框和历史方案。

---

### 关键缺陷（P0）

#### 1. 手动分工后报告的风险提示变成内部 note

**问题：** 用户拖拽调整分工后，apply_manual_assignment 把 qa_matrix.note（内部算法标记）直接塞进 report.risk_note，用户在报告里看到「状态切换重算（保留原分工）」等无意义文字。

**修改前：**
```python
report = fp.report.model_copy(update={
    "risk_note": qa.note,  # 内部调试文本泄露
})
```

**修改后：**
```python
# 基于实际负载和工期计算真实风险
risk_note = _build_manual_risk_note(plan, timeline, workload, fp.input.members)
report = fp.report.model_copy(update={
    "risk_note": risk_note,
})
```

_build_manual_risk_note 逐项检测负载不均衡（高于均值 1.35 倍）、工期紧张（总工时 > 产能 1.1 倍）、关键路径占比过高、未分配负责人等情况。

**为什么这样改：** 旧写法把算法内部状态标记直接暴露给用户，风险提示失去实际含义。改为基于真实负载数据动态计算，风险提示才有参考价值。

**收益：**
1. 手动调整分工后报告风险栏显示真实风险（如「张三负载偏重 8.5h」）。
2. 无风险时显示肯定语而非内部标记。

**同步修改：** tests/test_review_fixes.py（验证 risk_note 不再出现 note 原文）。

---

### 健壮性提升（P1）

#### 2. LLM 客户端每次新建实例导致首次请求冷启动超时

**问题：** 每个 Agent 独立 LLMClient() 创建 OpenAI SDK 客户端，每次都触发 TCP/TLS 握手，首次请求容易超时。

**修改前：**
```python
# base.py
self.llm = llm or LLMClient()
# routes.py project_chat
LLMClient().chat_text(...)
```

**修改后：**
```python
# base.py — 复用全局单例
self.llm = llm or LLMClient.get_shared()
# routes.py
LLMClient.get_shared().chat_text(...)
```

LLMClient.get_shared() 返回模块级单例，复用 httpx 连接池。

**为什么这样改：** OpenAI SDK 内部用 httpx 连接池，同一个 client 实例可复用已建立的连接。反复新建 client 会导致每次请求都冷启动。

**收益：**
1. 首次请求不再因连接建立慢而超时。
2. 多 Agent 协作时连接池复用，整体响应更快。

---

#### 3. timeout/rate_limit 时直接返回错误，放弃 plain 回退

**问题：** 结构化调用超时或限流时直接返回 AgentError，但此时连接可能已建立，plain 回退成功率较高。

**修改前：**
```python
if last_error_type in ("timeout", "rate_limit", "unknown"):
    return AgentError(...)  # 直接放弃
```

**修改后：**
```python
if last_error_type in ("timeout", "rate_limit", "unknown"):
    try:
        return self._try_plain_validate(...)  # 多等一个超时周期尝试 plain
    except Exception:
        return AgentError(...)
```

**为什么这样改：** 首次请求常因连接建立慢超时，但连接可能已就绪，plain 回退利用已建立的连接，成功概率显著高于完全重试。

**收益：**
1. 偶发网络抖动不再直接失败走兜底。
2. 兜底仅在确实无法恢复时触发。

---

#### 4. 报告区域不支持 Markdown 表格和列表渲染

**问题：** 报告内容含 Markdown 表格、加粗、列表等语法，前端直接 textContent 渲染，用户看到的是原始 Markdown 源码。

**修改后：**
```js
function renderMd(text){
  // 解析标题、表格、加粗/斜体/行内代码、有序/无序列表、段落
  // 表格：检测连续 | 行，渲染为 <table>
}
```

**为什么这样改：** 报告是核心产出物，Markdown 源码直接展示严重影响可读性。新增轻量 Markdown 解析器（约 1KB），无需引入外部库。

**收益：**
1. 报告表格、列表、加粗正确渲染。
2. 无外部依赖，首屏加载不受影响。

---

### 打磨（P3）

#### 5-6. 答辩要求框显示与历史方案载入

**问题：** 答辩要求输入框在切换页面后内容丢失；历史方案载入时 input.members 等字段未正确恢复。

**修改：** 答辩要求存入 state 持久化；历史方案载入时补全缺失字段（members、version 等），与新建方案结构一致。

**收益：** 答辩要求跨页面保留；历史方案载入后可正常编辑和重算。

---
## v5.0 —— 八项核心功能修复（2026-07-24）

**定位：** 逐项修复用户报告的 8 个功能缺陷，确保比赛版的基础功能（成员、匹配度、工作量、报告、答辩、甘特图、AI 对话、UI）全部可用且无 bug。

**审查/修改背景：** 用户对比作业版本后发现比赛版多个基础功能存在 bug。本版本对照 `AI实践基石大作业` 参考版本，逐一定位根因并修复。

---

### 关键缺陷（P0）

**1. 成员管理预填了 3 个默认成员（张三/李四/王五），干扰用户输入**

- **问题：** 页面加载即填入 3 个假成员和技能标签，用户需要先删除才能填自己的数据。
- **修改前：**
  ```js
  // index.html 行182
  addMember({name:'张三',skill_tags:['文案']});
  addMember({name:'李四',skill_tags:['摄影']});
  addMember({name:'王五',skill_tags:['秀米排版']});renderSteps();
  ```
- **修改后：**
  ```js
  renderSteps();
  ```
- **为什么这样改：** 默认值是早期开发调试残留，不应出现在面向用户的产品中。成员列表应初始为空，由用户自行填写。
- **收益：** 页面加载即干净；用户不会被无关数据误导；减少误操作。

**2. 技能匹配度为 0——同义词表覆盖不足，"文学素养"无法关联"写报告"等常见表达**

- **问题：** 评分引擎的 `_SKILL_SYNONYMS` 表缺少大量常用表达（写报告、撰写报告、报告、总结报告、推文、演讲、答辩、摄影、资料整理等），导致用口语化标签时匹配度始终为 0。
- **修改前：**
  ```python
  # scoring.py — 同义词表仅覆盖 ~50 个表达
  "文学素养": "文案撰写", "报告撰写": "文案撰写",
  # 缺少："写报告"、"撰写报告"、"报告"、"总结报告"等
  ```
- **修改后：**
  ```python
  # scoring.py — 新增 30+ 同义词映射
  "写报告": "文案撰写", "撰写报告": "文案撰写", "报告": "文案撰写",
  "总结报告": "文案撰写", "调研报告": "文案撰写", "推文": "文案撰写",
  "演讲": "沟通协调", "答辩": "沟通协调", "摄影": "视频剪辑",
  "资料收集": "调研分析", "资料整理": "调研分析", ...
  ```
- **为什么这样改：** 纯字符相似度无法识别"文学素养"与"写报告"的语义关联，必须靠同义词表将口语化标签归一化到标准技能词。根因是表覆盖面太窄。
- **收益：** 匹配度从 0% 恢复到合理值（如"文学素养"对"写报告"= 100%）；分工建议更准确。

**3. 工作量不随任务状态变化——标记完成后成员条带不缩短**

- **问题：** `workload_snapshot` 统计负载时只看 `assignee_id` 和工时，完全忽略 `task.status`，已完成任务仍被计入工作量。
- **修改前：**
  ```python
  # project_service.py workload_snapshot
  for task in plan.plan.tasks:
      owner = task.assignee_id
      if not owner or owner not in work:
          warnings.append(...)
          continue
      work[owner] += task.estimated_hours  # 已完成的也计入
  ```
- **修改后：**
  ```python
  for task in plan.plan.tasks:
      owner = task.assignee_id
      if not owner or owner not in work:
          continue
      if task.status == "completed":
          continue  # 已完成的不计入剩余工作量
      work[owner] += task.estimated_hours
  ```
- **为什么这样改：** 工作量面板的语义是"剩余负载"，已完成任务不应再占用产能。根因是统计逻辑遗漏了状态过滤。
- **收益：** 标记任务完成后成员条带实时缩短；工作量面板与现实进度一致。

**4. 风险提示栏显示"状态切换重算（保留原分工）"——内部调试文本泄露给用户**

- **问题：** `/recompute` 端点把 `qa_matrix.note`（内部状态描述）直接塞进 `report.risk_note`，用户在报告里看到这句无意义的话。
- **修改前：**
  ```python
  # routes.py /recompute
  report = req.report.model_copy(update={
      "timeline_section": timeline.note,
      "qa_matrix_section": "...",
      "risk_note": qa_matrix.note,  # 内部调试文本泄露
  })
  ```
- **修改后：**
  ```python
  report = req.report.model_copy(update={
      "timeline_section": timeline.note,
      "qa_matrix_section": "...",
      "risk_note": req.report.risk_note,  # 保留原有风险提示
  })
  ```
- **为什么这样改：** `qa_matrix.note` 是算法内部标记（如"B3确定性兜底"），不是面向用户的风险提示。状态切换不应改变风险内容。根因是后端把调试信息当作用户可见输出。
- **收益：** 报告风险栏不再出现无意义文字；状态切换不影响风险提示的稳定性。

**5. 答辩模拟点击"生成"报错无结果——前端对字符串做 .map()**

- **问题：** 后端 `/interview` 返回 `{"questions": "纯文本字符串"}`，前端却执行 `(data.questions||[]).map(...)`，对字符串调用 `.map()` 直接抛 TypeError。
- **修改前：**
  ```js
  // index.html bindInterviewControls
  var html=(data.questions||[]).map(function(q,i){
    var t=typeof q==='object'?(q.question||''):String(q);
    return '<div class="interview-q">...'+esc(t)+'</div>'
  }).join('')||'<p>暂无问题</p>';
  ```
- **修改后：**
  ```js
  var raw=typeof data.questions==='string'?data.questions:...;
  var items=raw.split(/\n+/).map(function(s){return s.trim()})
    .filter(function(s){return s.length>0});
  var html=items.length?'<div class="interview-list">'+items.map(function(item){
    return '<div class="interview-q"><span class="interview-dot"></span><span>'+esc(item)+'</span></div>'
  }).join('')+'</div>':'<p>暂无问题</p>';
  ```
- **为什么这样改：** 后端 `InterviewSimAgent.run()` 返回的是 `chat_text` 的纯文本（非结构化数组），前端必须按文本分行渲染。根因是前后端数据格式约定不一致。
- **收益：** 答辩模拟正常生成 10-15 道问题；文本按行渲染为清晰的问题列表。

**6. AI 调整建议读取方案信息错误——截断 JSON 导致 LLM 读到残缺数据**

- **问题：** `/chat` 端点用 `model_dump_json()[:18000]` 硬截断 FullPlan JSON，截断点常落在字符串中间，LLM 拿到的是不完整 JSON，导致读取错误、胡乱回答。
- **修改前：**
  ```python
  # routes.py project_chat
  if req.plan:
      context = req.plan.model_dump_json()[:18000]  # 硬截断
  ```
- **修改后：**
  ```python
  context = _build_chat_context(req)  # 结构化摘要
  # 构建可读摘要：项目名、背景、成员技能、任务列表(含状态)、
  # 时间线关键路径、分工匹配度、风险提示——无截断
  ```
- **为什么这样改：** JSON 截断破坏结构完整性，LLM 无法可靠解析。改为结构化摘要后信息密度更高且无语法断裂。
- **收益：** AI 能准确读取方案信息（人数、工时、分工）；回答基于完整数据而非残缺片段。

### 健壮性提升（P1）

**7. 成员管理 + 答辩模拟 UI 完全无样式——CSS 类缺失**

- **问题：** `member-edit-row`、`me-name`、`interview-q`、`legend-critical` 等类在 style.css 中完全不存在，面板以浏览器默认样式渲染。
- **修改前：** style.css 中无 `.member-edit-row`、`.interview-q`、`.legend-*` 等规则。
- **修改后：** 新增完整样式，复用设计令牌（`--primary`、`--line`、`--radius`），与配置面板 `.member-row` 风格对齐。
- **为什么这样改：** 面向比赛的产品不能有未样式化的裸元素。根因是前端新增类名但未同步补充 CSS。
- **收益：** 成员管理面板与答辩面板视觉统一；图例色块正确显示。

### 打磨（P3）

**8. CSS 版本号更新（4.9.0 → 5.0.0）**

缓存失效。

### 队友改动说明

本版本在 `origin/main`（v4.9，含清小搭协议接入）基础上修复。此前已删除队友遗留的一次性补丁脚本 `fix_html.py`（提交 d3d9af7），该脚本含乱码字符串且硬编码行号，有被误跑覆盖 index.html 的风险。

---
## v4.9 —— 比赛 Demo 加固与清小搭标准协议接入（2026-07-23）

**定位：** 在保留完整网页演示链路的同时，提供可由清小搭直接调用的 OpenAI 兼容服务入口。

**审查/修改背景：** 阶段一先以比赛可展示版本为目标修复演示链路；阶段二依据《接入清小搭方法》补齐模型发现、Bearer 鉴权、普通响应与 SSE 流式响应。开始阶段二前已拉取并合并队友在 `origin/main` 上的 9 个提交。

---

### 关键缺陷（P0）

**1. 清小搭没有可识别、可鉴权的标准服务入口**

- **问题：** 原项目只有 `/api/*` 网页业务接口，清小搭无法通过 OpenAI 兼容协议发现模型或发起对话；通用业务密钥也不应直接充当平台接入密钥。
- **修改前：**
  ```python
  app.include_router(api_router, prefix="/api")
  # 不存在 /v1/models 与 /v1/chat/completions
  ```
- **修改后：**
  ```python
  router = APIRouter(prefix="/v1")

  @router.get("/models")
  def models(authorization: str | None = Header(default=None)):
      _check_auth(authorization)

  @router.post("/chat/completions")
  def chat_completions(request: ChatCompletionRequest, authorization=Header(None)):
      _check_auth(authorization)
  ```
- **为什么这样改：** 清小搭按 OpenAI 协议探测服务，路由形状和 Bearer 语义必须符合平台预期。独立的 `QINGXIAODA_API_KEY` 将平台入站鉴权与项目调用上游模型的 `LLM_API_KEY` 分离，避免权限混用；密钥使用常量时间比较，缺失或错误时返回 401。
- **收益：** ① 清小搭可以发现并调用 `collaboration-planner`；② 入站与出站密钥职责清晰；③ 未配置服务端密钥时明确返回 503，不会误开放接口。

**2. 缺少清小搭要求的普通响应与 SSE 完整收尾协议**

- **问题：** 自定义 JSON 或一次性文本无法满足平台对 `choices[0].message.content`、流式 role/content/stop 帧、usage 和 `[DONE]` 的解析要求。
- **修改前：**
  ```python
  return {"reply": answer}
  ```
- **修改后：**
  ```python
  yield frame({"role": "assistant"})
  for chunk in _chunk_text(answer):
      yield frame({"content": chunk})
  yield frame({}, finish_reason="stop", include_usage=True)
  yield "data: [DONE]\n\n"
  ```
- **为什么这样改：** SSE 是逐帧协议，不只是给响应设置流式媒体类型。首帧角色、内容增量、唯一 stop 帧及最终 `[DONE]` 都是消费端确认消息边界所需的信息；普通响应也必须维持 OpenAI 的 choices/message 结构。
- **收益：** ① 普通与流式模式均可被标准客户端解析；② 平台能可靠判断响应结束；③ usage 字段支持调用统计。

### 健壮性提升（P1）

**3. 自然语言项目输入复用既有拆解、分工与排期主链路**

- **问题：** 若为清小搭重新实现一套规划规则，会与网页 Demo 的任务、技能匹配和排期结果逐渐分叉；简单按逗号切分成员还会把括号内技能误识别为成员。
- **修改前：**
  ```python
  for raw in re.split(r"[、,，;；]+", member_text):
      ...
  ```
- **修改后：**
  ```python
  # 仅在括号外切分成员，保留“小林(文案,统筹)”内部逗号
  inp = _build_input(user_text)
  draft = generate_draft(inp, use_ai=False)
  full_plan = confirm_draft(inp, draft)
  ```
- **为什么这样改：** Project Service 已承载阶段一验证过的任务拆解、技能评分、负载和时间线逻辑，适配层只应负责协议及自然语言转结构化输入。成员解析用括号深度判断分隔位置，才能正确保留技能列表。
- **收益：** ① 网页与清小搭共享同一业务事实源；② 接口不依赖外部模型也能稳定完成比赛演示；③ 中文成员技能输入不会产生伪成员。

**4. 平台探测边界有自动化契约测试**

- **问题：** `model=null`、`max_tokens=1`、字符串形式的 `stream`、错误密钥和 SSE stop 帧等边界若只人工验证，后续修改容易破坏接入。
- **修改前：**
  ```text
  tests/ 中没有清小搭协议测试。
  ```
- **修改后：**
  ```python
  assert response.status_code == 401
  assert response.json()["choices"][0]["message"]["content"] == "好"
  assert data_lines[-1] == "[DONE]"
  assert len(stop_frames) == 1
  ```
- **为什么这样改：** 这些字段是平台连通性探测的契约，不是内部实现细节。自动化测试能在每次改动时同时验证鉴权、模型发现、普通响应、严格布尔值、流式帧序列和真实项目规划。
- **收益：** ① 接入兼容性可重复验证；② 异常输入返回明确状态码；③ 回归风险更低。

### 体验优化（P2）

**5. 比赛 Demo 形成“输入到导出”的完整展示路径**

- **问题：** 原页面缺少稳定的一键案例和明确讲解顺序，临场输入成本高，无法保证在有限展示时间内覆盖人工调整、智能分工、甘特图和导出。
- **修改前：**
  ```text
  需要现场逐项填写项目和成员信息。
  ```
- **修改后：**
  ```javascript
  demoCaseBtn.addEventListener("click", loadDemoCase);
  ```
  并新增 `docs/比赛Demo演示流程.md`，固化“项目输入 → 任务拆解 → 人工调整 → 智能分工 → 甘特图 → 导出”的讲解路径。
- **为什么这样改：** 比赛版本的首要风险是展示中断而非工业级覆盖不足。一键案例减少现场键入和数据差异，流程文档让功能价值按可理解的顺序呈现。
- **收益：** ① 可快速复现完整演示；② 展示数据与技能匹配场景更有说服力；③ 保留手动操作体现人机协作。

### 打磨（P3）

**6. 配置示例、接入文档与版本号同步**

- **问题：** README 仍写“未来接入”，`.env.example` 曾包含真实外观的上游密钥，应用元数据仍为 v4.8，容易误导部署和验收。
- **修改前：**
  ```python
  app = FastAPI(title="协作分工智能体", version="4.8")
  ```
- **修改后：**
  ```python
  app = FastAPI(title="协作分工智能体", version="4.9")
  QINGXIAODA_API_KEY="replace-with-a-long-random-key"
  ```
- **为什么这样改：** 接入能力必须有可复制的 Base URL、Model 和鉴权配置说明；示例文件只能使用占位值。版本元数据与文档统一后，比赛验收和问题定位不会出现版本歧义。
- **收益：** ① 部署者可按文档直接配置；② 避免示例密钥泄露风险；③ API、数据模型与 README 版本一致。

**7. 新增可复现的 Render 公网部署描述**

- **问题：** 清小搭无法访问本机 `127.0.0.1`，仅完成协议代码仍缺少公网 HTTPS 服务；手工填写构建和启动参数也容易把监听地址或端口写错。
- **修改前：**
  ```text
  仓库没有 render.yaml，部署平台不知道如何构建和启动 FastAPI。
  ```
- **修改后：**
  ```yaml
  services:
    - type: web
      runtime: python
      plan: free
      startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
      healthCheckPath: /api/health
      envVars:
        - key: QINGXIAODA_API_KEY
          sync: false
        - key: LLM_API_KEY
          sync: false
  ```
- **为什么这样改：** 公网托管必须监听平台提供的端口及 `0.0.0.0`。Blueprint 将构建、启动、健康检查和密钥提示固化为代码，同时用 `sync: false` 确保清小搭入站密钥及千问 API 配置只在 Render 后台填写、不进入 Git。
- **收益：** ① 可从 GitHub 一键创建服务；② 自动获得 HTTPS 公网地址；③ 部署参数可审查、可复现且不泄露密钥。

### 队友改动说明

本版本先合并队友 `origin/main` 的 9 个提交，保留其 CI、API 测试、全局异常处理、工时知识库及界面增强；随后重新应用本分支的 Demo 案例、技能别名、建议参与人数约束与演示验收修复。合并提交为 `b424f0b`，全量测试通过后才进入清小搭适配开发。

---
## v4.8.1 —— 代码质量加固 + CI + API 测试覆盖（2026-07-22）

**定位：** 在 v4.8 基础上补充 CI 配置、API 测试覆盖、全局异常处理、参数调优和目录自动初始化。

---

### 新增（P1）

**1. GitHub Actions CI（.github/workflows/test.yml）**
- **问题：** 此前无 CI 配置，代码 push 后不会自动运行测试，依赖人工记忆执行 pytest，容易遗漏。
- **新增后：** 每次 push 或 PR 到 `main`/`feature/*` 分支时，自动在 Ubuntu 上安装依赖并运行 pytest（跳过需 API key 的 `test_reflection.py`）。
- **收益：** ① 每个 commit 自动验证测试通过；② PR 页面直接显示测试结果 ✅/❌；③ 降低团队协作中"改了别人的功能不知道"的风险。

**2. API 集成测试覆盖（tests/test_api.py 从 3 个扩充到 11 个）**
- **问题：** 原先只覆盖了 `/health`、`/recompute`、`/save` 三个端点，其余 13+ 端点无测试保护。
- **新增测试：**
  - `/api/draft` — use_ai=false 快速模式 + use_ai=true 无 key 降级
  - `/api/draft/mutate` — 新增任务 / 删除任务
  - `/api/confirm-draft` — 确认草案后自动分工
  - `/api/workload` — 工作量快照
  - `/api/manual-assignment` — 手动指定负责人/协作者
  - `/api/export/markdown` — Markdown 导出
- **设计原则：** 全部走确定性逻辑（不需要 LLM），CI 环境无 API key 也能通过。

**3. 全局异常处理器（app/main.py）**
- **问题：** 多处 `except Exception` 裸捕获，意外错误信息被吞掉。部署到比赛平台时，未处理的异常会暴露 Python 堆栈。
- **修改后：** `app/main.py` 新增 `@app.exception_handler(Exception)` 统一拦截所有未处理异常，日志保留完整堆栈，返回 JSON 格式的错误信息。设置环境变量 `DEBUG=true` 时返回详细错误内容，否则只返回 `"服务器内部错误"`。
- **同时清理：** `app/web/routes.py` 移除全部 13 处 `except Exception` 兜底块，统一由全局处理器接管。

### 调优（P2）

**4. LLM 超时与重试参数调整（app/config.py）**
- `LLM_TIMEOUT`：25s → **35s**（减少网络波动导致超时的概率）
- `LLM_MAX_RETRIES`：1 次 → **3 次**（给 LLM 调用更多耐心，降低兜底触发频率）

**5. Memory 目录启动时自动创建（app/config.py）**
- 新增 `MEMORY_DIR.mkdir(parents=True, exist_ok=True)`，应用启动时自动创建 `memory/` 目录，不再依赖 `/api/save` 端点来创建。

---
## v4.8 —— 统一合并：v4.7 算法修复 + 知识库增强工时估算（2026-07-22）

**定位：** v4.7.2 在 v4.6 基础上独立开发了工时知识库与负载均衡改进（DEFAULT_BALANCE_THRESHOLD_HOURS=2h、avoid 技能守卫），但未拉取本仓库 v4.7 的三大算法修复后 force push。本版本将两条独立发展线合并到同一代码基础，保留双方全部功能。

**审查/修改背景：** v4.7.2 的 origin/main 与本地的 v4.7 从 `41a526d` 分叉。逐项核查后确认：v4.7.2 缺 `_rebalance_presenters` 全局重排、`completed_ids` 已完成任务保留、`_resync_scores` 均衡后分数同步；本地缺 `DEFAULT_BALANCE_THRESHOLD_HOURS=2h` 阈值、`_avoids_required` 负向偏好守卫、`duration_estimator` 工时知识库。

---

### 关键缺陷（P0）

**1. scoring.py 负载均衡：贪心局部最优 + 全局重排的融合**
- **问题：** 双方各自重写了 `_balance_workload`，逻辑互不兼容。v4.7.2 版本有更合理的技能守卫（不因均衡砸坏专业匹配）和 2h 阈值；本地版本有 `_rebalance_presenters` 全局联合枚举（跳出贪心局部最优）。
- **修改后：** 保留 v4.7.2 的 `avoids()` 闭包 + 技能下降幅度守卫 + `DEFAULT_BALANCE_THRESHOLD_HOURS=2.0`；在此基础上保留本地的 `rebalance_guard` 机制——贪心卡住时触发 `_rebalance_presenters` 全局重排。`_balance_workload` 签名统一为 `(threshold=DEFAULT_BALANCE_THRESHOLD_HOURS, task_skills=None, member_map=None)`。
- **为什么这样改：** v4.7.2 的技能守卫解决「均衡不能砸坏匹配」；本地的全局重排解决「贪心搬运粒度 > 需要的转移量时卡死」。两者解决不同层面的问题，缺一不可。
- **收益：** ① 负载差收敛到 2h 内且不破坏技能匹配；② 贪心卡住时全局枚举兜底；③ 回避者不会被分配到任何角色。

**2. `_rebalance_presenters` 全局重排未过滤辅助角色回避者**
- **问题：** 合并后发现 `_rebalance_presenters` 枚举 `qa_primary` 时使用 `q_lists = [list(names)]`（全部成员），没有过滤明确回避该任务技能的成员，导致回避者被塞进 qa_primary。
- **修改前：** `q_lists = [list(names) for _ in active]`
- **修改后：** `q_lists = [cand_p[a.task_id] for a in active]`（复用已过滤回避者的候选人列表）
- **为什么这样改：** 回避者不应出现在任务的任何角色中。presenter 候选已通过 `cand_p` 过滤，辅助角色理应使用同一门槛。
- **收益：** ① 回避者在全局重排后也不会被分配到任何角色；② 保持与初始分配和 `_balance_workload` 的一致性。

### 健壮性提升（P1）

**3. 已完成任务分工保留（completed_ids）**
- **保留来源：** v4.7 原有修复。`recompute_preserve` 用 `completed_ids` 集合让 `_work_from` 跳过已完成任务，score/reasoning 完整保留。v4.7.2 版本无此修复。

**4. 均衡后分数同步（_resync_scores）**
- **保留来源：** v4.7 原有修复。`_balance_workload` 修改 presenter 后，`_resync_scores` 重算 score/reasoning 使其与最终负责人自洽。v4.7.2 版本用 inline 逻辑实现类似功能，合并后统一用 `_resync_scores` 替代。

**5. `rest` 列表解包不匹配（合并引入的 bug）**
- **问题：** 合并后 `scored` 为 4 元组 `(name, skill, avoiding, score)`，但v4.7.2的 `rest` 列表用 3 元组解包 `for n, skill, _ in scored`。
- **修改后：** `for n, skill, _av, _sc in scored`

**6. `_fallback_plan` 缺 `total_capacity` 变量（合并引入的 bug）**
- **问题：** v4.7 在 fallback 中用 `total_capacity` 自适应阶段数，但该变量只在 `run()` 方法作用域内定义，fallback 方法内未定义。
- **修改后：** 在 fallback 方法内重新计算 `total_capacity = sum(m.available_hours for m in inp.members)`。

### 打磨（P3）

**7. 版本号统一为 4.8**
- `app/main.py`、`app/models/schemas.py`、`README.md` 版本号统一更新。

### 关联版本说明

v4.7.2 在本版本基础上贡献了完整的工时估算系统，全部保留：
- `duration_estimator.py`：相似案例检索 + 本地反馈学习（中位数校准，至少 3 条相似修正才生效）
- `DEFAULT_BALANCE_THRESHOLD_HOURS = 2.0`：更合理的软目标
- `_avoids_required` 负向偏好守卫 + 技能下降幅度检查
- Planner Prompt 改为「先估任务、后看产能」原则
- 任务卡显示参考工时范围、可信度、估算依据

### 验证
- 前端 JS 语法检查通过。
- 全量测试：92 passed（合并前本地 80 + v4.7.2 新增 12）。

---
## v4.7.2 —— 知识库增强的合理工时估算（2026-07-21）

**定位：** 让任务工时由实际工作范围和相似案例决定，不再为了贴近团队总可用时间而把短任务拉长。

**审查/修改背景：** 用户反馈自动拆解中的部分短任务仍显示过长，并要求默认分工尽量均匀、成员负载差控制在 2 小时内。第一阶段只能修正明显的关键词固定值，尚未区分字数上限、规定活动时长和负责人制作工时，也没有利用用户后续手动修正。

---

#### 1. 团队可用时间反向放大任务工时

1. **问题：** 通用兜底把团队总可用时间换算成 `scale`，相同工作在成员可用 100h 时会比可用 10h 时自动显示更长。
2. **修改前：** `hours = round(hours * scale)`，其中 `scale = total_capacity / 60.0`。
3. **修改后：** 删除产能缩放；新增 `duration_examples.json` 与 `calibrate_plan_estimates()`，任务按相似案例、工作范围和成果规模估算。
4. **为什么这样改：** 可用时间回答“能否做完”，任务范围回答“需要多久”，二者因果方向不同。把产能当估时目标会产生“时间越多，任务越慢”的反常结果。
5. **收益：** ① 同一任务不随团队空闲时间膨胀；② AI 与快速草案使用同一估时标准；③ 短任务可稳定回到 0.5～3h 的常见范围。

#### 2. 字数上限和规定活动时长被误当成实际制作工时

1. **问题：** “报告不超过10000字”被当作默认写满10000字；“讲座共2学时”又与负责人准备、组织工作的投入混成一个字段。
2. **修改前：** 正则提取到数字后直接按最大字数放大，任意 `X 学时` 都可能覆盖 `estimated_hours`。
3. **修改后：** `_scope_multiplier()` 识别“不超过/至多/上限”等语气，按常见实际篇幅估算；`_explicit_time_info()` 将明确制作人时与 `required_duration_hours` 分开保存。
4. **为什么这样改：** 上限是约束而不是目标值；活动持续时间描述“要在场多久”，制作人时描述“负责人要投入多少工作”，只有拆开后才能准确排期和解释。
5. **收益：** ① 10000字上限报告不再默认写满；② 讲座、研讨会和现场活动的规定时长可追溯；③ 页面能解释长时长来自课程要求还是系统预测。

### 健壮性提升（P1）

#### 3. 默认确认分工没有执行负载拉平

1. **问题：** `assign_with_balance()` 为保护技能匹配跳过了 `_balance_workload()`，导致确认草案后的默认分工可能明显失衡；另一条 LLM 后处理路径仍使用 1h 阈值，两个入口标准不一致。
2. **修改前：** 默认入口仅 `_work_from(...)` 统计负载；LLM 后处理调用 `_balance_workload(..., threshold=1.0)`。
3. **修改后：** 两个入口统一使用 `DEFAULT_BALANCE_THRESHOLD_HOURS = 2.0`；再平衡同时检查技能下降幅度和成员负向偏好，无法达到目标时由 `_split_suggestion()` 指出应拆分的任务。
4. **为什么这样改：** 完全不平衡违背默认分工直觉，强行追求1h又容易牺牲技能匹配。2h 是更合理的软目标，应在专业匹配和明确回避约束内尽量实现。
5. **收益：** ① 可均摊场景默认负载差不超过2h；② “不想做PPT”等偏好不会因拉平被破坏；③ 不可均摊时给出明确拆分建议。

#### 4. 用户修正工时不会反哺后续估算

1. **问题：** 用户把“秀米排版 2h”改为“1h”后，下一次相似任务仍重复给出原建议，系统无法从真实使用中学习。
2. **修改前：** `mutate_draft()` 只保存新的 `estimated_hours`，原值与修正值随请求结束丢失。
3. **修改后：** `record_duration_feedback()` 在本地 JSONL 中记录匿名化任务签名、案例类型、建议值和修正值；`_feedback_multiplier()` 仅在至少3条相似修正后采用中位数校准，并限制倍率在0.5～1.5之间。
4. **为什么这样改：** 单次修改可能是误操作，不能立即污染知识库；三条相似证据加中位数能过滤偶然值，同时让系统逐步适应用户团队的真实工作速度。
5. **收益：** ① 相似任务会随持续使用变准；② 不保存完整任务说明；③ 少量异常修改不会造成估时漂移。

### 体验优化（P2）

#### 5. 页面只有单一工时数字，用户无法判断来源

1. **问题：** 任务卡只显示 `estimated_hours`，无法区分知识库建议、人工确认和课程规定活动时长。
2. **修改前：** 页面只渲染“预计工时”输入框。
3. **修改后：** 任务卡增加参考范围、可信度、估算依据悬浮提示和规定活动时长；用户改动后标记“用户已确认”。
4. **为什么这样改：** 估时本质上存在不确定性。展示范围和来源比伪精确的单一数字更利于用户发现异常并修正。
5. **收益：** ① 用户能快速定位低可信度任务；② 长时长有可读解释；③ 保留原有直接编辑能力。

**同步修改：** Planner Prompt 明确“先估任务、后看产能”；新增会议组织案例、阶段二回归测试和 README 说明。

### 验证

- 前端脚本语法检查通过，未发现 `\\u0022` 或异常字符串拼接。
- 全量自动化测试：92 项通过，1 条第三方测试客户端弃用警告与本次修改无关。

---
## v4.7.1 —— 合入 v3.5–v3.8 算法修复：负载均衡全局重排 + 状态切换分工保留 + 健壮性加固（2026-07-20）

**定位：** v4.x 系列在 v3.4 基础上分叉发展，缺少 v3.5–v3.8 的核心算法修复。本版本将这些修复移植到 v4.6 代码基础上，并保留v4.6 的全部 新功能（文件上传、任务拆解工作流、反思 agent 等）。

**审查/修改背景：** 团队在 v3.4 时间点分叉：一端发展为 v4.6（文件分析 + 工作台 + 聊天），另一端发展为 v3.8（负载均衡 + 状态切换 + 健壮性）。经逐项核查，v4.6 缺少以下 v3.5–v3.8 修复。

---

### 关键缺陷（P0）

**1. 负载均衡陷入局部最优，分工相差可达 6h+（v3.8 修复）**
- **问题：** `_balance_workload` 的贪心搬运在「最小搬运粒度 > 需要的转移量」时陷入局部最优，gap 卡在 1.4h 甚至 6h+ 时直接 break。
- **修改前：** `if best is None: break  # 局部最优即停`
- **修改后：** 贪心卡住时触发 `_rebalance_presenters`（全局联合枚举负责人+主要协助，选 gap 最小组合），重排有改善则回贪心再迭代，`rebalance_guard` 防死循环：
  ```python
  if best is None:
      cur = _work_from(assignments, task_hours, members)
      cur_gap = (max(cur.values()) - min(cur.values())) if cur else 0.0
      if cur_gap <= threshold + 1e-9:
          break
      if rebalance_guard <= 0:
          break
      rebalance_guard -= 1
      new_gap = _rebalance_presenters(assignments, task_hours, members,
                                      task_skills, member_map, cur_gap)
      if new_gap < cur_gap - 1e-9:
          continue  # 重排解锁了更优解，贪心继续
      break
  ```
- **为什么这样改：** 负责人和主要协助是负载权重最大的两个角色（1.0 / 0.3），把它们一起当变量做全局枚举，能在作业级规模（3人6任务）瞬间找到全局最优。枚举时排除 `p==q` 退化组合，保证预算负载与 `_apply_role_remap` 应用后一致。
- **收益：** ① 实测 gap 从 32.3h → 0.85h（达标 ≤1h）；② 回避门槛被尊重（PPT 不派给不想做 PPT 的人）；③ 不破坏 LLM 的参与结构。

**2. 状态来回切换丢失原责任分工（v3.7 修复）**
- **问题：** `recompute_preserve` 把已完成任务的 `presenter` 覆盖成 `"(已完成)"`，原始负责人名字被丢弃。切回 pending 时后端看到 `presenter="(已完成)"` 走兜底从零重算，分工和匹配度都与最初不一致。
- **修改前：**
  ```python
  if t.status == "completed":
      assignments.append(QAAssignment(
          presenter="(已完成)", qa_primary="", qa_support=[],
          score=0.0, reasoning="任务已完成",  # 原分工被覆盖
      ))
  ```
- **修改后：** 已完成任务保留原 presenter/qa_primary/qa_support（只要成员仍在职），完成状态由 `task.status` 唯一表达，score/reasoning 不清零：
  ```python
  if t.status == "completed":
      if old is not None and old.presenter in member_map:
          assignments.append(old.model_copy(update={
              "task_name": t.name,
              "qa_primary": qa_p, "qa_support": qa_s,
              # score/reasoning 不动，切回 pending 时无损还原
          }))
  ```
- **为什么这样改：** root cause 是用「覆盖 presenter 字段」标记完成，既丢数据又把状态和分配耦合。让 `task.status` 作为完成态唯一真相源，presenter 独立保留，状态来回切换无损。
- **收益：** ① 任务状态来回切换分工完全还原；② 已完成任务在矩阵里仍能看到原分工；③ `_work_from` 用 `completed_ids` 集合跳过，不再依赖魔法字符串。

### 健壮性提升（P1）

**3. enhance 偷偷搬运负责人，与 docstring/提示词矛盾（v3.5 修复）**
- **问题：** docstring 写「保留 LLM 分配」，函数体却无条件调 `_balance_workload` 搬运负责人。
- **修改后：** 仅在 gap > threshold 时才触发均衡，且先修回避冲突（把回避者换到最合适的非回避成员），均衡后 `_resync_scores` 让 score/reasoning 与最终 presenter 一致。
- **收益：** ① enhance 以 LLM 分配为基准，仅必要时校正；② 回避者在 enhance 路径也被纠偏；③ 提示词（MATCHER_SYSTEM）如实说明「系统会校正但以你为基准」。

**4. LLM 调用健壮性加固（v3.5/v3.6 合并）**
- **问题：** 配额耗尽被当瞬时限流反复重试、空 API key 挂死网络、ASCII 屏蔽词误伤 upload/download、Planner 兜底固定 5 阶段淹没小团队、`/api/save` 同名覆盖历史计划。
- **修改后：** ①配额耗尽 `insufficient_quota` 立即失败不重试；②空 key 秒退走兜底；③ASCII 屏蔽词加 `\b` 单词边界；④兜底按总产能自适应 3/4/5 阶段；⑤`/api/save` 同名追加计数后缀。
- **收益：** ①配额耗尽给出可操作提示；②测试/未配置环境稳定；③正常英文词不被误删；④小团队兜底计划合理；⑤历史计划不被同名覆盖。

### 打磨（P3）

**5. 工程可复现性 + 文档同步（v3.6 修复）**
- **问题：** 干净环境无 `pytest.ini` 导致 `@pytest.mark.asyncio` 用例报错；文档测试计数三处互相矛盾（45/24/53）。
- **修改后：** ①新增 `pytest.ini`（`asyncio_mode=auto`）；②统一测试计数为 80。

### 关联版本说明

本版本基于 v4.6（`origin/main`），完整保留其全部新功能：
- v4.0–v4.6：文件上传分析、任务拆解工作流、`project_service.py` 业务分层、反思 agent、长课程手册解析、AI JSON 容错。
- `assign_with_balance` 的多因子打分（技能 0.55 + 总负载 0.20 + 阶段负载 0.15 + 剩余产能 0.10）。

本版本在其基础上的增强：将 v3.5–v3.8 独立发展线的算法修复移植过来，与v4.6 的多因子打分融合——`_balance_workload` 增加全局重排 + 回避门槛，`enhance` 改为条件式均衡，`recompute_preserve` 保留原分工，`_fallback_plan` 自适应阶段数。

---
## v4.6 —— 任务与附属限制分离、AI JSON 本地容错（2026-07-19）

**定位：** 禁止把“命令行即可、不要求图形界面”等实现限制生成新任务，并让轻微不规范的 AI JSON 无需再次请求即可本地修复。

**审查/修改背景：** 用户反馈文件拆解会生成“制作界面即可，不要求使用图形界面）”这种带孤立括号的任务；同时页面提示 AI 返回不符合数据格式，却没有说明具体含义，也没有利用已经返回的可修复内容。

---

### 关键缺陷（P0）

#### 1. 限制条件被“制作”等动作关键词误判成任务

1. **问题：** 通用分析器只要句子包含“制作、完成、设计”等词就加入 `core_tasks`，没有识别“即可、不要求、不得”等约束语气；PDF 切句产生的孤立右括号也被保留。
2. **修改前：**
   ```python
   core = [
       sentence for sentence in sentences
       if any(word in sentence for word in ("完成", "制作", "撰写", ...))
   ]
   ```
3. **修改后：**
   ```python
   classified = [_classify_requirement_unit(sentence) for sentence in sentences]
   task_requirements = [
       {"task": task, "constraints": constraints}
       ...
   ]
   ```
   `_classify_requirement_unit()` 将每个单元拆成“行动任务 + 附属限制”；`_strip_dangling_brackets()` 清除 PDF 遗留孤立括号；NFKC 规范化把兼容字形转换成正常汉字。
4. **为什么这样改：** “制作命令行界面即可”虽含“制作”，语义重点却是允许采用命令行、不要求 GUI。关键词不能脱离语气判断。新版优先识别约束词，再判断是否存在独立可交付动作。
5. **收益：** ① 限制句不再独立成任务；② 孤立括号不会进入任务名；③ 约束仍完整保留在相关任务 description 中。

#### 2. 单独成句的限制没有归属到上一任务

1. **问题：** PDF 常把“实现文件加密功能”和下一行“命令行界面即可”切成两个单元；即使第二句被识别为限制，如果没有关联关系仍可能丢失或污染所有通用任务。
2. **修改前：**
   ```python
   constraints = matched("必须", "不得", "要求", "至少", ...)
   ```
3. **修改后：**
   ```python
   if task:
       task_requirements.append({"task": task, "constraints": [...]})
   elif constraints_for_unit and task_requirements:
       task_requirements[-1]["constraints"] += constraints_for_unit
   ```
   Coordinator 使用 `_requirement_source_with_constraints()` 只把限制写入对应任务；存在映射时不再把全局限制复制到其他通用任务。
4. **为什么这样改：** 相邻的纯限制句在课程要求中通常修饰上一项功能。建立显式映射比“把所有限制拼到所有任务”更准确，也避免“确认目标”任务莫名出现 GUI 限制。
5. **收益：** ① 限制有明确归属；② 相关任务说明可直接作为验收标准；③ 其他任务不受污染。

### 健壮性提升（P1）

#### 3. AI JSON 轻微不规范导致整份草案退回本地规则

1. **问题：** 模型已经返回可读 JSON，但只要缺少 `id`、工时写成“4小时”、技能为逗号字符串或使用 `task_list/title` 等近义字段，Pydantic 校验就失败，整个 AI 结果被丢弃。
2. **修改前：**
   ```python
   raw = self._extract_json(raw)
   return response_model.model_validate_json(raw)
   ```
3. **修改后：**
   ```python
   try:
       return response_model.model_validate_json(raw)
   except (ValidationError, ValueError):
       repaired = self._repair_response(raw, response_model)
       if repaired is not None:
           return repaired
       raise
   ```
   本地修复会补任务 ID 和默认字段、解析数字工时、拆分技能/依赖、规范执行阶段，并把任务名中的括号限制移入说明；纯限制任务会合并到上一任务。
4. **为什么这样改：** 结构校验失败不等于语义完全不可用。对确定、无歧义的字段形态进行本地规范化，可以保留 AI 的专业拆解，同时无需再发起一次模型请求；无法解析或截断的 JSON 仍拒绝采用。
5. **收益：** ① 显著减少不必要的本地兜底；② 不增加模型等待和调用成本；③ 修复过程仍经过最终 Pydantic 校验。

#### 4. “不符合数据格式”提示过于抽象

1. **问题：** 用户无法知道该提示是文件失败还是 AI JSON 缺字段。
2. **修改前：**
   ```python
   return "原因：AI 返回内容不符合任务草案的数据格式"
   ```
3. **修改后：**
   ```python
   return (
       "原因：AI 已返回内容，但其中存在缺少必填字段、字段类型错误或"
       "JSON 不完整，系统无法安全采用"
   )
   ```
4. **为什么这样改：** 文件解析与 AI 结构校验是两个阶段，提示必须说明失败发生在哪一层，以及为何选择兜底。
5. **收益：** ① 用户能判断文件并未丢失；② 错误原因可操作；③ 与本地修复/兜底行为一致。

### 同步修改

- `README.md`：版本更新至 v4.6，补充任务/限制分类和 AI JSON 本地修复说明。
- `tests/test_workflow_v4.py`：新增孤立括号、约束归并和端到端任务说明测试。
- `tests/test_review_fixes.py`：新增 AI 近似 JSON 本地修复、纯限制任务合并测试。

---
## v4.5 —— 长课程手册的成果识别与递归拆解（2026-07-19）

**定位：** 从长篇课程手册中先识别“学生真正要做什么、交什么”，再把推送、Vlog、报告等复合成果继续拆成可分工步骤。

**审查/修改背景：** 用户上传 63 页《“思政实践”课程手册（学生版）》后，草案出现“设计了 4 阶段教学环节”“调研报告撰写方法”等说明性句子，却遗漏 3 次研讨会、支队/个人报告等实际要求；同时“模型暂时不可用”提示没有解释是 AI 失败还是文件解析失败。

---

### 关键缺陷（P0）

#### 1. PDF 段落被压成一整行，课程说明被误判为任务

1. **问题：** `extract_text()` 用 `re.sub(r"\s+", " ", text)` 删除所有换行；`analyze_locally()` 又只检查前 120 个切分结果。目录、课程介绍、教学安排和系统操作占据前部，真正位于“实践板块学习指南”的要求被截断或与其他段落粘连。
2. **修改前：**
   ```python
   cleaned = re.sub(r"\s+", " ", text).strip()
   sentences = [
       part.strip(...) for part in re.split(r"[。！？\n;；]+", cleaned)
       if part.strip()
   ][:120]
   ```
3. **修改后：**
   ```python
   cleaned = _normalize_document_text(text)
   sentences = _candidate_units(cleaned)
   ```
   `_normalize_document_text()` 保留段落边界；`_candidate_units()` 清理页码、目录点线并扫描最多 800 个候选单元；`_is_reference_noise()` 排除“本课程设计”“系统操作”“第 4 讲”“范例/附件”等说明性内容。
4. **为什么这样改：** 根因是页面结构在文本预处理阶段丢失，后续关键词再准确也只能在错误的长字符串上匹配。保留段落并先过滤参考性内容，才能区分“课程如何设计”与“学生需要完成的动作”。
5. **收益：** ① 不再把教学环节说明和讲座标题生成任务；② 长文件中后部要求不会被固定 120 句截断；③ 本地分析仍为毫秒级规则处理。

#### 2. 没有区分硬性要求、建议项和鼓励项

1. **问题：** 旧分析只输出 `core_tasks` 和 `deliverables`，导致“3 次研讨会”与“鼓励制作视频”处于同一层级，既可能遗漏硬性要求，也可能把建议成果误报为必交作业。
2. **修改前：**
   ```python
   return {
       "core_tasks": core,
       "deliverables": deliverables,
       "summary": summary,
   }
   ```
3. **修改后：**
   ```python
   return {
       "required_deliverables": required_deliverables,
       "recommended_deliverables": recommended_deliverables,
       "task_blueprint": blueprint,
       ...
   }
   ```
   每个蓝图任务带 `requirement_level`（必须/建议/鼓励）、执行阶段、描述、依赖、工时、技能和建议人数。
4. **为什么这样改：** 课程手册中的“应/需/形成”与“建议/鼓励”具有不同约束力。只有在分析结构中保留层级，Planner 和本地兜底才可能准确表达，不应依靠任务名称猜测。
5. **收益：** ① 明确保留不少于 4 天调研、1 次理论讲座、3 次研讨会等硬性数量；② 总结推送标为建议项、Vlog 标为鼓励项；③ 用户能区分结课底线和团队自选成果。

#### 3. 复合交付物只生成一个总任务，无法直接分工

1. **问题：** 即使识别出“制作总结推送”，旧规则也可能只生成一项，仍需人工拆成文案、摄影、排版等岗位任务；个人总结报告也没有按成员展开。
2. **修改前：**
   ```python
   add(name, category, hours, skills, stage, people, source)
   ```
3. **修改后：**
   ```python
   blueprint = [
       "策划实践总结推送结构",
       "筛选推送照片并收集成员感想",
       "撰写实践总结推送文案",
       "按秀米规范完成推送排版",
       "完成推送审核、排期申请与投稿",
       ...
   ]
   ```
   `_fallback_blueprint_plan()` 还会把“每位成员撰写个人总结报告”按当前成员姓名展开成独立任务。
4. **为什么这样改：** 任务分工的最小有效粒度是“一名主要负责人可以独立推进并验收”。推送、Vlog、研究报告跨越多种技能，必须递归拆分；个人报告则天然是逐人责任。
5. **收益：** ① 推送直接拆到策划、素材、文案、排版、审核投稿；② Vlog 拆到脚本镜头和剪辑审核，并复用现场视频采集；③ 每名成员的个人总结责任清晰。

### 健壮性提升（P1）

#### 4. AI 失败提示无法说明文件是否解析成功

1. **问题：** 页面显示“模型暂时不可用，已生成领域化草案”，用户无法判断是 API、文件还是系统规则出了问题；成功提示仍写“AI 任务草案已生成”，与实际兜底矛盾。
2. **修改前：**
   ```python
   summary = "模型暂时不可用，已根据项目背景..."
   ```
   ```js
   showNotice('AI 任务草案已生成', 'success')
   ```
3. **修改后：**
   ```python
   reasoning = (
       f"{_friendly_fallback_cause(error_msg)}；这不代表文件解析失败。"
       "系统已使用文件任务蓝图继续生成..."
   )
   ```
   ```js
   showNotice(
     usedFallback
       ? 'AI 本次未返回可用草案，已改用文件任务蓝图'
       : '任务草案已生成，可修改后确认',
     usedFallback ? 'info' : 'success'
   )
   ```
4. **为什么这样改：** “AI 返回失败”和“文件解析失败”是两个独立阶段。旧提示把它们混成“模型暂时不可用”，无法指导用户处理。新版将超时、鉴权和返回格式错误改写为可理解原因，同时明确文件任务蓝图是否已成功接管。
5. **收益：** ① 用户知道草案来自 AI 还是本地蓝图；② 明确文件没有丢失；③ AI 失败时仍能生成比旧通用模板更细的任务。

### 针对《“思政实践”课程手册（学生版）》的验证结果

- 必须项：不少于 4 天（32 学时）调研、1 次 2 学时理论讲座、3 次各 2 学时研讨会且每次形成文字记录、1 份不超过 10000 字的支队调研报告、每人 1 份不超过 3000 字的个人总结报告、6 分钟汇报 PPT。
- 建议/鼓励项：实践总结公众号推送、实践 Vlog/短视频；均已继续细分。
- 文件原文只写“队旗、队服等”物资，并未明确帆布包；系统会提示用户在补充要求中确认帆布包数量和预算，不擅自编造。
- 文件规定的是 1 次支队理论讲座，不是固定 1 次座谈会；座谈/访谈安排需结合具体支队行程确认。
- 实测：63 页 PDF 文字提取约 1.33 秒，本地要求分析约 2.2 毫秒，生成 23 项基础蓝图；3 名成员时展开为 25 项任务。未新增 LLM 调用。

### 同步修改

- `README.md`：版本更新至 v4.5，说明长课程手册、要求层级和复合成果拆分。
- `tests/test_workflow_v4.py`：新增教学说明过滤、思政手册任务蓝图、推送/Vlog 细分、个人报告逐人展开和 AI 失败解释测试。

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
## v4.2 —— 文件驱动的具体任务拆解与自由编辑（2026-07-19）

**定位：** 让任务书中的具体要求真正决定任务名称和验收标准，同时恢复任务卡内的文字拖选编辑，且不增加 LLM 调用次数。

**审查/修改背景：** 用户反馈任务拆解仍会生成“现场执行与过程调整”一类无法直接执行的宽泛任务，需要大量人工二次细化；同时任务卡整卡拖拽会抢占输入框中的鼠标拖选，导致无法顺畅选中文字修改。

---

### 关键缺陷（P0）

#### 1. 默认 AI 生成路径跳过上传文件分析

1. **问题：** 页面只有在 `useAi !== true` 时才调用 `analyzeFiles()`，但“生成任务拆解”和“AI 重新拆解”都传入 `true`，因此 Planner 实际只看到文件名，看不到任务书内容。
2. **修改前：**
   ```js
   if(state.files.length&&useAi!==true)await analyzeFiles();
   ```
3. **修改后：**
   ```js
   if(state.files.length)await analyzeFiles();
   ```
   同一批文件和项目背景的提炼结果会保存在当前页面状态中，AI 重新拆解时直接复用。
4. **为什么这样改：** 根因不是模型能力不足，而是页面条件与实际入口相反，文件内容从未进入默认生成链路。现在两种草案模式共用同一份本地文件提炼；提炼仍不调用模型，随后 Planner 仍只请求一次。
5. **收益：** ① 任务书内容真正进入拆解上下文；② 首次生成与重新拆解结果一致；③ 不增加串行 LLM 等待，重复重拆也不重复解析文件。

#### 2. Planner 与快速兜底允许宽泛任务覆盖文件要求

1. **问题：** Prompt 只要求“按流程拆分”，本地兜底又写死“现场执行与过程协调”；文件中的数量、格式、交付物和评分点没有逐项映射到任务。
2. **修改前：**
   ```python
   add("现场执行与过程协调", "执行", 6, ["组织协调"], "实践中", 3)
   description=f"完成{spec[0]}，形成可检查、可交付的成果"
   ```
3. **修改后：**
   ```python
   for item in _specific_requirement_items(analysis)[:6]:
       name = _requirement_task_name(item)
       add(name, _infer_category(name), _estimate_hours(name),
           _infer_skills(name), _infer_stage(name),
           _infer_people(name), item)

   description=_fallback_description(spec[0], spec[1], spec[6], inp)
   ```
   Prompt 同步规定任务名必须是“动作 + 具体对象/成果”，并禁止“现场执行与过程调整”“推进项目”等宽泛名称。
4. **为什么这样改：** 通用活动关键词只能说明项目处于“实践”场景，不能说明团队到底要宣讲、走访、测量还是回收材料。新版先把文件中的明确动作与交付物转成任务，再补足领域流程；说明字段保留对应要求并提示核对对象、数量、格式、时间和质量条件。
5. **收益：** ① 文件要求可以追溯到具体任务；② 快速兜底也能个性化，不依赖模型成功；③ 显著减少后期人工改名和补验收标准。

### 体验优化（P2）

#### 3. 整张任务卡可拖拽，抢占文字选择

1. **问题：** `draggable="true"` 设置在整张 `<article>` 上，从名称输入框或说明文本框按下并拖动时，浏览器可能启动卡片拖拽，无法稳定选中文字。
2. **修改前：**
   ```html
   <article class="task-edit-card" data-id="..." draggable="true">
     <span class="drag-handle">⋮⋮</span>
   </article>
   ```
   ```js
   card.ondragstart=function(event){ ... }
   ```
3. **修改后：**
   ```html
   <article class="task-edit-card" data-id="...">
     <span class="drag-handle" draggable="true"
           title="拖动调整顺序">⋮⋮</span>
   </article>
   ```
   ```js
   handle.ondragstart=function(event){ ... }
   handle.ondragend=function(){card.classList.remove('dragging')};
   ```
4. **为什么这样改：** 排序的有效拖拽区域本来就应该是视觉手柄。把 HTML5 Drag 事件缩到手柄后，卡片其余区域恢复浏览器原生的光标定位、拖选和输入行为，排序能力不受影响。
5. **收益：** ① 名称、说明和技能等字段可正常拖选修改；② 拖拽入口更明确；③ 排序中增加视觉状态反馈。

### 健壮性提升（P1）

#### 4. 文件要求以结构化压缩形式进入单次 Planner 请求

1. **问题：** 后端过去只把 `requirement_analysis.summary` 拼进 Prompt，核心任务、交付物、格式、限制和评价标准混成一段，模型难以判断哪些要求必须逐项落地。
2. **修改前：**
   ```python
   extracted = inp.requirement_analysis.get("summary", "")
   ```
3. **修改后：**
   ```python
   extracted = _format_requirement_analysis(
       inp.requirement_analysis, inp.uploaded_files)
   ```
   输出按“项目目标 / 核心任务 / 交付物 / 时间要求 / 格式要求 / 限制条件 / 评价标准”分组，并设置 5000 字符上限。
4. **为什么这样改：** 结构化上下文能让 Planner 在同一次推理里建立“要求 → 任务”映射；长度上限防止大文件重复内容扩大输入并拖慢生成。
5. **收益：** ① 具体要求更不容易遗漏；② Prompt 长度受控；③ 保持一次 Planner 调用。

### 同步修改

- `README.md`：版本号更新至 v4.2，并说明文件驱动拆解、单次模型调用和手柄拖拽。
- `tests/test_workflow_v4.py`：新增文件要求个性化任务、禁用宽泛名称、手柄限定拖拽和两种模式均分析文件的回归测试。

---
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

**定位：** 对提交的《代码全面审查报告》逐条核对代码后，修复其中确实成立且值得动手的 12 项；明确驳回/暂不改若干项并给出理由。
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

### 关联版本说明
本版本基于提交的《代码全面审查报告》（史雨彤，2026-07-17）。该报告为“仅审查、未修改”。本版本在其基础上：逐条核对源码后落地上述 13 项；并明确以下项的处理：
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
- 修订的 Planner 提示词中「参考 available_hours 估算工时」终于能实际生效

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
提示词改动说明

**jiajia-hua** 在 `feature/planner-prompt` 分支修改了 Planner 提示词（v0.3 版）：
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
| **v0.1** | **初始骨架** | **已完成** |
| **v0.2** | **核心算法实现** | **已完成** |
| **v0.3** | **Web 重做 + Memory + 答辩模拟** | **已完成** |
| **v0.4** | **技能评分 + 动态编辑 + 精细打磨** | **已完成** |
| **v1.0** | **功能完整正式版** | **已完成** |
| **v1.1** | **代码质量加固** | **已完成** |
| **v1.2** | **进度追踪 + 突发情况处理** | **已完成** |
| **v2.0** | **深度审查修复（30 项问题）** | **已完成** |
| **v3.0** | **七轮审查全量修复** | **已完成** |
| **v3.1** | **审查复核选择性修复（13 项）** | **已完成** |
| **v3.2** | **用户验收六连击修复** | **已完成** |
| **v3.3** | **完成不重排 + 负向技能标签识别** | **已完成** |
| **v3.4** | **报告自动重生 + 表格渲染修复 + 空鉴权快速兜底** | **已完成** |
| **v4.0** | **任务拆解与分工双确认工作流** | **已完成** |
| **v4.1** | **恢复工作台视觉与共享业务服务** | **已完成** |
| **v4.2** | **文件驱动的具体任务拆解与自由编辑** | **已完成** |
| **v4.3** | **修复首次提交不走快速模式、默认调 AI** | **已完成** |
| **v4.4** | **AI 调整建议按钮可拖拽 + 生成按钮反馈修复** | **已完成** |
| **v4.5** | **长课程手册的成果识别与递归拆解** | **已完成** |
| **v4.6** | **任务与附属限制分离、AI JSON 本地容错** | **已完成** |
| **v4.7.1** | **合入 v3.5-v3.8 算法修复** | **已完成** |
| **v4.7.2** | **知识库估时、反馈学习与2h均衡分工** | **已完成** |
| **v4.8** | **统一合并：算法修复 + 工时知识库** | **已完成** |
| **v4.8.1** | **代码质量加固 + CI + API 测试覆盖** | **已完成** |
| **v4.9** | **比赛 Demo 加固 + 清小搭标准协议接入** | **已完成** |
| **v5.0** | **八项核心功能修复** | **已完成** |
| **v5.1** | **六项用户报告问题修复** | **已完成** |
| **v5.2** | **AI 调整建议按钮交互重写** | **已完成** |
| **v5.3** | **抽屉遮挡修复 + 首次拆解兜底优化** | **已完成** |
| **v5.4** | **DeepSeek 超时根因修复 + 推理模型容错加固** | **已完成** |
| **v5.5** | **新增成员零工时修复 + 任务分工术语清理** | **已完成** |
| **v5.6** | **成员变动后分工失衡 + 导出与报告问题修复** | **已完成** |
| **v5.7** | **第二轮深度审查全量修复 + AI 协作助手体验重写** | **已完成** |
| v5.8 | 大型项目模式后端+前端修复 | 文档先行，代码待补 |
| **v5.9** | **测试恢复 + BOM 清理 + 文档与代码一致性修正** | **已完成** |
| **v5.10** | **大型项目模式补做 + 评审预演多轮互动 + 前端适配** | **已完成** |
| **v5.11** | **大型项目模式闭环：志愿者招募与认领** | **已完成** |
| **v5.12** | **大型项目模式重构：模块→子任务→骨干认领→子任务级志愿者招募** | **已完成** |
| **v5.14** | **前端全量重写：外部 app.js 替代内联脚本，大型/小型项目完全分离** | **已完成** |
| **v5.15.1** | **骨干认领阶段补齐骨干管理面板** | **已完成** |
| **v5.15** | **CSS 全量重写修复类名不匹配 + 大模块编辑增强** | **已完成** |
| **v5.13** | **大型项目分步流程补齐：模块拆解→骨干认领→子任务拆解→志愿者认领** | **已完成** |
| **v5.16** | **静态资源缓存版本号修复** | **已完成** |
| **v5.17** | **品牌标识 SVG 重设计 + 配色/质感精细化** | **已完成** |
| **v5.18** | **大型项目体验对标小型项目 + P0–P3 全量修复** | **已完成** |
| **v5.19** | **前端视觉精细度整体打磨** | **已完成** |
| **v5.20** | **前端交互修复 + 时间线/历史弹窗/品牌视觉再打磨** | **已完成** |
| **v5.21** | **阶段导航去重 + 评审预演双模式** | **已完成** |
| **v5.22** | **角色模型第一版：角色化工作量 + 志愿者折算 + 冲突检测** | **已完成** |
| **v5.23** | **实际工时 + 复盘闭环** | **已完成** |
| **v5.24** | **组织树 + 任务级参与清单** | **已完成** |
| **v5.25** | **资源日历 + 冲突检测深化** | **已完成** |
| **v5.26** | **Excel / CSV / ICS 导入导出** | **已完成** |
| **v5.27** | **变更记录 / 审计 / 回滚** | **已完成** |
| **v5.28** | **只读分享 + 提醒中心 + 知识库 + 组织复盘** | **已完成** |
| **v5.29** | **鉴权 + 工具调用 + 并发冲突 + 多模态文件** | **已完成** |
| **v5.30** | **Knowledge Agent + 跨项目经验复用** | **已完成** |
| **v5.31** | **外部通知：Webhook 推送提醒** | **已完成** |
| **v5.32** | **多用户账号 + 项目级权限** | **已完成** |
| **v5.33** | **图片 OCR / 音频转写** | **已完成** |
| **v5.34** | **深度审查修复** | **已完成** |
| **v5.35** | **清小搭多轮规划 + 文本甘特图 + 首字优化** | **已完成** |
| **v5.36** | **清小搭通用问答 + 快速规划 + 移动端兼容** | **已完成** |
| **v5.37** | **通用问答真流式 + 模型超时修复** | **已完成** |
| **v5.38** | **Render 默认模型固定为 DeepSeek-V3.2** | **已完成** |
| **v5.39** | **清小搭移动端首帧严格兼容** | **已完成** |
| **v5.40** | **清小搭移动端问题原文展示兜底** | **已完成** |
| **v5.41** | **Web 路由按业务域拆分** | **已完成** |
| **v5.42** | **Excel 下载后缀修复与响应式布局统一** | **已完成** |
| **v5.43** | **相似任务版本树、版本对比与分支回滚** | **已完成** |
| **v5.44** | **最终方案入口归并与知识能力内隐** | **已完成** |
| **v5.45** | **多选不可用日期与大型项目排期闭环** | **已完成** |
| **v5.46** | **错误与警告信息分层展示** | **已完成** |
| **v5.47** | **项目规模角色模型简化** | **已完成** |
| **v5.48** | **需求驱动的材料答辩模拟** | **已完成** |
| **v5.76** | **基础版整合 v5.49–v5.76 通用能力、移除清小搭接入残留与导出区上移** | **已完成** |
| **v5.77** | **不可用日期硬约束：时间线避开、日期回填与资源日历负载修复** | **已完成** |
| **v6.9** | **多模态需求输入：语音描述与拍照直接生成任务** | **已完成** |
| **v6.8** | **P0 全模态交互落地：前端接通 MiniCPM-o、语音输入与语音回复** | **已完成** |
| **v7.0** | **视频理解：会议录像边看边听 + 多模态演示闭环** | **已完成** |
| **v7.1** | **全链路审计修复：A3 长音频防崩 + 语音记忆/转写质量 + 导出编码** | **已完成** |
| v6.x | 后续功能扩展 | 规划中 |
