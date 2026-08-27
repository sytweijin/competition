<!-- ppt-master-schema: design-spec/v1 -->
# 协作分工智能体 · 昇腾全模态版 - Design Spec

## I. Project Information

| Item | Value |
| --- | --- |
| Project Name | 协作分工智能体 · 昇腾全模态版（华为昇腾创新应用赛道参赛 PPT） |
| Canvas Format | ppt169（1280×720） |
| Page Count | 29 |
| Primary Language | zh-CN |
| Target Audience | 华为昇腾创新应用赛道评审（评委）。他们围绕 MiniCPM-o 4.5 全模态模型与昇腾 NPU，重点考察创新性、实用性、场景价值、工程完整度、可运行可展示、统一昇腾环境复现；对项目本身没有先入了解，需要通过材料快速判断参赛价值。 |
| Communication Intent | 先说明项目是什么、解决什么问题（功能与价值），再解释系统如何运转（架构与机制），进而用证据展示创新点、实际落地应用与工程验证（测试、昇腾 A3 实测、复现），最后如实交代边界与下一步。以 inform 与 explain 为主线，辅以 persuade：让评委信服这是一个完整、真实、可落地、可复现的全模态协作闭环，而非单点 Demo。 |
| Desired Audience Outcome | 评委独立阅读后能准确复述：项目的核心功能链路、核心创新点、实际应用场景，以及工程完整度的证据（379 项自动化测试通过、昇腾 A3 实测指标、统一昇腾环境复现步骤），并据此作出正向评审判断。 |
| Core Message / Ask / Action | 把 MiniCPM-o 4.5 从"聊天机器人"变成真实协作流程的入口：拍照立项、说话排期、确认分工、语音/照片汇报、群通知的完整闭环；LLM 负责创造性拆解，确定性算法负责正确性保证，任何环节失败都有兜底；双后端一键切换，在昇腾 A3 本地或 ModelBest 云端均可运行，统一昇腾环境可复现。 |
| Delivery Context | 主要为评委直接阅读的提交材料（读者驱动、无主讲人），页面必须自洽、信息完整、可独立理解；次要为评审答辩或路演时辅助讲解。 |
| Artifact Afterlife | 作为比赛提交材料存档，评委可能反复查阅，并与项目说明、演示视频、复现文档对照核验；也可能用于后续路演或成果展示。 |
| Reading Mode | text（文字型：评委直接阅读，页面自洽、信息完整） |
| Content Strategy | 严格贴近原材料，但是可以自己组织语言（确认值：以项目 README 与 docs/ 下提交文档为唯一事实来源，按评审导向重组结构、自行组织表述，不新增事实）。2026-08-27 用户追加修订：整体叙事改为完整句、偏讲故事，前几页与创新点按"现状问题 → 机制 → 为什么前所未有 → 证据"重写，避免短句小短语；配色改为封面蓝 #375A86 体系。 |
| Design Style | custom 模式「评审导向说明书」+ swiss-minimal 视觉风格（封面蓝 #375A86 体系） |
| Formula Policy | text-only |
| AI Image Acquisition Path | 不适用（不使用 AI/网络图片） |
| Generation Mode | continuous |
| Spec Refinement | enabled（设计规格初稿需经用户审阅批准后才锁定） |
| Speaker Notes | enabled — 最终 Stage-2 主动策略 proactive_speaker_notes=true（无更早显式指令） |
| Custom Animations | disabled — 最终 Stage-2 主动策略 proactive_custom_animations=false |
| Narration Audio | disabled — 最终 Stage-2 主动策略 proactive_narration_audio=false |
| Created Date | 2026-08-27 |

## II. Canvas Specification

| Property | Value |
| --- | --- |
| Format | ppt169 |
| Dimensions | 1280 × 720 |
| viewBox | `0 0 1280 720` |
| Margins | 40px 安全边距（四周） |
| Content Area | 1200 × 640（40–1240 横向，40–680 纵向） |

## III. Visual Theme

### Theme Style

- **Mode**: custom — 评审导向说明书
- **Mode References**: briefing, pyramid
- **Mode Behavior**: 以说明书式完整覆盖为骨架（主题式标题、可扫读、并列均衡），但把评委最关心的三个要点——核心功能、创新点、落地应用——在开篇"项目一览"页以结论前置的方式集中呈现，随后按"背景问题 → 系统架构 → 核心功能 → 创新点 → 应用与工程验证 → 边界与展望"完整展开；标题以主题为主，关键证据页辅以结论式副标题。
- **Visual style**: swiss-minimal
- **Theme**: 冷静深海蓝 + 克制金色点缀的瑞士极简网格；白底为主，几何色块定位页面区域，无渐变、无阴影、无装饰性元素。
- **Tone**: 严谨、克制、可信、正式；面向比赛评委的工程展示书语气。

### Color Scheme

| Role | HEX | Purpose |
| --- | --- | --- |
| Background | #FFFFFF | 页面底色，绝大多数内容承载面 |
| Secondary background | #F0F6FC | 卡片、图表区、代码/表格底、章节色带浅层 |
| Primary | #375A86 | 主标题、章节编号、强调文字、关键图形与连接线（用户确认改为封面蓝） |
| Accent | #C9A227 | 关键路径高亮、创新点标记、少量点缀（金色只作标点式强调） |
| Secondary accent | #7B9DC4 | 次级强调、辅助说明图形、图标统一色 |
| Body text | #1E293B | 正文、表格文字、注释文字 |
| Grid | #DCE6F0 | 卡片细边框、表格行线、分隔线等结构线 |
| Muted | #64748B | 页脚、页码、脚注、次要注释文字 |

## IV. Typography System

### Font Plan

| Role | Character (Reference) | Primary | English if non-English | Fallback tail |
| --- | --- | --- | --- | --- |
| Title | 黑体、粗、克制严谨 | Microsoft YaHei | Arial | sans-serif |
| Body | 黑体、常规、易读 | Microsoft YaHei | Arial | sans-serif |

- **Typography upgrade (Reference)**: 无（目标评审环境为通用 PowerPoint，微软雅黑可直接使用；不嵌入字体）
- **Title stack**: Microsoft YaHei / Arial / sans-serif
- **Body stack**: Microsoft YaHei / Arial / sans-serif

### Font Size Hierarchy

| Purpose | Anchor Size (px) |
| --- | ---: |
| Body | 20 |
| Title | 36 |
| Subtitle | 28 |
| Lead | 24 |
| Annotation | 16 |
| Footnote / page number | 12 |

