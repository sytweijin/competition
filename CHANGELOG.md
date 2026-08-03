# 变更日志 (CHANGELOG)

> 本文档记录项目每一次版本变更，附核心改动的**原版 vs 现版代码对照**，
> 方便团队成员理解"为什么这么改、改了什么、好在哪里"。
> 按时间倒序排列（最新在最上面），随项目同步更新。

---
---
---
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
| v6.x | 正式发布与功能扩展 | 规划中 |
