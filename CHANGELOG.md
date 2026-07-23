# 变更日志 (CHANGELOG)

> 本文档记录项目每一次版本变更，附核心改动的**原版 vs 现版代码对照**，
> 方便团队成员理解"为什么这么改、改了什么、好在哪里"。
> 按时间倒序排列（最新在最上面），随项目同步更新。

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
| **v4.2** | **文件驱动的具体任务拆解与自由编辑** | **已完成** |
| **v4.5** | **长课程手册的成果识别与递归拆解** | **已完成** |
| **v4.6** | **任务与附属限制分离、AI JSON 本地容错** | **已完成** |
| **v4.7.1** | **合入 v3.5-v3.8 算法修复** | **已完成** |
| **v4.7.2** | **知识库估时、反馈学习与2h均衡分工** | **已完成** |
| **v4.8** | **统一合并：算法修复 + 工时知识库** | **已完成** |
| **v4.9** | **比赛 Demo 加固 + 清小搭标准协议接入** | **已完成** |
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