## V. Layout Principles

### Page Structure

- **Header area**: 顶部统一页眉区：左上方章节编号（小型标签，如 01/02…）+ 主标题（左对齐，粗体黑）；标题下方一条 2px 主色细规则线或浅色横线，形成稳定的页眉基线；章节页标题更大、居中或左置大号编号。
- **Content area**: 1200×640 内容区内的模块化网格：内容按 12 列网格排布，卡片/图形/表格严格对齐；卡片以细边框或浅色底区分，不用阴影；图表区留出统一外边距。
- **Footer area**: 底部统一页脚：左侧项目短名「协作分工智能体 · 昇腾全模态版」，右侧页码（XX / 29）与版本标识（v7.1），使用 12px 灰色文字，页脚与内容区之间留 24px。

### Spacing Specification

| Element | Current Project |
| --- | --- |
| Safe margin | 40px |
| Content block gap | 18px |
| Icon-text gap | 8px |

## VI. Icon Usage Specification

- **Primary bundled library**: tabler-outline
- **Stroke Width**: 2

| Icon Path | Suitable Scenarios |
| --- | --- |
| tabler-outline/message-circle | 对话、AI 建议抽屉 |
| tabler-outline/message | 文本对话、消息 |
| tabler-outline/microphone | 语音输入、语音对话、语音汇报 |
| tabler-outline/camera | 拍照立项、拍照交付 |
| tabler-outline/video | 视频理解、答辩录像、会议录像 |
| tabler-outline/volume | TTS 播报、语音回复 |
| tabler-outline/headphones | 语音汇报、旁听 |
| tabler-outline/users | 成员、团队 |
| tabler-outline/users-group | 项目组、小组 |
| tabler-outline/school | 课程设计、校园场景 |
| tabler-outline/building-community | 社团、组织活动 |
| tabler-outline/trophy | 比赛团队、赛事冲刺 |
| tabler-outline/clipboard-list | 任务拆解、需求清单 |
| tabler-outline/clipboard-check | 负责人确认、验收 |
| tabler-outline/calendar | 排期、截止日 |
| tabler-outline/clock | 工时估算、可用工时 |
| tabler-outline/timeline | CPM 排期、甘特示意 |
| tabler-outline/target | 技能匹配、目标 |
| tabler-outline/adjustments | 负载均衡、调整 |
| tabler-outline/bell | 提醒、通知 |
| tabler-outline/send | 群通知推送 |
| tabler-outline/send-2 | 通知触达 |
| tabler-outline/brand-wechat | 企业微信通知 |
| tabler-outline/calendar-stats | 今日播报 |
| tabler-outline/file-text | 报告生成 |
| tabler-outline/file-export | 多格式导出 |
| tabler-outline/file-spreadsheet | Excel 导出 |
| tabler-outline/file-type-pdf | PDF 导出 |
| tabler-outline/database | 知识库、工时案例库 |
| tabler-outline/shield-check | 合规、安全、鉴权 |
| tabler-outline/cpu | 昇腾 A3 算力、本地推理 |
| tabler-outline/cloud | 云端 API |
| tabler-outline/server | 后端服务 |
| tabler-outline/api | 接口 |
| tabler-outline/network | 系统架构 |
| tabler-outline/stack | 分层架构 |
| tabler-outline/refresh | 即时重算 |
| tabler-outline/refresh-alert | 故障兜底、重试 |
| tabler-outline/edit | 可编辑草案 |
| tabler-outline/arrows-shuffle | 拖拽分工、调整分工 |
| tabler-outline/git-branch | 版本管理、版本树 |
| tabler-outline/history | 回滚、历史 |
| tabler-outline/plus | 增删改、新增 |
| tabler-outline/check | 确认完成、通过 |
| tabler-outline/link | 免登录汇报链接、只读分享 |
| tabler-outline/key | token 鉴权 |
| tabler-outline/lock | 登录、会话、审计 |
| tabler-outline/alert-circle | 风险提示、边界提示 |
| tabler-outline/award | 创新点、成果 |
| tabler-outline/rocket | 落地、启动、展望 |
| tabler-outline/bulb | AI 建议、洞察 |
| tabler-outline/sparkles | 全模态能力 |
| tabler-outline/photo | 照片交付、图片理解 |
| tabler-outline/upload | 上传文件、素材 |
| tabler-outline/player-play | 演示、运行效果 |
| tabler-outline/terminal | 部署、命令行 |
| tabler-outline/activity | 监控、健康检查 |
| tabler-outline/lifebuoy | 兜底保障 |
| tabler-outline/settings | 工程配置 |

## VIII. Image Resource List

| Filename | Dimensions | Ratio | Purpose | Type | Layout pattern | Crop Policy | Acquire Via | Status | Reference | text_policy | page_role |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

（本表无资源行：架构图、流程图、机制示意图全部以原生矢量绘制；"运行效果展示"页的截图占位框为原生 SVG 图形，由作者在导出后在 PowerPoint 中直接粘贴真实运行截图，不进入图片资源管线。）

## IX. Content Outline

### Part 1: 项目概览

#### Slide 01 - 封面

- **Audience move**: 评委在打开文件的第一眼就知道这是"什么项目、参加什么赛道、核心定位是什么" → 产生继续阅读的意愿。
- **Layout**: 瑞士极简封面：左侧全高深海蓝竖色带（约 1/4 版宽）承载章节标识与赛道信息；右侧白底大标题区。大标题 36px 以上黑体、副标题 20px、底部一行参赛信息（赛道 / 版本 / 日期）。
- **Title**: 协作分工智能体 · 昇腾全模态版
- **Core message**: 基于 MiniCPM-o 4.5 的团队协作全模态闭环——把多模态大模型变成真实协作流程的入口。
- **Content**:
  - 主标题：协作分工智能体 · 昇腾全模态版
  - 封面钩子（副标题）：别人给你一张静态分工表；我们给你一张可编辑的活协作图——拍照立项、说话排期、确认分工、语音/照片汇报、群通知，全链路由 MiniCPM-o 4.5 全模态能力驱动。
  - 标识信息：华为昇腾创新应用赛道 · 创新应用赛道（OpenBMB / 面壁智能 × 华为昇腾）；基于 MiniCPM-o 4.5 全模态模型
  - 版本信息：v7.1 · 2026-08

#### Slide 02 - 目录

