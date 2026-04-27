# 产品路线图

## 当前版本 v0.1.0 (MVP)

- [x] JWT 认证与 RBAC 权限系统
- [x] 智能体对话（LangGraph 8节点主图 + 4个子智能体）
- [x] 流式对话（SSE）与 WebSocket
- [x] 工具管理器（Skill + MCP 双协议）
- [x] 知识库 LLM-Wiki（三层存储：raw/wiki/meta）
- [x] 告警中心（状态机：pending→analyzing→awaiting_review→confirmed/dismissed→closed）
- [x] 定时任务调度（Celery Beat + RedBeat）
- [x] 通知渠道（钉钉/企业微信/Webhook）
- [x] 两级记忆系统（个人记忆 + 团队记忆，LLM 自动抽取）
- [x] Docker Compose 一键部署
- [x] 深色/浅色主题

## v0.2.0 — 告警闭环增强

- [ ] Kafka 告警事件消费与去重
- [ ] 告警自动分析触发规则引擎
- [ ] 告警确认 → 知识入库闭环
- [ ] 前端告警中心时间线视图
- [ ] 告警聚合分组（同类型/同主机）

## v0.3.0 — 人工介入与协作

- [ ] human_interrupt 节点（审批/表单挂起恢复）
- [ ] 安全确认卡片（风险等级、代码预览、影响范围）
- [ ] 参数收集表单（JSON Schema 动态生成）
- [ ] 多渠道回调统一入口
- [ ] 会话中断恢复

## v0.4.0 — 客户端 Agent MVP

- [ ] Go 客户端基础框架（mTLS WebSocket）
- [ ] Docker/Wasm 沙箱执行
- [ ] 自治规则引擎
- [ ] 指标采集器（CPU/内存/磁盘/网络）
- [ ] Agent Profile 管理与配置下发

## v0.5.0 — 企业特性

- [ ] 多租户组织隔离
- [ ] LDAP 集成
- [ ] 能力级权限（sub_agent:use / skill:use / mcp:use）
- [ ] 个人助理自定义配置
- [ ] 企业品牌设置

## v1.0.0 — 生产就绪

- [ ] 端到端自动化测试
- [ ] 性能压测（对话并发 100+ / 告警吞吐 1000+/min）
- [ ] 管控分离部署模式
- [ ] Ansible 批量部署客户端 Agent
- [ ] 用户手册与管理手册
- [ ] OpenAPI 文档完善
- [ ] 监控指标导出（Prometheus endpoint）
