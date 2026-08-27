# 协作分工智能体 · 昇腾全模态版

**版本：v7.1（华为昇腾创新应用赛道）** | 最后更新：2026-08-27

> 别人给你一张**静态分工表**；我们给你一张**可编辑的活协作图**——拍照立项、说话排期、确认分工、语音/照片汇报、群通知，全链路由 MiniCPM-o 4.5 全模态能力驱动。

输入「项目要求 + 团队成员 + 截止日」，系统自动 **拆解任务 → CPM 关键路径排期 → 技能匹配分工 → 生成报告**，并支持随时增删改任务、拖拽调整分工、切换状态后即时重算。

---

## 赛道与定位

面向 **华为昇腾创新应用赛道**（OpenBMB / 面壁智能 × 华为昇腾，围绕 MiniCPM-o 4.5 全模态模型）：

- 把多模态大模型从"聊天机器人"变成**真实协作流程的入口**：拍照、录音、会议录像直接进入任务拆解；
- 计划引擎提供**确定性正确性保证**（关键路径、技能评分、负载均衡、依赖校验），LLM 只负责创造性拆解；任何环节失败都有兜底，演示不依赖模型持续在线；
- 覆盖「计划生成 → 成员执行 → 汇报反馈 → 通知触达」的完整闭环，而非单点 Demo。

## 核心能力

### 全模态交互（MiniCPM-o 4.5）

| 能力 | 说明 |
|---|---|
| 文本对话 | AI 建议抽屉带当前方案快照，SSE 流式输出 |
| 语音 | 麦克风直接对话、语音需求输入、语音汇报、今日播报（TTS） |
| 图片 | 拍照需求、白板/截图/扫描 PDF 理解（文字 + 非文字内容） |
| 视频 | 会议录像边看边听整理任务；答辩录像逐帧表现观察 |
| TTS | 语音回复开关与重听（云端后端） |

### 计划引擎