- **Audience move**: 评委快速了解全文结构与导航，明确 29 页的阅读路径 → 可按需跳读。
- **Layout**: 两栏编号目录：左栏 01–03 章，右栏 04–06 章；每章条目附一句内容说明；顶部目录标题与细规则线。
- **Title**: 目录
- **Core message**: 本文档按"概览 → 架构 → 功能 → 创新 → 应用与验证 → 边界与展望"组织。
- **Content**:
  - 01 项目概览：定位、背景与痛点、解决思路（P03–P05）
  - 02 系统架构与核心机制：总体架构、计划引擎、全模态链路、协作闭环（P06–P10）
  - 03 核心功能详解：功能总览、计划引擎、CPM 排期、动态协作、汇报触达、全模态矩阵（P11–P17）
  - 04 创新点：全模态进流程、确定性保证、完整闭环与兜底、昇腾双后端（P18–P22）
  - 05 应用落地与工程验证：应用场景、运行效果、工程验证、昇腾复现、合规与边界（P23–P28）
  - 06 总结与展望（P29）

#### Slide 03 - 项目一览（评审要点前置）

- **Audience move**: 评委在 30 秒内抓住"项目是什么、创新在哪、能否落地"三个评审要点 → 建立继续阅读的框架。
- **Layout**: 顶部一句话定位横幅（主色底、白字）；下方三列等宽卡片（功能 / 创新 / 应用），每列带图标与要点列表；底部一条设计原则说明。
- **Title**: 项目一览：功能 · 创新 · 应用
- **Core message**: 输入「项目要求 + 团队成员 + 截止日」，系统自动完成 任务拆解 → CPM 关键路径排期 → 技能匹配分工 → 报告生成，并支持随时调整、即时重算；全链路由 MiniCPM-o 4.5 全模态能力驱动。
- **Content**:
  - 定位横幅：把项目要求、成员能力、截止日期，转化为可编辑的任务拆解、分工、排期与报告——以 MiniCPM-o 4.5 全模态能力作为真实协作流程的入口。
  - 功能卡（图标 clipboard-list）：全模态输入（文本 / 语音 / 图片 / 视频 / TTS）；计划引擎（拆解 · 排期 · 分工 · 报告）；动态协作（编辑 · 拖拽 · 即时重算）；汇报触达（免登录汇报页 · 群通知 · 今日播报）。
  - 创新卡（图标 award）：多模态从"聊天"进入真实协作流程；LLM 负责创造 × 确定性算法负责正确；任何环节失败都有兜底，演示不依赖模型持续在线；完整闭环而非单点 Demo。
  - 应用卡（图标 rocket）：面向学生团队 / 课程设计 / 社团 / 中小型企业项目组；支持小型（扁平协作）与大型（模块 → 骨干认领 → 志愿者招募）两种项目模式；免登录成员汇报页 + 企业微信 / 飞书 / 钉钉群通知；多格式导出与只读分享；昇腾 A3 本地或云端双后端可复现。
  - 设计原则：LLM 负责创造性拆解，确定性算法负责正确性保证；每个 Agent 失败都有兜底，主链路永不中断。

#### Slide 04 - 背景与痛点

- **Audience move**: 评委理解项目要解决的真实问题及其普遍性 → 认可问题的价值。
- **Layout**: 顶部叙事引入段（完整句讲述组队协作的常见经历）；下方四张宽卡（2×2），每卡大号编号 + 标题 + 两段完整句说明，避免短句碎片。
- **Title**: 背景与痛点：为什么需要"活"的协作规划
- **Core message**: 现有规划工具与通用大模型都没有让计划随现实持续运转，也没有让多模态能力进入真实协作流程。
- **Content**:
  - 引入（叙事）：组队完成一个项目，几乎每个人都有过这样的经历：分工靠临时商量，任务边界含糊，有人忙有人闲，临近答辩才发现进度对不上。这背后是四个真实存在的痛点。
  - 痛点 01 静态计划表：绝大多数规划工具只在项目开始时生成一张分工表，从那以后就再也不更新。项目一旦变化——有人请假、任务调整、截止日提前——这张表立刻过时，团队只能回到群里反复对齐。
  - 痛点 02 大模型没有进入流程：多模态大模型的能力越来越强，但通常只被当作聊天机器人挂在旁边：问它问题、让它转写、让它识别图片。结果停留在对话框里，从来没有真正改变团队怎么分工、怎么排期。
  - 痛点 03 汇报成本高：成员更新任务状态要打开表格、找到任务、填写说明，步骤繁琐，于是大家选择不更新。负责人只能挨个追问进度，信息永远滞后。
  - 痛点 04 会议内容难沉淀：开一次会，录音、录像、白板照片散落各处，没有人愿意二次整理；会上明明讨论清楚的任务，会后依然要靠回忆。

#### Slide 05 - 解决思路：全模态协作闭环

- **Audience move**: 评委理解解决方案的整体逻辑：五步闭环 + 设计原则 → 与"单点 Demo"形成对比。
- **Layout**: 顶部叙事引入段；横向五步闭环图（每步含编号、图标、标题与两行完整句说明，箭头连接）；下方两条设计原则条。
- **Title**: 解决思路：以全模态能力驱动完整协作闭环
- **Core message**: 不是单点 Demo，而是「计划生成 → 成员执行 → 汇报反馈 → 通知触达」的完整闭环。
- **Content**:
  - 引入（叙事）：我们的答案不是做一个更强的聊天机器人，而是把全模态能力接进一条完整的协作流程：从立项到交付，每一步都有明确的动作和反馈，环环相扣。
  - 闭环五步（完整句）：拍照立项——白板、截图、手绘拍下来就能读懂，直接生成项目需求，不用逐字输入；说话排期——录音直接交给模型，听懂后生成任务草案，不必先转写再粘贴；确认分工——草案可以增删、拖拽、调整，每次改动即时重算排期与负载；成员汇报——免登录点开链接，语音或照片就能交，负责人确认后自动进入计划；群通知触达——状态一变，工作群自动收到通知，每天还有 AI 语音播报今日待办。
  - 设计原则 1：LLM 负责创造性拆解；关键路径、技能评分、负载均衡、依赖校验全部由确定性算法完成。
  - 设计原则 2：每个 Agent 失败都有确定性兜底；断网、模型离线时主链路依然可以完整演示。

### Part 2: 系统架构与核心机制

#### Slide 06 - 章节页 · 系统架构与核心机制

