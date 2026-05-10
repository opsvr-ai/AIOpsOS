# AIOpsOS — AI 运维智能操作系统

基于 LangGraph 多智能体协同的智能运维平台，实现告警分析、根因定位、知识沉淀、流程自动化的全链路闭环。

## 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                前端展示层 (React 18 + TypeScript + Vite)            │
│  运维中心 (总览/对话/告警/场景/数据接入/事件/自动化/CMDB/            │
│           日志检索/ITSM/分析报告/定时任务)                           │
│  AI中心 (智能体/工具市场/知识库/睡眠管理/记忆管理/我的助理)           │
│  控制中心 (空间/渠道/模型/权限/用户/系统/日志/分析/演进/特性开关)     │
│  文档中心 │ 多空间管理 │ 深色/浅色主题                                │
└───────────────────────────┬──────────────────────────────────────┘
                    HTTPS   │
┌───────────────────────────┼──────────────────────────────────────┐
│                      Nginx 网关 (路径分流)                         │
│           /api/* → server:8000      / → SPA                       │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│                    AIOpsOS Server (FastAPI)                       │
│  Control Plane                   Execution Plane                  │
│  用户/权限/RBAC                  对话/智能体/流式                   │
│  场景/工具 CRUD                  告警/执行                         │
│  知识库管理                      通知/渠道回调                     │
│  记忆系统（两层级）               Worker (Celery — 独立服务)         │
│  睡眠管理/自动记忆沉淀            Kafka Consumer / Admin           │
│  模型供应商管理                   文档中心 API                      │
│  分析报表 / PDF 导出             日志/ITSM/CMDB 检索                │
│  演进管线 (工具版本管理)          运行时特性开关                      │
│  可观测性 (OpenTelemetry/Prom)   系统审计                           │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│          基础设施: PostgreSQL+pgvector / Redis / Kafka              │
└──────────────────────────────────────────────────────────────────┘
```

## 核心功能

### 运维中心

| 模块 | 功能 |
|------|------|
| **AI 对话** | LangGraph 8 节点主图（init→plan→route→exec→synthesize→reflect→final_answer），4 个子智能体协同（知识/记忆/监控/CMDB），流式 SSE 响应，上下文文件上传与文件夹管理 |
| **告警中心** | 6 状态流转（pending→analyzing→awaiting_review→confirmed/dismissed→closed），LLM 自动分析→确认→知识库闭环，批量操作，触发器条件规则引擎 |
| **数据接入** | Webhook / API Poller / Kafka Consumer 三种通道，支持性能指标、告警事件、日志、ITSM、CMDB 等多种数据类型，归一化、去重、动态事件表映射 |
| **事件接入** | 自定义表结构映射，原始事件持久化查询，动态 schema 管理 |
| **场景运维** | 多场景类型（命令式/自然语言式/混合式），标准化模板（故障定界/健康巡检/容量预测/告警分析），增强触发规则（趋势检测/组合条件/频率限制），资源关联（技能/智能体/知识库/消息渠道） |
| **应急协同** | 场景触发自动创建协同会话，企业微信群自动创建与消息同步，邮件通知，LLM 驱动进度分析，智能下一步建议，协同会话全生命周期管理 |
| **自动化** | Celery Beat 定时调度 + 触发规则引擎，Cron 表达式 + 频率限制 + 时间窗口，执行历史回放 |
| **CMDB** | 配置节点管理，属性图存储，LLM 自动发现映射规则，审核队列与同步日志 |
| **日志检索** | PG 分区表存储，Agent 智能检索，时间范围过滤，全文搜索 |
| **ITSM** | 工单流程接入，Jira/ServiceNow/自定义脚本适配器，告警关联与工作流引擎 |
| **分析报告** | 智能体对话生成 HTML 报告，三级可见性（私有/空间/公开），公开 URL 直接访问，报告管理页，LLM 驱动的 PPT 风格 PDF 导出（WeasyPrint），报告类型与日期范围元数据 |
| **定时任务** | Cron + RedBeat 动态调度，内置运维场景任务模板，超时/重试机制 |

### AI 中心

| 模块 | 功能 |
|------|------|
| **智能体管理** | 主智能体 + 子智能体 CRUD，系统提示词定制，模型选择，子智能体关联配置 |
| **工具市场** | Skill（Python 函数）+ MCP（多协议客户端连接池）双协议，MCP 服务器管理（CRUD），技能文件上传、AI 生成、演进管线版本管理与同步 |
| **知识库** | LLM-Wiki 三层存储（raw/wiki/meta），预编译去 RAG 化，文件监控自动同步，跨引用检测 |
| **睡眠管理** | 会话空闲自动检测（>5 分钟），自动/手动触发记忆沉淀，标记已整理状态 |
| **记忆管理** | 两层级记忆：个人记忆（跨会话，用户隔离）+ 团队记忆（跨用户，经验提炼，自动脱敏），记忆图谱可视化，跨会话检索，Celery 后台异步沉淀 |
| **我的助理** | 侧边抽屉式快捷助手，快速问答与操作入口 |

### 控制中心（管理员）

| 模块 | 功能 |
|------|------|
| **空间管理** | 多空间 CRUD，成员管理，空间级数据隔离 |
| **消息渠道** | 企业微信/钉钉/Webhook/Email 多渠道通知，Agent Profile 绑定 |
| **模型配置** | OpenAI/Anthropic/本地部署等多模型供应商管理，模型类型与参数配置 |
| **权限矩阵** | RBAC 角色权限（admin/user），资源级操作权限控制 |
| **用户管理** | 用户 CRUD，角色分配，邀请注册 |
| **系统管理** | 全局配置，种子数据，品牌定制 |
| **分析中心** | 运营分析仪表盘（概览卡片 + 趋势图表 + Top 排行），LLM 多步分析 + PPT 风格 PDF 报告导出 |
| **演进管线** | 工具/Skill 版本全生命周期管理（Draft → Review → Active → Archived + Rollback），版本差异对比 |
| **Kafka 管理** | Topic CRUD、Schema Registry、消费者组监控、消息浏览、健康检查 |
| **运行时特性** | 动态特性开关，渐进式发布，安全回滚 |
| **日志查看** | 在线日志文件浏览，级别/模块/关键词过滤，JSON/文本双格式支持 |

### 平台特性

- **多空间** — 空间级数据隔离，空间选择器，角色感知导航
- **认证授权** — JWT + RBAC，RequireAuth/RequireAdmin 组件守卫
- **深色/浅色主题** — 全局主题切换，持久化偏好
- **文档中心** — 内置用户指南、管理员指南、API 文档、部署文档
- **引导向导** — 首次使用接入引导
- **可观测性** — OpenTelemetry 分布式追踪（OTLP），Prometheus 指标导出
- **演进管线** — 工具/Skill 版本化发布，Draft → Review → Active 生命周期
- **运行时特性开关** — 渐进式功能发布，安全回滚
- **操作审计** — 关键操作日志记录
- **用户反馈** — 反馈收集与管理

## 技术栈

| 层级 | 技术 |
|------|------|
| **前端** | React 18, TypeScript, Vite, Ant Design 5, Zustand, React Router 6 |
| **后端** | Python 3.11, FastAPI, SQLAlchemy 2.0 (async), Alembic |
| **智能体** | LangGraph 状态图, LangChain, DeepAgents 子智能体, MCP 协议 |
| **任务队列** | Celery + Redis (Beat 定时调度, 独立 worker 服务) |
| **消息流** | Kafka (事件消费与流处理, aiokafka 管理 API) |
| **数据** | PostgreSQL 15 + pgvector (向量检索), Redis 7 |
| **可观测性** | OpenTelemetry (分布式追踪), Prometheus (指标) |
| **部署** | Docker, Docker Compose, Nginx, Poetry, pnpm |

## 快速开始

### Docker Compose 部署（推荐）

```bash
cd deploy
cp .env.example .env
vim .env   # 配置 LLM_API_KEY
docker compose up -d
```

访问：
- 前端: `http://localhost`
- API 文档: `http://localhost:8000/docs`
- 默认账号: `admin` / `admin123`

### 开发模式

```bash
# 一键启动（Windows / Linux 均提供脚本）
cd scripts
# Windows: .\start-all.ps1
# Linux:   ./start-all.sh

# 或手动启动各服务
docker compose -f deploy/docker-compose.dev.yml up -d

# 后端
cd server
cp .env.example .env  # 配置 LLM_API_KEY
poetry run python run_server.py

# 前端
cd web
pnpm dev
```

访问：
- 前端: `http://localhost:5173`
- API 文档: `http://localhost:8000/docs`

### 环境变量

后端主配置见 `server/.env`：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | 异步数据库连接 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 连接 |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka 地址 |
| `LLM_API_KEY` | — | 大模型 API Key |
| `LLM_BASE_URL` | — | 大模型 API 地址 |
| `LOG_LEVEL` | `DEBUG` | 日志级别 |
| `LOG_DIR` | `data/logs` | 日志存放目录 |
| `LOG_FORMAT` | `text` | 日志格式 (text/json) |
| `LOG_RETENTION_DAYS` | `30` | 日志保留天数 |
| `SERVICE_TYPE` | `server` | 服务模式（server/worker/allinone） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry 导出端点 |
| `PROMETHEUS_PORT` | `9090` | Prometheus 指标端口 |
| `CELERY_BROKER_URL` | — | Celery Broker 连接串 |
| `CELERY_RESULT_BACKEND` | — | Celery 结果后端 |
| `SYNC_DATABASE_URL` | — | 同步数据库连接（Celery Worker 用） |

### 镜像构建

```bash
cd deploy
./build.sh                           # 构建全部，带时间戳tag
./build.sh --version v1.0.0 --push  # 指定版本 + 推送
```

## 项目结构

```
AIOpsOS/
├── server/                        # 后端 (FastAPI)
│   ├── src/
│   │   ├── agent/                 # LangGraph 智能体 (主图/子智能体/工具/nodes)
│   │   ├── api/
│   │   │   ├── control/           # 管理面 API (CRUD/配置)
│   │   │   └── execution/         # 执行面 API (对话/流式/回调/webhook)
│   │   ├── models/                # SQLAlchemy ORM 数据模型
│   │   ├── schemas/               # Pydantic 请求/响应模型
│   │   ├── services/              # 业务服务 (memory/knowledge/cron/channels/...)
│   │   ├── consumers/             # Kafka 消费者 (告警/归一化/去重)
│   │   ├── workers/               # Celery Worker 应用 (memory/wiki/evolution 队列)
│   │   └── core/                  # 核心配置/模型工厂/日志/Redis
│   ├── migrations/                # Alembic 数据库迁移
│   ├── scripts/                   # 运维脚本 (seed/eval/evolution/rollout/backfill)
│   ├── tests/                     # 测试
│   └── data/                      # 运行时数据 (知识库/Skills/日志/报告)
├── web/                           # 前端 (React + Vite)
│   └── src/
│       ├── features/              # 功能页面 (chat/alerts/datacenter/...)
│       ├── components/            # 通用组件 (layout/Sidebar/auth/...)
│       ├── stores/                # Zustand 状态管理
│       ├── services/              # API 客户端
│       └── router/                # 路由配置
├── deploy/                        # 部署配置
│   ├── docker-compose.yml         # 生产环境 (server + worker + web)
│   ├── docker-compose.dev.yml     # 开发环境
│   ├── Dockerfile.server          # 后端镜像 (API + Worker 共用)
│   ├── Dockerfile.web             # 前端镜像
│   ├── entrypoint-server.sh       # 服务入口脚本 (alembic + uvicorn)
│   ├── nginx.conf                 # Nginx 反向代理
│   ├── build.sh                   # 镜像构建脚本
│   └── README.md                  # 部署文档
├── scripts/                       # 开发环境启停脚本
│   ├── start-all.ps1 / start-all.sh
│   ├── start-backend.ps1 / start-backend.sh
│   └── start-frontend.ps1 / start-frontend.sh
├── docs/                          # 平台文档
│   ├── superpowers/specs/         # 设计规格文档
│   ├── roadmap.md                 # 路线图
│   ├── user-guide/                # 用户指南
│   ├── admin-guide/               # 管理员指南
│   └── api/                       # API 文档
└── CLAUDE.md                      # Claude Code 项目配置
```

## 产品路线图

### v0.1.0 (已完成)
- [x] JWT + RBAC 认证授权
- [x] LangGraph 智能体对话（流式 SSE）
- [x] 工具市场（Skill + MCP + MCP 服务器管理）
- [x] 知识库 LLM-Wiki + 文件监控自动同步
- [x] 告警中心（6 状态流转 + 触发引擎 + LLM 分析 + 知识库闭环）
- [x] 数据接入（Webhook/API/Kafka + 归一化去重 + 动态事件表）
- [x] 事件接入（自定义表结构映射 + 原始事件持久化查询）
- [x] 场景运维（预设任务模板 + 工具智能体组合）
- [x] 自动化（定时调度 + 触发规则引擎）
- [x] CMDB（配置节点管理 + 审核队列 + 同步日志）
- [x] 日志检索（Agent 智能检索 + 分区表存储）
- [x] ITSM（Jira/ServiceNow/脚本适配器 + 工作流引擎）
- [x] 分析报告（HTML 生成 + 三级可见性 + 公开 URL + 报告管理）
- [x] 通知渠道（企微/钉钉/Webhook/Email）
- [x] 两层级记忆系统 + 记忆图谱可视化
- [x] 睡眠管理 + 自动记忆沉淀
- [x] 模型供应商管理（OpenAI/Anthropic/本地部署）
- [x] 多空间管理 + 空间级数据隔离
- [x] 我的助理
- [x] 文档中心
- [x] 操作审计日志
- [x] 在线日志查看
- [x] 用户反馈收集
- [x] Docker Compose 部署 (server + worker + web)
- [x] 深色/浅色主题
- [x] 接入引导向导
- [x] 日志集中管理（data/logs，.env 控制级别）
- [x] 运营分析仪表盘 + LLM 驱动 PDF 报告导出
- [x] 演进管线 (工具/Skill 版本管理)
- [x] Kafka 管理 API (Topic CRUD + Schema Registry)
- [x] 运行时特性开关
- [x] OpenTelemetry 分布式追踪 + Prometheus 指标
- [x] Celery 独立 Worker 服务 (memory/wiki/evolution 队列)
- [x] 开发环境启停脚本 (Windows + Linux)

### v0.2.0 — 场景运维优化与应急协同 (已完成)
- [x] 场景类型系统（命令式/自然语言式/混合式）
- [x] 标准化场景模板（故障定界/健康巡检/容量预测/告警分析）
- [x] 增强触发规则（趋势检测/组合条件/频率限制/时间窗口）
- [x] 场景资源关联（技能/智能体/知识库/消息渠道）
- [x] 场景执行引擎（超时处理/日志记录/结果生成）
- [x] 应急协同工作流（自动创建协同会话/状态流转/总结报告）
- [x] 群聊管理（企业微信群自动创建/消息收发/成员管理）
- [x] 邮件通知（模板渲染/状态更新/重试机制）
- [x] 消息同步（双向同步/格式转换/去重）
- [x] 进度分析（LLM 驱动/关键事件识别/进度摘要）
- [x] 下一步建议（知识库集成/优先级/反馈学习）
- [x] 协同会话管理（查询/筛选/搜索/导出）
- [x] 场景执行与协同会话集成（自动创建协同会话）
- [x] 消息同步集成（GroupChatManager + MessageSyncService）
- [x] 进度分析与建议集成（分析后自动生成建议）

### v0.3.0 — 人工介入
- [ ] human_interrupt 挂起/恢复
- [ ] 安全确认卡片
- [ ] 参数收集表单
- [ ] 多渠道回调
- [ ] 告警时间线视图
- [ ] 告警聚合分组

### v0.3.0 — 人工介入
- [ ] Go 客户端 mTLS WebSocket
- [ ] Docker/Wasm 沙箱
- [ ] 自治规则引擎
- [ ] Agent Profile 管理

### v0.4.0 — 客户端 Agent
- [ ] Go 客户端 mTLS WebSocket
- [ ] Docker/Wasm 沙箱
- [ ] 自治规则引擎
- [ ] Agent Profile 管理

### v0.5.0 — 企业特性
- [ ] 多租户隔离
- [ ] LDAP 集成
- [ ] 能力级权限
- [ ] 容量预测/管理

### v1.0.0 — 生产就绪
- [ ] 端到端测试 + 性能压测
- [ ] 管控分离部署
- [ ] Ansible 批量部署
- [ ] Prometheus 监控导出

详见 `docs/roadmap.md` 或平台文档中心。

## 设计文档

- [数据接入架构设计](docs/superpowers/specs/2026-05-01-data-ingestion-design.md) — 五通道统一采集管道，含 CMDB/日志/ITSM 接入及 CmdbIngestionAgent 设计

## 许可证

MIT
