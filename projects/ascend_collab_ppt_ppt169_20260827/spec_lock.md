<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: 华为昇腾创新应用赛道评审（评委）——重点考察创新性、实用性、场景价值、工程完整度、可运行可展示、统一昇腾环境复现
- objective: 让评委独立阅读后准确复述项目的核心功能链路、核心创新点、实际应用场景与工程验证证据（379 项自动化测试通过、昇腾 A3 实测、统一昇腾环境复现），并据此作出正向评审判断。
- core_message: 把 MiniCPM-o 4.5 从"聊天机器人"变成真实协作流程的入口：拍照立项、说话排期、确认分工、语音/照片汇报、群通知的完整闭环；LLM 负责创造、算法负责正确、任何环节失败都有兜底；双后端一键切换，昇腾 A3 本地或云端可复现。

## mode
- mode: custom
- mode_references: briefing, pyramid
- mode_behavior: 以说明书式完整覆盖为骨架（主题式标题、可扫读、并列均衡），但把评委最关心的三个要点——核心功能、创新点、落地应用——在开篇"项目一览"页以结论前置的方式集中呈现，随后按"背景问题 → 系统架构 → 核心功能 → 创新点 → 应用与工程验证 → 边界与展望"完整展开；标题以主题为主，关键证据页辅以结论式副标题。

## visual_style
- visual_style: swiss-minimal

## colors
- background: #FFFFFF
- secondary_bg: #F2F5FA
- primary: #375A86
- accent: #C9A227
- secondary_accent: #7B9DC4
- body_text: #1E293B
- grid: #DCE6F0
- muted: #64748B

## typography
- font_family: Microsoft YaHei, Arial, sans-serif
- title_family: Microsoft YaHei, Arial, sans-serif
- body_family: Microsoft YaHei, Arial, sans-serif
- title: 36
- subtitle: 28
- lead: 24
- body: 20
- annotation: 16
- footnote: 12

## icons
- library: tabler-outline
- stroke_width: 2
- inventory: tabler-outline/message-circle, tabler-outline/message, tabler-outline/microphone, tabler-outline/camera, tabler-outline/video, tabler-outline/volume, tabler-outline/headphones, tabler-outline/users, tabler-outline/users-group, tabler-outline/school, tabler-outline/building-community, tabler-outline/trophy, tabler-outline/clipboard-list, tabler-outline/clipboard-check, tabler-outline/calendar, tabler-outline/clock, tabler-outline/timeline, tabler-outline/target, tabler-outline/adjustments, tabler-outline/bell, tabler-outline/send, tabler-outline/send-2, tabler-outline/brand-wechat, tabler-outline/calendar-stats, tabler-outline/file-text, tabler-outline/file-export, tabler-outline/file-spreadsheet, tabler-outline/file-type-pdf, tabler-outline/database, tabler-outline/shield-check, tabler-outline/cpu, tabler-outline/cloud, tabler-outline/server, tabler-outline/api, tabler-outline/network, tabler-outline/stack, tabler-outline/refresh, tabler-outline/refresh-alert, tabler-outline/edit, tabler-outline/arrows-shuffle, tabler-outline/git-branch, tabler-outline/history, tabler-outline/plus, tabler-outline/check, tabler-outline/link, tabler-outline/key, tabler-outline/lock, tabler-outline/alert-circle, tabler-outline/award, tabler-outline/rocket, tabler-outline/bulb, tabler-outline/sparkles, tabler-outline/photo, tabler-outline/upload, tabler-outline/player-play, tabler-outline/terminal, tabler-outline/activity, tabler-outline/lifebuoy, tabler-outline/settings

## page_rhythm
- P01: anchor
- P02: breathing
- P03: dense
- P04: dense
- P05: dense
- P06: breathing
- P07: dense
- P08: dense
- P09: dense
- P10: dense
- P11: breathing
- P12: dense
- P13: dense
- P14: dense
- P15: dense
- P16: dense
- P17: dense
- P18: breathing
- P19: dense
- P20: dense
- P21: dense
- P22: dense
- P23: breathing
- P24: dense
- P25: dense
- P26: dense
- P27: dense
- P28: dense
- P29: anchor

## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