- **Audience move**: 评委进入架构章节，明确本节将回答"系统如何运转" → 建立技术信任。
- **Layout**: 章节页：大号章节编号（02）占主视觉，右侧章节名与本节要点列表（三至四项，带小图标）。
- **Title**: 02 系统架构与核心机制
- **Core message**: 本节说明系统如何分层、计划如何保证正确、多模态如何统一接入、协作如何闭环。
- **Content**:
  - 本节要点：
    - 总体架构：工作台 → FastAPI 后端 → 昇腾 A3 / 云端双后端（network）
    - 计划引擎：LLM 创造 × 算法正确的确定性机制（adjustments）
    - 全模态接入：统一适配层打通文本 / 语音 / 图片 / 视频 / TTS（sparkles）
    - 协作闭环：计划 → 执行 → 汇报 → 通知触达（send-2）

#### Slide 07 - 总体架构

- **Audience move**: 评委一眼看懂系统分层与双后端设计，认可架构的完整与工程化。
- **Layout**: 三层横向架构图：顶层前端、中层 FastAPI 后端（内部分组）、底层双后端（昇腾 A3 本地 / ModelBest 云端）；层间以连接线标注 HTTP / WebSocket；图下方一行架构关键点说明。
- **Title**: 总体架构：工作台 → 后端服务 → 昇腾双后端
- **Core message**: 前端统一入口，后端按业务域组织，模型层在昇腾 A3 本地与云端 Realtime API 之间一键切换。
- **Content**:
  - 前端层：Web 工作台（文本 / 语音 / 拍照 / 录像 / TTS / 甘特图 / 导出 / 提醒）；成员轻量汇报页（token 链接、免登录）。
  - 后端层（FastAPI，/api）：计划引擎（拆解 → 校验 → 分工 → CPM 关键路径排期 → 报告）；多模态适配 omni_chat（本地直听 / 云端先转写再理解）；媒体分析（PyAV 抽帧/抽音频、图片理解、扫描 PDF、音频转写）；协作闭环（汇报 token、负责人确认、即时重算、Webhook 通知）。
  - 模型层（WebSocket）：昇腾 A3（910C）+ llama.cpp-omni + MiniCPM-o-4_5-F16（本地主力）；ModelBest 云端 Realtime API（MiniCPM-o-4.5-Realtime，免费，开发期兜底）。
  - 架构关键点：双后端一键切换（ASCEND_OMNI_WS_URL 指向 A3 即走本地，注释掉走云端）；`/api/realtime/status` 返回 `backend: local | map` 直观确认当前链路。

#### Slide 08 - 计划引擎：确定性正确性保证

- **Audience move**: 评委理解计划结果为何可信、为何不依赖模型状态 → 认同"AI 与算法分工"的设计。
- **Layout**: 顶部五步流水线（拆解 → 校验 → 分工 → 排期 → 报告）；下方左右对照两栏：左"LLM 负责创造"、右"确定性算法负责正确"；底部一行失败兜底说明。
- **Title**: 计划引擎：LLM 创造 × 算法正确
- **Core message**: LLM 只负责创造性拆解；关键路径、技能评分、负载均衡、依赖校验全部由确定性算法保证，任何环节失败自动切换兜底。
- **Content**:
  - 流水线五步：任务拆解（LLM + 知识库工时校准）→ 结构化校验（依赖、边界）→ 技能匹配分工（三层角色 + 匹配度评分）→ CPM 关键路径排期（倒排日期、计算关键路径与浮动）→ 报告生成（Markdown + 数据化风险提示）。
  - LLM 负责：任务拆解、工时建议、报告表达、批判性审查建议。
  - 算法负责：CPM 关键路径与浮动、技能匹配评分、负载均衡、依赖校验、日期约束（跳过周末与成员不可用日）。
  - 兜底：拆解 / 分工 / 排期 / 审查任一环节 LLM 失败，自动切换到确定性规则实现；演示不依赖模型持续在线。

#### Slide 09 - 全模态接入链路

- **Audience move**: 评委理解五种模态如何被统一接入与理解，认可适配层设计的工程价值。
- **Layout**: 中心为 omni_chat 适配层（主色圆角框），四周五个模态输入节点（文本 / 语音 / 图片 / 视频 / TTS）以箭头汇入；适配层下方分两条输出分支（本地直听 / 云端先转写再理解）；底部一行实测说明。
- **Title**: 全模态接入：统一适配层打通五种交互
- **Core message**: 文本、语音、图片、视频、TTS 经统一适配层接入 MiniCPM-o 4.5；本地能直接听，云端 turn-based 自动改为"先转写再理解"，双后端行为统一。
- **Content**:
  - 文本（message）：SSE 流式对话，AI 建议抽屉带当前方案快照（/api/realtime/chat/stream）。
  - 语音（microphone）：麦克风直接对话、语音需求输入、语音汇报（/api/realtime/voice-chat、transcribe）。
  - 图片（camera）：拍照需求、白板 / 截图 / 扫描 PDF，理解文字与非文字内容（/api/analyze-files）。
  - 视频（video）：会议录像边看边听整理任务；答辩录像逐帧表现观察（/api/realtime/meeting、performance）。
  - TTS（volume）：语音回复开关与重听、今日播报（/api/realtime/tts，云端）。
  - 适配层 omni_chat：本地 llama-omni 能直接听；云端 turn-based 音频表现为"转写并复述"，自动改为先转写再文字理解；长音频分片处理。

#### Slide 10 - 协作闭环：执行、汇报与触达

- **Audience move**: 评委理解计划如何驱动真实执行并形成反馈闭环 → 认可"可运行可展示"的完整性。
- **Layout**: 环形流程图：计划生成 → 成员执行 → 汇报反馈 → 负责人确认 → 通知触达 → 即时重算回到计划；环内放一句核心说明；环外四个小卡片补充关键机制（token 免登录、语音/照片汇报、Webhook、今日播报）。
- **Title**: 协作闭环：从计划到执行再到触达
- **Core message**: 状态变更自动重算、通知自动推送、负责人确认完成——计划随现实持续运转。
- **Content**:
  - 闭环：计划生成 → 成员执行 → 汇报反馈（语音 / 照片 / 文本）→ 负责人确认完成 → 群通知自动推送 → 即时重算更新计划。
  - 关键机制 1（link）：成员 token 链接免登录，只看到自己的任务。
  - 关键机制 2（headphones）：语音上报状态 / 工时 / 备注；拍照交付物。
  - 关键机制 3（send）：状态变更自动推送企业微信 / 飞书 / 钉钉 Webhook。
  - 关键机制 4（calendar-stats）：今日播报——云端 TTS 朗读今日待办要点。

