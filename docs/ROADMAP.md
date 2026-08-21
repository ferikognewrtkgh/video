# MangaFlow Studio 路线图

本路线以《MangaFlow 业务级 AI 漫剧平台方案》为产品基线。目标不再是扩展“一句话生成视频”演示，而是按可验证的业务闭环建设多人协作、可恢复、可审计的 AI 漫剧生产平台。

## 路线原则

1. 先完成一条真实的短片垂直链路，再扩展模型与页面数量。
2. 生成是异步业务任务；幂等、UNKNOWN 对账、成本封顶和状态时间线属于主流程。
3. Asset Bible 与 ShotSpec 是事实源；资产更新必须产生新版本并显式使下游产物失效。
4. 自动质检只提供证据和建议，关键帧、返修和交付保留人工审核节点。
5. Mock、规则指标、真实媒体实测和真实供应商结果必须在 API、界面和报告中明确区分。

## 能力状态（2026-08-21）

| 能力 | 状态 | 验证边界 |
| --- | --- | --- |
| TXT / Markdown / DOCX / 结构化剧本 JSON 导入 | 已实现 | 本地安全解析、规范化、长文本重叠切分、哈希；尚未接对象存储 |
| 输入驱动结构化改编 | 工程实现 | Pydantic 严格结构与连续性门禁；默认是确定性离线模型，不是生产 LLM |
| Organization / Membership / RBAC | 已实现 | 严格模式要求身份头，角色由服务端 Membership 查询；跨租户返回 404 |
| Project 预算、截止时间与乐观锁 | 已实现 | 提交前成本封顶；JSON Repository 支持版本冲突检测 |
| Asset 不可变版本与来源/许可 | 已实现 | 新版本保留父版本、来源与许可字段，并传播 stale |
| 依赖图与影响分析 | 已实现 | 资产到资产/镜头的传递失效；尚未迁移图数据库 |
| 审批、评论、操作审计 | 已实现 | 核心写操作保留 actor、target、detail 和时间 |
| 生成运行时状态机 | 已实现 | CREATED/QUEUED/SUBMITTING/SUBMITTED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN 时间线 |
| Provider 契约 | 已实现 | capabilities、estimate、normalize_error、health、webhook 验签、幂等恢复；真实 SLA 未验证 |
| 媒体硬质检 | 已实现 | OpenCV 实际解码、时长、分辨率、FPS、黑帧、闪烁、参考图像素代理；无 CLIP/DINO/VLM |
| Storyboard CSV 交换 | 已实现 | 全量校验、原子导入、乐观锁；Excel 文件格式尚未实现 |
| 效率指标与交付 Manifest | 已实现 | 使用运营记录计算；本地媒体含 SHA-256/大小，缺件时阻断，绝不伪造 MP4 |
| 真实媒体收敛 | 已实现 | 上传或 Provider 输出经过白名单下载、格式校验、SHA-256 与 OpenCV 实测；仓库尚无真实供应商样例 |
| TTS / 字幕 / FFmpeg 成片 | 工程实现 | 已实现逐镜 TTS、SRT 时间轴、FFmpeg 合成和 ffprobe 三轨门禁；当前环境未配置真实 TTS 且未安装 FFmpeg |
| PostgreSQL / Redis / MinIO / Temporal | 规划中 | 当前仍为模块化单体与原子 JSON Checkpoint |

## Sprint 1：真实垂直切片（最高优先级）

验收目标：输入一个 3000–5000 字故事，产出一条可播放的 20–60 秒 MP4，过程包含可选关键帧、真实 I2V、TTS、字幕和 FFmpeg 合成。

- 接入生产结构化 LLM Adapter，保存模型、模板版本、输入输出哈希和失败样本。
- 在受控环境配置至少一个真实图像 Provider 和一个真实 I2V Provider；2–4 关键帧候选、轮询与人工选择代码已完成。
- 在受控环境配置真实 TTS；逐镜音轨与 SRT 时间轴代码已完成。
- 使用真实素材验收 FFmpeg 装配、转码、三轨验证、交付 Manifest 和可下载成片；CI 已配置真实 FFmpeg 集成测试。
- 建立 10 个故事、60–80 个镜头的真实回归集；规则路由结果不能替代真实质量评测。

退出条件：CI 中继续使用 Provider 合同测试；受控验收环境运行一套真实端到端样例，并保存成本、耗时、任务 ID 与媒体质检证据。

## Sprint 2：可靠运行时与基础设施

- Repository 接口落 PostgreSQL，Checkpoint 迁移工具支持回滚与校验。
- 媒体进入 MinIO/S3，使用签名 URL、内容类型、大小与校验和验证。
- Redis 用于短期队列与限流，Temporal 编排长任务、补偿、重试和人工等待。
- Provider Registry 使用能力、可用性、截止时间、成本与质量预算做路由。
- 完成 webhook 收敛、定时 reconcile、UNKNOWN 告警、失败分级和死信处理。

退出条件：进程重启、重复 webhook、Provider 超时、网络分区和 worker 崩溃均不会重复扣费或丢失任务。

## Sprint 3：业务协作

- 组织邀请、成员管理、项目级角色覆盖、服务账号和 API Token。
- 分镜批量编辑、A/B 对比、负责人、评论线程、审核流和变更时间线。
- 资产锁定策略、依赖影响预览、失效产物批量重算和冲突解决。
- CSV 完善并增加 XLSX 模板导入导出。
- 关键管理接口增加限流、CSRF/Token 策略和安全审计导出。

退出条件：制片、编剧、分镜师、美术、审核与运营可在同一组织内完成交接，且所有关键变更可追溯。

## Sprint 4：质量与效率优化

- 接入身份/人脸 Embedding、CLIP/DINO、光流、唇形同步与 VLM 软评审。
- 采集人工接受/拒绝反馈，评估路由、Prompt 模板和自动修复策略。
- 建立首稿时间、镜头一次通过率、返工率、单位成片成本、Provider 成功率仪表盘。
- 支持批量实验与模板版本对照，质量提升必须带统计证据。

退出条件：质量或成本优化必须在固定回归集和真实生产数据上显著优于基线，且无租户隔离与交付回归。

## 暂不承诺

- 未配置 Provider 凭据时，不宣称已完成真实图像或视频生成。
- 未生成并解码真实媒体时，不展示“实测质量分”。
- 未安装并调用 FFmpeg/TTS 时，不宣称已产出成片。
- JSON Repository、规则 Router 和确定性改编器不会被描述为 PostgreSQL、学习型路由或生产 LLM。