Planner（任务拆解）→ Matcher（技能匹配分工）→ Timeline（CPM 排期）→ Reporter（报告）→ Reflection（批判审查），详见[系统架构](#系统架构)。

### 协作闭环

- 成员轻量汇报页：token 链接、无需登录，语音 / 拍照 / 工时上报，负责人确认完成；
- 群通知：企业微信 / 飞书 / 钉钉 Webhook 自动推送状态变更；
- 版本管理：保存即生成版本快照，可对比、回滚、分支。

### 合规性

`APP_MODEL_MODE=minicpm` 且 `APP_ALLOW_EXTERNAL_MODELS=0` 时，**全链路仅调用 MiniCPM-o 4.5**（昇腾 A3 本地或 ModelBest 云端），不创建任何其他模型客户端。

## 演示流程（五步）

1. 📷 拍照 / 录音描述需求 → 自动识别进拆解；
2. 🎤 语音补充项目要求 → 生成任务草案；
3. ✅ 人工调整草案 → 确认分工（可拖拽、即时重算）；
4. 📱 成员汇报：语音 / 拍照交付 → 负责人确认；
5. 🔔 群通知自动推送 → 今日播报。

完整讲稿与异常兜底见 [比赛Demo演示流程](docs/比赛Demo演示流程.md) 与 [演示脚本](docs/演示脚本.md)。

## 系统架构

```text
┌──────────────┐      ┌────────────────────────────────┐
│  Web 工作台   │      │          FastAPI 后端           │
│  文本/语音/   │ ───► │ 计划引擎：拆解→分工→排期→报告      │
│  拍照/录像    │      │ 多模态适配：本地直听/云端先转写    │
│  甘特图/导出  │      │ 汇报闭环：token/确认/通知         │
└──────────────┘      └───────────────┬────────────────┘
                                      │ WebSocket
                      ┌───────────────┴────────────────┐
              ┌───────▼────────┐            ┌──────────▼─────────┐
              │ 昇腾 A3 (910C)  │            │ ModelBest 云端      │
              │ llama-omni     │            │ MiniCPM-o-4.5-      │
              │ MiniCPM-o F16  │            │ Realtime API        │
              └────────────────┘            └────────────────────┘
```

| 模块 | 文件 | 职责 |
|---|---|---|
| 计划引擎 | `app/coordinator.py` + `app/agents/` | 拆解、校验、分工、CPM 排期、报告、反射审查 |
| Realtime 客户端 | `app/services/realtime_client.py` | 云端 MAP / 本地 llama-omni 双后端 WebSocket 协议 |
| 音频适配 | `app/services/omni_chat.py` | 本地直听 / 云端先转写再理解、长音频分片 |
| 媒体分析 | `app/services/media_analysis.py` | 图片理解、音频转写、视频抽帧/抽音频、扫描 PDF |
| 汇报闭环 | `app/web/routers/report.py` | 成员 token、语音/照片汇报、负责人确认 |
| Web 前端 | `app/web/` | 原生 JS 单页工作台 + 成员汇报页 |

**设计原则**：LLM 负责创造性拆解，确定性算法负责正确性保证；每个 Agent 失败都有兜底，主链路永不中断。

## 快速开始

### 环境

- Python 3.11+
- 安装依赖：`pip install -r requirements.txt`
- 复制配置：`cp .env.example .env`

### 配置模型后端（二选一）

**方式 A：ModelBest 云端 Realtime API（开发/评审期通用，免费）**

```dotenv
MAP_REALTIME_API_KEY=你的ModelBest_API_Key
MAP_REALTIME_MODEL=MiniCPM-o-4.5-Realtime
MAP_REALTIME_BASE_URL=wss://api.modelbest.cn/v1/realtime
MAP_REALTIME_MAX_TOKENS=1024
MAP_REALTIME_TIMEOUT=60
```

**方式 B：昇腾 A3 本地 llama-omni-server（统一昇腾环境复现）**

```dotenv
ASCEND_OMNI_WS_URL=ws://127.0.0.1:28099/backend
ASCEND_OMNI_TIMEOUT=300
```

### 启动

```bash
python -m app.main        # 默认 http://127.0.0.1:8000
```

打开页面点「载入演示案例」即可跑通完整流程；`GET /api/realtime/status` 返回 `backend: local | map` 可确认当前链路。

完整接入说明见 [华为昇腾创新应用赛道接入说明](docs/华为昇腾创新应用赛道接入说明.md)，A3 部署见 [昇腾A3_910C_llama_omni部署指南](docs/昇腾A3_910C_llama_omni部署指南.md)。

## 验证与测试

```bash
# 全量自动化测试（379 passed）
python -m pytest tests/ -q

# 演示前一键预检（应用 / 后端 / A3 health / MiniCPM-o 暖机）
python scripts/preflight_demo.py
```

## 公网部署（Render）

仓库根目录的 `render.yaml` 已按合规模板配置启动命令与健康检查。仓库当前为私有：登录 [Render](https://render.com) → **New → Blueprint** → 连接 GitHub 并选择本仓库 → 按需填写环境变量后部署：

| 变量 | 必填 | 说明 |
|---|---|---|
| `MAP_REALTIME_API_KEY` | 是 | ModelBest 云端 Realtime Key |
| `APP_ADMIN_TOKEN` | 建议 | 管理端登录口令（评委凭此登录体验） |
| `ASCEND_OMNI_WS_URL` | 否 | 公网留空；自托管昇腾评审环境可填 A3 地址 |
| `STORAGE_BACKEND` | 否 | 默认 `local`；重启不丢数据可配 `s3` |

部署后访问 `https://<实例>.onrender.com`，检查 `/api/health` 与 `/api/realtime/status`。免费实例闲置会休眠，正式展示前请先访问一次预热。

## 项目结构

```text
app/
├── main.py / config.py      # 入口与全局配置
├── coordinator.py           # 主链路编排
├── models/schemas.py        # JSON 接口契约
├── agents/                  # Planner / Matcher / Timeline / Reporter / Reflection / InterviewSim
├── services/                # project_service / realtime_client / omni_chat / media_analysis / report
├── llm/                     # LLM 客户端（合规模式仅 MiniCPM-o）+ 提示词
└── web/                     # FastAPI 路由 + 原生前端（index.html / app.js）
tests/                       # 379 项自动化测试
docs/                        # 项目说明、复现文档、部署指南、演示脚本
scripts/                     # 演示预检、A3 冒烟、模型下载
```

## 核心接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/run` | 完整主链路：拆解 → 分工 → 排期 → 报告 |
| POST | `/api/analyze-files` | 图片 / 音频 / PDF 多模态需求识别 |
| POST | `/api/realtime/chat` | MiniCPM-o 文本对话（SSE 流式：`/api/realtime/chat/stream`） |
| POST | `/api/realtime/voice-chat` | 语音直接对话 |
| POST | `/api/realtime/meeting` | 会议录音/录像边看边听整理任务 |
| POST | `/api/realtime/interview-turn` | 答辩评委语音/视频点评追问 |
| POST | `/api/report/link` | 生成成员轻量汇报链接 |
| POST | `/api/export/{docx,pdf,md,excel,csv,ics}` | 多格式导出 |
| GET | `/api/realtime/status` | 后端状态（local / map） |
| GET | `/api/health` | 健康检查 |

完整接口与协议见 [华为昇腾创新应用赛道接入说明](docs/华为昇腾创新应用赛道接入说明.md)。

## 文档索引

| 文档 | 用途 |
|---|---|
| [项目说明](docs/项目说明.md) | 提交材料：架构、能力、接口、测试结果 |
| [复现文档](docs/复现文档.md) | 提交材料：昇腾环境版本、模型、启动命令、验证结果 |
| [使用说明书](docs/使用说明书.md) | 面向使用者的操作手册 |
| [华为昇腾创新应用赛道接入说明](docs/华为昇腾创新应用赛道接入说明.md) | API 与双后端接入协议 |
| [昇腾A3_910C_llama_omni部署指南](docs/昇腾A3_910C_llama_omni部署指南.md) | A3 完整部署与排障 |
| [比赛Demo演示流程](docs/比赛Demo演示流程.md) / [演示脚本](docs/演示脚本.md) | 演示讲稿与五步流程 |
| [功能验证清单](docs/功能验证清单.md) | P0 全模态交互验收 |
| [单 Agent 调试指南](docs/单Agent调试指南.md) | 开发向：单独调试 Agent / 提示词 |

## 版本与变更

| 版本 | 日期 | 定位 |
|---|---|---|
| v7.1 | 2026-08-24 | 全链路审计修复 + 比赛前审查收尾（A3 长音频防崩、语音记忆、Matcher JSON 修复、auth 加固） |
| v7.0 | 2026-08-22 | 视频理解：会议录像边看边听 + 多模态演示闭环 |
| v6.9 | 2026-08-21 | 多模态需求输入：语音描述与拍照直接生成任务 |
| v6.0 | 2026-08-20 | 接入 MiniCPM-o Realtime Chat 模式（昇腾创新应用赛道） |
| v5.76 | 2026-08-18 | 基础版通用能力整合（角色视图、AI 助手、OCR、监控、存储） |

完整变更记录（含问题分析、代码对照）见 [CHANGELOG.md](CHANGELOG.md)。