### Part 3: 核心功能详解

#### Slide 11 - 章节页 · 核心功能详解

- **Audience move**: 评委进入功能章节，明确本节将逐模块说明"能做什么、怎么用"。
- **Layout**: 章节页：大号章节编号（03）+ 章节名 + 本节要点列表（五大模块）。
- **Title**: 03 核心功能详解
- **Core message**: 计划引擎为核心，动态协作、全模态交互、汇报通知、工程能力共同构成完整产品。
- **Content**:
  - 本节要点：功能总览矩阵；计划引擎（拆解 / 工时 / 匹配 / 均衡）；CPM 关键路径排期；动态协作与版本管理；成员汇报与群通知；全模态交互能力矩阵。

#### Slide 12 - 功能总览

- **Audience move**: 评委获得功能全景地图，之后各页可对照展开。
- **Layout**: 五大模块卡片网格（每卡：模块名 + 图标 + 功能清单）；卡间以细规则线分隔，保持可扫读。
- **Title**: 功能总览：五大能力模块
- **Core message**: 计划引擎为核心，动态协作、全模态交互、汇报通知、工程能力围绕其展开。
- **Content**:
  - 计划引擎（adjustments）：小型 / 大型项目双模式；任务拆解、工时估算、CPM 关键路径排期、技能匹配分工、负载均衡、报告生成、批判性审查。
  - 动态协作（refresh）：可编辑草案、拖拽分工、即时重算、成员管理、版本管理（快照 / 对比 / 回滚）；大型项目分阶段流程（大模块 → 骨干认领 → 子任务 → 志愿者招募）。
  - 全模态交互（sparkles）：文本、语音、图片、视频、TTS 五种模态的产品化表达。
  - 成员汇报与通知（send）：轻量汇报页、语音 / 照片汇报、负责人确认、群通知、今日播报。
  - 工程能力（settings）：MD / Word / PDF / Excel / CSV / ICS 导出、知识库问答、鉴权与审计、监控与演示前预检。

#### Slide 13 - 计划引擎功能详解

- **Audience move**: 评委理解拆解、工时、匹配、均衡四项核心机制的具体设计。
- **Layout**: 2×2 四宫格卡片，每格标题 + 图标 + 机制说明（两至三行）；底部一行"失败自动兜底"条。
- **Title**: 计划引擎：拆解 · 工时 · 匹配 · 均衡
- **Core message**: 四项机制协同，保证拆解有依据、工时可学习、分工有评分、负载可均衡。
- **Content**:
  - 任务拆解（clipboard-list）：小型项目直接拆解分工；大型项目按"大模块 → 骨干认领 → 子任务 → 志愿者招募"分阶段推进；LLM + 知识库工时校准生成弹性子任务，按项目规模 1–8 项；失败自动切换确定性兜底。
  - 工时估算（clock）：结构化工时案例库检索 + 用户反馈学习；实际偏差自动沉淀回知识库，越用越准。
  - 技能匹配分工（target）：中文 / 近义词 / 中英文技能匹配；负责人 + 主要协助 + 辅助协助三层角色，给出匹配度评分。
  - 负载均衡（adjustments）：基于成员可用工时与阶段负载自动均衡；均衡后重算匹配评分并说明理由。
  - 兜底条：任一环节 LLM 失败 → 确定性规则实现，主链路不中断。

#### Slide 14 - CPM 关键路径排期

- **Audience move**: 评委理解排期为何正确可靠：算法保证而非模型猜测。
- **Layout**: 左侧算法要点列表（四条）；右侧甘特示意（原生 SVG：任务条、里程碑、依赖连线、关键路径高亮，附图注"示意，可替换为真实截图"）。
- **Title**: CPM 关键路径排期：确定性算法保证
- **Core message**: 纯算法倒排日期、计算关键路径与浮动，跳过周末与成员不可用日，支持手动固定日期。
- **Content**:
  - 算法要点 1（calendar）：倒排日期——从截止日反向推导各任务起止。
  - 算法要点 2（timeline）：计算关键路径与浮动，识别影响整体进度的任务。
  - 算法要点 3（users）：跳过周末与成员不可用日，尊重资源日历。
  - 算法要点 4（edit）：支持手动固定日期，计划可人工微调。
  - 甘特示意：任务条 + 里程碑 + 依赖连线；关键路径以金色高亮；图注：示意绘制，运行界面见 P25。

#### Slide 15 - 动态协作与版本管理

- **Audience move**: 评委理解计划如何随现实变化持续保持可用，而非一次生成。
- **Layout**: 左侧三行交互能力（编辑 / 拖拽 / 重算）+ 右侧版本管理流程（保存快照 → 版本树 → 差异对比 → 分支回滚）。
- **Title**: 动态协作：计划随现实变化实时重算
- **Core message**: 增删改、拖拽、状态切换、成员变动后即时重算；保存即版本快照，可对比、可回滚。
- **Content**:
  - 可编辑草案（edit）：增删改 / 拆分 / 合并任务与模块，确认前自由调整。
  - 拖拽分工（arrows-shuffle）：看板拖拽负责人与协作者，一键恢复自动分工。
  - 即时重算（refresh）：切换任务状态 / 成员变动 / 工时调整后自动重算排期与分工。
  - 成员管理（users）：增删成员、调整工时 / 角色 / 不可用日期，自动重排。
  - 大型项目模式（users-group）：大模块拆解 → 骨干认领（负责人 + 技能 / 工时 / 不可用日期）→ 子任务拆解 → 志愿者招募与认领，多阶段连续推进。
  - 版本管理（git-branch）：保存即生成版本快照；相似任务版本树、差异对比、分支回滚。

#### Slide 16 - 成员汇报与群通知

