# AIOpsOS — AI 运维智能操作系统

基于 LangGraph 多智能体协同的智能运维平台，实现告警分析、根因定位、知识沉淀的全链路闭环。

## 架构概览

```
┌──────────────────────────────────────────────────────────────────┐
│                前端展示层 (React 18 + TS + Vite)                    │
│  运维中心 (对话/告警/数据接入/事件接入/场景/自动化)                   │
│  AI中心 (智能体/工具/知识库/定时任务/睡眠管理/记忆管理)               │
│  控制中心 (用户/权限/渠道/模型供应商/系统/反馈/审计)                  │
│  文档中心 │ 多空间管理                                              │
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
│  记忆系统（两层级）               Worker (Celery)                  │
│  睡眠检测/自动记忆沉淀            Kafka Consumer                  │
│  模型供应商管理                   文档中心 API                      │
│  系统审计                                                         │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────┐
│          基础设施: PostgreSQL+pgvector / Redis / Kafka              │
└──────────────────────────────────────────────────────────────────┘
```

## 核心功能

### 运维中心
- **AI 对话** — LangGraph 8节点主图（init→plan→route→exec→synthesize→reflect→final_answer），4个子智能体协同，支持流式响应和 WebSocket
- **告警中心** — 6状态流转（pending→analyzing→awaiting_review→confirmed/dismissed→closed），自动分析→确认→知识入库闭环，支持批量操作和触发器引擎
- **数据接入** — Webhook / API Poller / Kafka Consumer 三种通道，支持性能指标、告警事件等多种数据类型，含归一化、去重、动态事件表
- **事件接入** — 数据源事件管理，自定义表结构映射，原始事件持久化查询
- **场景运维** — 预设运维任务模板，工具+智能体组合复用
- **自动化** — Celery Beat 定时任务 + SceneTrigger 条件规则引擎

### AI中心
- **智能体管理** — 主智能体/子智能体 CRUD，系统提示词定制，模型选择
- **工具注册** — Skill（Python函数）+ MCP（多协议客户端连接池）双协议
- **知识库** — LLM-Wiki 三层存储（raw/wiki/meta），预编译去RAG化，文件监控自动同步
- **两层级记忆** — 个人记忆（跨会话，用户隔离）+ 团队记忆（跨用户，经验提炼，自动脱敏）；支持记忆图谱可视化和跨会话检索
- **睡眠管理** — 会话空闲自动检测（>5分钟）→ 自动/手动触发记忆沉淀 → 标记已整理
- **定时任务** — Cron + RedBeat 动态调度，支持执行历史和回放

### 控制中心
- **身份与权限** — JWT + RBAC 细粒度权限
- **模型供应商** — OpenAI/Anthropic/本地部署等多模型配置管理
- **消息渠道** — 钉钉/企业微信/Webhook 多渠道通知
- **系统管理** — 全局配置、种子数据、操作审计

## 技术栈

| 层级 | 技术 |
|------|------|
| **前端** | React 18, TypeScript, Vite, Ant Design 5, Zustand, TanStack Query |
| **后端** | Python 3.11, FastAPI, SQLAlchemy 2.0 async, Alembic |
| **智能体** | LangGraph 状态图, DeepAgents 子智能体, MCP 协议 |
| **数据** | PostgreSQL 15 + pgvector, Redis 7, Kafka |
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
# 启动基础设施
docker compose -f deploy/docker-compose.dev.yml up -d

# 后端
cd server && uv run uvicorn src.main:app --reload --port 8000

# 前端
cd web && pnpm dev
```

### 镜像构建

```bash
cd deploy
./build.sh                           # 构建全部，带时间戳tag
./build.sh --version v1.0.0 --push  # 指定版本 + 推送
```

详细部署文档见 `deploy/README.md` 或平台内文档中心。

## 项目结构

```
AIOpsOS/
├── server/                     # 后端 (FastAPI)
│   ├── src/
│   │   ├── agent/              # LangGraph 智能体 (主图/子智能体/工具)
│   │   ├── api/                # API 路由 (control/execution)
│   │   ├── models/             # SQLAlchemy 数据模型
│   │   ├── services/           # 业务服务 (memory/knowledge/sleep_detector/...)
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   └── core/               # 核心配置/模型工厂/日志
│   ├── migrations/             # Alembic 数据库迁移
│   └── data/                   # 运行时数据 (知识库/Skills)
├── web/                        # 前端 (React + Vite)
│   └── src/
│       ├── features/           # 功能页面 (chat/alerts/datacenter/events/memory/sleep/...)
│       ├── components/         # 通用组件 (layout/Sidebar/MarkdownContent/...)
│       ├── stores/             # Zustand 状态管理
│       └── router/             # 路由配置
├── deploy/                     # 部署配置
│   ├── docker-compose.yml      # 生产环境
│   ├── docker-compose.dev.yml  # 开发环境
│   ├── Dockerfile.server       # 后端镜像
│   ├── Dockerfile.web          # 前端镜像
│   ├── nginx.conf              # Nginx 反向代理
│   ├── build.sh                # 镜像构建脚本
│   └── README.md               # 部署文档
├── docs/                       # 平台文档
│   ├── superpowers/specs/      # 设计规格文档
│   ├── roadmap.md              # 路线图
│   ├── user-guide/             # 用户指南
│   ├── admin-guide/            # 管理员指南
│   └── api/                    # API 文档
├── .superpowers/               # 脑暴会话产物 (不入库)
└── CLAUDE.md                   # Claude Code 项目配置
```

## 产品路线图

### v0.1.0 (当前) — MVP
- [x] JWT + RBAC 认证授权
- [x] LangGraph 智能体对话（流式 + WebSocket）
- [x] 工具管理器（Skill + MCP）
- [x] 知识库 LLM-Wiki + 文件监控自动同步
- [x] 告警中心（6状态流转 + 触发引擎 + LLM 分析）
- [x] 数据接入（Webhook/API/Kafka + 事件表映射）
- [x] 定时任务调度（Cron + RedBeat）
- [x] 通知渠道（钉钉/企微/Webhook）
- [x] 两层级记忆系统 + 记忆图谱
- [x] 睡眠管理 + 自动记忆沉淀
- [x] 模型供应商管理（OpenAI/Anthropic/本地部署）
- [x] 多空间管理
- [x] 操作审计日志
- [x] Docker Compose 部署
- [x] 深色/浅色主题
- [x] 接入引导向导

### v0.2.0 — 数据接入扩展（设计中）
- [ ] 日志接入 — PG 分区表 + 30min 窗口 + Agent 检索
- [ ] ITSM 流程接入 — API Poller request_chain + 告警关联
- [ ] CMDB 配置接入 — 属性图 + LLM 发现 + 规则执行
- [ ] CmdbIngestionAgent 智能体 — 自动映射规则生成 + 三层校验
- [x] Kafka 告警消费与去重
- [x] 触发规则引擎
- [x] 告警→知识库自动闭环
- [ ] 告警时间线视图
- [ ] 告警聚合分组

### v0.3.0 — 人工介入
- [ ] human_interrupt 挂起/恢复
- [ ] 安全确认卡片
- [ ] 参数收集表单
- [ ] 多渠道回调

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
