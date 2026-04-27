# AIOpsOS — AI 运维智能操作系统

基于 LangGraph 多智能体协同的智能运维平台，实现告警分析、根因定位、知识沉淀的全链路闭环。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│               前端展示层 (React 18 + TS + Vite)                │
│  运维中心 (对话/告警/场景/自动化) │ AI中心 (智能体/工具/知识库) │
│  控制中心 (权限/渠道/系统)       │ 文档中心                     │
└──────────────────────────┬──────────────────────────────────┘
                   HTTPS   │
┌──────────────────────────┼──────────────────────────────────┐
│                   Nginx 网关 (路径分流)                       │
│        /api/* → server:8000      / → SPA                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   AIOpsOS Server (FastAPI)                    │
│  Control Plane           Execution Plane                     │
│  用户/权限/RBAC           对话/智能体/流式                     │
│  场景/工具CRUD            告警/执行                           │
│  知识库管理               通知/渠道回调                       │
│  记忆系统（两级）          Worker (Celery)                    │
│  文档中心 API             Kafka Consumer                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│         基础设施: PostgreSQL+pgvector / Redis / Kafka          │
└─────────────────────────────────────────────────────────────┘
```

## 核心功能

### 运维中心
- **AI 对话** — LangGraph 8节点主图（init→plan→route→exec→synthesize→reflect→final_answer），4个子智能体协同
- **告警中心** — 6状态流转（pending→analyzing→awaiting_review→confirmed/dismissed→closed），自动分析→确认→知识入库闭环
- **场景运维** — 预设运维任务模板，工具+智能体组合复用
- **自动化** — Celery Beat 定时任务 + 触发规则引擎

### AI中心
- **智能体管理** — 主智能体/子智能体 CRUD，系统提示词定制
- **工具注册** — Skill（Python函数）+ MCP（多协议客户端连接池）双协议
- **知识库** — LLM-Wiki 三层存储（raw/wiki/meta），预编译去RAG化
- **两级记忆** — 个人记忆（跨会话，用户隔离）+ 团队记忆（跨用户，经验提炼，自动脱敏）
- **定时任务** — Cron + RedBeat 动态调度

### 控制中心
- **身份与权限** — JWT + RBAC 细粒度权限
- **消息渠道** — 钉钉/企业微信/Webhook 多渠道通知
- **系统管理** — 全局配置、种子数据

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
│   │   ├── services/           # 业务服务 (memory/knowledge/tool_manager)
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   └── core/               # 核心配置/日志
│   ├── migrations/             # Alembic 数据库迁移
│   └── data/                   # 运行时数据 (知识库/Skills)
├── web/                        # 前端 (React + Vite)
│   └── src/
│       ├── features/           # 功能页面 (chat/alerts/knowledge/memory/docs...)
│       ├── components/         # 通用组件 (layout/Sidebar/Header/MarkdownContent)
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
└── docs/                       # 平台文档（嵌入文档中心）
    ├── roadmap.md              # 路线图
    ├── user-guide/             # 用户指南
    ├── admin-guide/            # 管理员指南
    └── api/                    # API 文档
```

## 产品路线图

### v0.1.0 (当前) — MVP
- [x] JWT + RBAC 认证授权
- [x] LangGraph 智能体对话（流式 + WebSocket）
- [x] 工具管理器（Skill + MCP）
- [x] 知识库 LLM-Wiki
- [x] 告警中心状态机
- [x] 定时任务调度
- [x] 通知渠道（钉钉/企微/Webhook）
- [x] 两级记忆系统
- [x] Docker Compose 部署
- [x] 深色/浅色主题

### v0.2.0 — 告警闭环增强
- [ ] Kafka 告警消费与去重
- [ ] 触发规则引擎
- [ ] 告警→知识库自动闭环
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

### v1.0.0 — 生产就绪
- [ ] 端到端测试 + 性能压测
- [ ] 管控分离部署
- [ ] Ansible 批量部署
- [ ] Prometheus 监控导出

详见 `docs/roadmap.md` 或平台文档中心。

## 许可证

MIT