- **Audience move**: 评委理解低门槛汇报与自动触达如何显著降低协作成本。
- **Layout**: 横向四步流程卡（生成链接 → 语音/照片汇报 → 负责人确认 → 群通知 / 今日播报），底部加一行"全程免登录"强调条。
- **Title**: 汇报触达：免登录汇报 + 自动群通知
- **Core message**: token 链接免登录，成员只看到自己的任务；状态变更自动推送企业微信 / 飞书 / 钉钉。
- **Content**:
  - 步骤 1（link）：轻量汇报页——token 链接免登录，成员只看到自己的任务。
  - 步骤 2（headphones）：语音上报状态 / 工时 / 备注；拍照交付物。
  - 步骤 3（clipboard-check）：负责人确认完成，变更进入计划。
  - 步骤 4（send）：状态变更自动推送企业微信 / 飞书 / 钉钉 Webhook；今日播报（云端 TTS 朗读今日待办要点）。
  - 强调条：汇报与确认全程免登录、低门槛，手机即可完成。

#### Slide 17 - 全模态交互能力矩阵

- **Audience move**: 评委对照能力清单理解每种模态的产品化表达与对应接口。
- **Layout**: 表格（模态 / 能力 / 典型场景 / 对应接口），五行内容；表头主色底白字。
- **Title**: 全模态交互：五种模态 × 真实协作场景
- **Core message**: 每种模态都对应真实协作动作，并有明确接口支撑。
- **Content**:
  - 文本（message）：SSE 流式对话、AI 建议抽屉带方案快照；场景：边看方案边提问；接口：/api/realtime/chat。
  - 语音（microphone）：麦克风对话、语音需求输入、语音汇报；场景：说话排期、路上汇报；接口：/api/realtime/voice-chat、transcribe。
  - 图片（camera）：拍照需求、白板 / 截图 / 扫描 PDF；场景：拍照立项、白板拍照进拆解；接口：/api/analyze-files。
  - 视频（video）：会议录像边看边听、答辩录像表现观察；场景：会议旁听整理任务；接口：/api/realtime/meeting、performance。
  - TTS（volume）：语音回复开关与重听、今日播报；场景：今日待办播报；接口：/api/realtime/tts（云端）。

### Part 4: 创新点

#### Slide 18 - 章节页 · 创新点

- **Audience move**: 评委进入创新章节，明确四个创新点的递进关系：入口 → 机制 → 闭环 → 昇腾落地。
- **Layout**: 章节页：大号章节编号（04）+ 章节名 + 四个创新点编号列表。
- **Title**: 04 创新点
- **Core message**: 四个创新点分别回答：模型怎么用、结果怎么可信、系统怎么完整、昇腾怎么落地。
- **Content**:
  - 01 全模态从"聊天"进入真实协作流程（sparkles）
  - 02 LLM 创造 × 算法正确的确定性保证（adjustments）
  - 03 完整协作闭环与全环节故障兜底（lifebuoy）
  - 04 昇腾本地 + 云端双后端统一适配与合规（cpu）

#### Slide 19 - 创新点一：全模态从"聊天"进入真实流程

- **Audience move**: 评委认同这是产品化表达而非功能堆砌 → 认可"全模态能力的产品化表达"这一评审点。
- **Layout**: 顶部叙事引入段（现状问题）→ 四个完整句场景块（2×2）→ 底部"为什么这是创新"结论条（主色底白字）。
- **Title**: 创新点一：把多模态模型变成协作流程的入口
- **Core message**: 拍照、录音、会议录像直接进入任务拆解——模型第一次成为真实工作流的入口，而非附加聊天窗。
- **Content**:
  - 引入（现状）：市面上几乎所有多模态应用都把模型放在聊天框里——你问它问题，它回答你，然后就没有然后了。图片识别、语音转写、视频理解都是一个个孤立的按钮，结果永远不会变成团队下一步要做的事。
  - 拍照立项（camera）：白板、截图、手绘拍下来直接进入任务拆解；系统理解的不只是文字，还有图表、流程和界面，并把它们转化成项目需求。
  - 说话排期（microphone）：录音直接交给模型，听懂后生成任务草案；不需要先转写、再复制、再粘贴，说完就开始了。
  - 会议旁听（video）：会议录像边看边听——画面和声音分别理解后合并，自动整理成要点、任务和风险。
  - 答辩直连（volume）：评委的语音、视频点评直接对话，答辩录像附逐帧表现观察，现场就能得到反馈。
  - 结论（为什么这是创新）：这些能力的共同点，是模型每一次输出都落到工作流的下一个动作上——要么变成任务，要么变成排期，要么变成汇报。多模态能力第一次不是附加功能，而是协作这件事的入口。

#### Slide 20 - 创新点二：LLM 创造 × 算法正确

- **Audience move**: 评委理解关键计划环节如何防幻觉、保正确 → 认同工程设计的可信度。
- **Layout**: 顶部叙事引入段（AI 规划输出不稳定问题）→ 双引擎对照图（左 LLM、右确定性算法，中间"分工明确"徽标）→ 底部"算法安全带"结论条。
- **Title**: 创新点二：确定性正确性保证
- **Core message**: 关键路径、技能评分、负载均衡、依赖校验均为确定性算法；LLM 只负责创造性拆解。
- **Content**:
  - 引入（问题）：AI 规划工具最大的问题是输出不稳定：同一个需求，模型这次给三周、下次给五天；今天推荐的负责人和明天不一样。计划是要拿来执行的，不确定性就是成本。所以我们把架构拆成两半——模型负责创造，算法负责正确。
  - LLM 负责创造（sparkles）：理解自然语言需求、拆解任务、给出工时建议、组织报告、提出批判性审查建议——这些没有唯一正确答案，适合大模型发挥。
  - 确定性算法负责正确（adjustments）：关键路径与浮动、技能评分、负载均衡、依赖校验、跳过周末与不可用日的日期约束——这些必须精确、可复现，绝不能依赖模型输出的偶然正确。
  - 结论（算法安全带）：模型发挥创造，算法保证正确；任何 LLM 环节失败都自动切到确定性规则，断网、模型离线时主链路依然完整。

#### Slide 21 - 创新点三：完整闭环 + 全环节兜底

- **Audience move**: 评委理解这不是单点 Demo，且演示可靠性有明确保障。
- **Layout**: 顶部叙事引入段（单点 Demo 的问题）→ 横向闭环图（四节点）→ 四道确定性兜底卡（2×2）。
- **Title**: 创新点三：完整协作闭环，任何环节失败都有兜底
- **Core message**: 覆盖"计划生成 → 成员执行 → 汇报反馈 → 通知触达"全链路；断网或模型不可用时主链路仍可演示。
- **Content**:
  - 引入（问题）：很多作品是一个漂亮的单点 Demo——识别很准、语音很流畅，但离"能解决一个问题"还差很远；评审时最怕的，是演示到一半模型不可用。所以我们做的不是单点，而是一条闭环，并给每一环都准备了确定性兜底。
  - 闭环：计划生成 → 成员执行 → 汇报反馈 → 通知触达（语音 / 照片 / 文本汇报，负责人确认，Webhook 群通知与今日播报）。
  - 拆解兜底（clipboard-list）：LLM 拆解失败，用确定性拆解和弹性子任务规则顶上，方案照样生成。
  - 分工兜底（users）：分工失败，用规则分工与评分兜底，匹配度依然可解释。
  - 排期兜底（timeline）：排期失败，用算法排期兜底，日期约束依然生效。
  - 断网兜底（lifebuoy）：断网或模型离线，本地保留确定性拆解与导出能力，主链路仍可演示。
  - 结论：演示不依赖模型持续在线。哪怕模型全挂，核心协作流程照样能走通——这对"可运行、可展示"的评审要求是实打实的保障。

#### Slide 22 - 创新点四：昇腾双后端统一适配

- **Audience move**: 评委理解昇腾落地路径与模型合规性 → 认可"统一昇腾环境复现"这一评审点。
- **Layout**: 顶部叙事引入段（昇腾算力紧张问题）→ 双后端对照卡（中间"一键切换"徽标）→ 状态确认条 → 合规要点条。
- **Title**: 创新点四：昇腾本地 + 云端双后端统一适配
- **Core message**: 同一套 WebSocket 协议兼容本地 llama-omni 与云端 Realtime API，一键切换；合规模式下全链路仅调用 MiniCPM-o 4.5。
- **Content**:
  - 引入（问题）：昇腾赛要求统一昇腾环境复现，但 910C 算力紧张，开发期不可能一直占着。我们的做法，是让同一套系统同时支持两种后端，一键切换——评审用本地昇腾证明复现，开发用云端保证进度。
  - 本地后端（cpu）：昇腾 A3（Ascend 910C）+ llama.cpp-omni + MiniCPM-o-4_5-F16（约 16G），评审期主力，用于真实昇腾环境复现；本地能直接听，实听实答。
  - 云端后端（cloud）：ModelBest Realtime API（MiniCPM-o-4.5-Realtime），免费，开发期兜底；turn-based 音频先转写再理解，行为经适配层与本地统一。
  - 统一适配（network）：同一套 WebSocket 协议——配置 ASCEND_OMNI_WS_URL 走本地，注释掉走云端；`/api/realtime/status` 返回 `local | map` 一键确认。
  - 合规要点（shield-check）：`APP_MODEL_MODE=minicpm` 且 `APP_ALLOW_EXTERNAL_MODELS=0` 时，全链路仅调用 MiniCPM-o 4.5，不创建任何其他模型客户端；API Key 仅存 `.env`，不入版本库。

### Part 5: 应用落地与工程验证

#### Slide 23 - 章节页 · 应用落地与工程验证

- **Audience move**: 评委进入应用与验证章节，明确本节将展示"谁能用、效果如何、能否复现"。
- **Layout**: 章节页：大号章节编号（05）+ 章节名 + 本节要点列表。
- **Title**: 05 应用落地与工程验证
- **Core message**: 真实场景、真实运行效果、真实测试与昇腾实测，均可在统一昇腾环境复现。
- **Content**:
  - 本节要点：应用场景与落地形态；运行效果展示（截图占位）；工程验证（379 项测试 + A3 实测）；统一昇腾环境复现；合规安全与已知边界。

#### Slide 24 - 应用场景与落地形态

- **Audience move**: 评委理解项目的真实使用场景与落地方式，认可实用性与场景价值。
- **Layout**: 四张场景卡片（2×2：学生团队 / 社团活动 / 小型项目组 / 比赛冲刺），每卡图标 + 一句话场景 + 两行典型用法；底部一条落地形态横幅。
- **Title**: 应用场景：从课堂到团队协作
- **Core message**: 面向学生与中小型企业两类协作场景；小型项目扁平推进，大型项目分层认领；通知可接入企业微信 / 飞书 / 钉钉，落地轻量、触达自动。
- **Content**:
  - 学生团队项目 / 课程设计（school）：拍照白板、语音需求直接进拆解；成员免登录汇报，进度透明。
  - 社团 / 活动组织（building-community）：成员管理、任务认领、状态变更自动群通知。
  - 小型企业项目组——小型项目模式（users-group）：成员已确定、组织关系简单，快速拆解 → 排期 → 分工 → 汇报闭环；通知可直接推送到企业微信 / 钉钉 / 飞书工作群，融入企业既有协作工具。
  - 中型 / 复杂项目——大型项目模式（stack）：先拆分大模块，骨干认领负责，子任务拆解后志愿者招募与认领；适合跨部门项目、外包协同、赛事活动等成员与分工动态变化的场景。
  - 比赛 / 冲刺团队（trophy）：快速排期、负载均衡、今日播报，冲刺阶段节奏可控。
  - 落地形态横幅：Web 工作台 + 免登录成员汇报页 + 企业微信 / 飞书 / 钉钉 Webhook 群通知 + 多格式导出（Word / PDF / Excel / CSV / ICS）+ 只读分享链接。

#### Slide 25 - 运行效果展示（截图占位）

- **Audience move**: 评委看到真实运行界面（由作者在提交前粘贴截图）→ 建立"可运行可展示"的直接证据。
- **Layout**: 三个等宽占位框横排：工作台首页 / 任务草案与甘特图 / 成员汇报页与群通知；每框为虚线边框 + 中央图标 + "截图占位：请粘贴运行截图"注释；底部一行说明。
- **Title**: 运行效果展示
- **Core message**: 本页为真实运行截图占位，提交前由作者粘贴对应界面截图。
- **Content**:
  - 占位 1（player-play）：工作台首页——项目输入、方案总览、AI 建议抽屉。
  - 占位 2（timeline）：任务草案与甘特图——拆解 / 排期 / 分工 / 拖拽调整。
  - 占位 3（send）：成员汇报页与群通知——免登录汇报、语音 / 照片交付、Webhook 推送。
  - 底部说明：本页占位框为矢量图形，作者在 PowerPoint 中直接以真实运行截图替换 / 覆盖即可；截图来源建议：昇腾 A3 环境录屏或本地实测。

#### Slide 26 - 工程验证：测试与昇腾 A3 实测

- **Audience move**: 评委获得可核验的工程完整度证据 → 认可"可运行、可验证、可展示"。
- **Layout**: 左侧两行大数字 KPI（379 项自动化测试 / 全部通过；健康检查 ok）；右侧 A3 实测指标表（能力 / 实测延迟 / 说明）；底部来源注。
- **Title**: 工程验证：379 项测试通过 + 昇腾 A3 实测
- **Core message**: 自动化测试全绿；昇腾 A3（910C / CANN 9.1.0-beta.1 / llama.cpp-omni / F16）实测延迟与健康检查记录在案。
- **Content**:
  - 测试 KPI：379 项自动化测试全部通过（`python -m pytest tests/ -q`，无需配置密钥）。
  - 健康检查：`/api/health` → `{"engine":"comni","status":"ok"}`。
  - A3 实测表：文本对话约 2s；语音理解约 8s；图片理解约 7s（含无文字流程图）；答辩录像 4 帧表情分析约 4s；会议视频边看边听端到端约 16s（3 帧 + 音频理解）。
  - 云端实测：语音转写、图片理解、TTS 朗读均正常；音频理解自动"先转写再理解"。
  - 来源注：测试与实测数据取自项目 `docs/项目说明.md` 第 8 节与 `README.md`（2026-08-24 记录；测试数于 2026-08-27 复核为 379 passed）。

#### Slide 27 - 统一昇腾环境复现

- **Audience move**: 评委按图索骥可复现 → 满足"统一昇腾环境复现验证"评审点。
- **Layout**: 左侧环境版本表（应用 / A3 环境 / 模型 / 编译）；右侧三步复现流程（安装依赖 → 配置后端二选一 → 启动与验证）；底部一键预检条。
- **Title**: 统一昇腾环境复现
- **Core message**: 环境版本固定，两条链路均可一键启动与验证。
- **Content**:
  - 环境版本表：应用 Python 3.11+；A3 环境 Ascend 910C / CANN 9.1.0-beta.1 / Python 3.12.13；模型 MiniCPM-o-4_5-F16（约 16G）；推理服务 llama.cpp-omni（`-DGGML_CANN=ON` 编译）。
  - 复现步骤：1) `pip install -r requirements.txt`；2) 配置 `.env`（二选一：云端 `MAP_REALTIME_API_KEY`，或本地 `ASCEND_OMNI_WS_URL=ws://127.0.0.1:28099/backend`）；3) `python -m app.main` 启动（默认 http://127.0.0.1:8000）。
  - 验证：`/api/health` 健康检查；`/api/realtime/status` 返回 `backend: local | map`；`python scripts/preflight_demo.py` 演示前一键预检（应用 / 后端 / A3 health / MiniCPM-o 暖机）。

#### Slide 28 - 合规安全与已知边界

- **Audience move**: 评委认可工程严谨与诚实 → 建立信任。
- **Layout**: 左栏合规与安全要点（三项，shield-check 图标）；右栏已知边界（四项，alert-circle 图标，标题注明"如实说明"）；底部一句工程态度说明。
- **Title**: 合规安全与已知边界（如实说明）
- **Core message**: 模型合规可控、密钥安全、操作可审计；同时对已知边界如实披露，不夸大能力。
- **Content**:
  - 合规与安全（shield-check）：全链路仅调用 MiniCPM-o 4.5（合规模式）；API Key 仅存 `.env` 不进入版本库；多用户 ACL、会话登录、操作审计与版本回滚。
  - 已知边界 1（alert-circle）：8B 模型推理能力有限，演示主打感知与流程闭环，不做高难度推理。
  - 已知边界 2（alert-circle）：910C 本地 TTS 有已知算子问题，语音播报走云端 API。
  - 已知边界 3（alert-circle）：云端 turn-based 音频表现为转写，已通过适配层统一为"先转写再理解"。
  - 已知边界 4（alert-circle）：llama-omni-server 同一时间只支持一个活跃 session，演示前需运行预检暖机。
  - 工程态度：能力边界如实标注，确保演示可预期、可复现。

### Part 6: 总结与展望

#### Slide 29 - 总结与展望

- **Audience move**: 评委带着清晰的价值判断离开：项目是什么、强在哪、往哪走。
- **Layout**: 顶部一句话核心价值横幅（主色底白字）；中间四个关键词（全模态入口 / 确定性正确 / 完整闭环 / 昇腾可复现）；下方展望三项（带图标）；底部结尾信息行（版本 · 赛道 · 日期）。
- **Title**: 总结与展望
- **Core message**: 一个把 MiniCPM-o 4.5 变成真实协作流程入口的完整、可复现、可落地闭环。
- **Content**:
  - 核心价值横幅：把 MiniCPM-o 4.5 从"聊天机器人"变成真实协作流程的入口——拍照立项、说话排期、确认分工、语音/照片汇报、群通知，全链路由全模态能力驱动。
  - 关键词 1（sparkles）：全模态入口——五种模态进入真实协作流程。
  - 关键词 2（adjustments）：确定性正确——LLM 创造 × 算法保证。
  - 关键词 3（lifebuoy）：完整闭环——计划 → 执行 → 汇报 → 触达，全环节兜底。
  - 关键词 4（cpu）：昇腾可复现——A3 本地 / 云端双后端统一适配。
  - 展望 1（video）：实时语音 / 视频双工体验（云端 mode=audio/video 全双工实时对话）。
  - 展望 2（send）：企业微信"应用消息"定向推送，触达更精准。
  - 展望 3（database）：课程、赛事、企业项目等场景模板与知识库经验持续沉淀，工时估算与分工建议越用越准。
  - 结尾信息：v7.1 · 2026-08 · 华为昇腾创新应用赛道 · OpenBMB / 面壁智能 × 华为昇腾

## X. Speaker Notes Requirements

- **Generation**: enabled
- **Filename**: 与 `svg_output/` 每个 SVG 文件名一一对应，写入 `notes/total.md`
- **Content**: 逐页以该页最终 SVG 的全部信息承载内容为据撰写备注：补充背景（为什么这一页存在）、机制解释（架构/算法如何运转）、证据口径（测试数、A3 实测的来源与含义）、以及与项目说明 / 演示视频的对照指引；不引入页面上没有的新事实。
- **Total duration**: 文档型提交，不预设演讲时长；如用于答辩路演，可节选核心章节（约 8–10 分钟）。
- **Notes style**: formal（正式、平实、说明书式）
- **Presentation purpose**: inform, explain, persuade（以说明与解释为主，辅以说服）
