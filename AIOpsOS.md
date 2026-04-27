---

## 一、整体架构分析
### 1.1 架构全景
```plain
┌─────────────────────────────────────────────────────────────────────┐
│                    前端展示层 (React 18 + TS + Vite)                   │
│    运维中心 (告警/对话/场景/自动化) │ AI中心 (智能体/工具/知识库) │ 控制中心 (权限/渠道/系统) │
└──────────────────────────────────┬──────────────────────────────────┘
                 HTTPS/WSS        │
┌──────────────────────┼──────────────────────────────────────────────┐
│              Nginx 网关 (路径分流)                                    │
│     /api/control/* → control        /api/execution/* → execution     │
└────────────┬──────────┬──────────────┬───────────────┬──────────────┘
    ┌────────▼─────┐ ┌──▼───────────┐ ┌▼──────────┐ ┌─▼─────────────┐
    │ Control Plane│ │Execution Plane│ │  Worker   │ │ Kafka Consumer│
    │ (FastAPI)    │ │ (FastAPI)     │ │ (Celery)  │ │               │
    │ 用户/权限/RBAC│ │ 对话/智能体   │ │ 定时任务   │ │ 事件消费       │
    │ 场景/工具CRUD │ │ 告警/执行     │ │ RedBeat   │ │ 去重/分组      │
    │ 知识库管理    │ │ 流式/WebSocket│ │            │ │               │
    │ 客户端Profile │ │ 通知/渠道回调 │ │            │ │               │
    └──────┬───────┘ └──────┬───────┘ └────────────┘ └───────────────┘
           │                │
           └───────┬────────┘
                   │  共享: PostgreSQL+pgvector / Redis / Kafka
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│                    客户端 Agent (边缘执行)                             │
│  mTLS通讯 │ 沙箱执行(Docker/Wasm) │ 数据采集 │ 自治规则引擎 │ 自更新  │
│  物理机/虚拟机/网络设备/存储系统 统一代理                               │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心架构决策
| 维度 | 决策 | 依据 |
| --- | --- | --- |
| **管控分离** | 同一代码基、多入口启动(`main_control.py` / `main_execution.py`)，共享模型与核心库 | 安全隔离 + 独立扩展 + 单体兼容模式回退 |
| **智能体框架** | LangGraph 状态图，8节点主流程 `init→plan→route→exec_task→synthesize→human_interrupt→reflect→evolve→final_answer` | 有状态、可中断恢复、支持人工介入 |
| **工具体系** | 统一 ToolManager 管理 Skill + MCP，热加载、缓存、限流 | 插件化扩展，动态注册 |
| **知识库** | LLM-Wiki 三层(raw/wiki/schema) + Hermes 自进化引擎，预编译去RAG化 | 秒级全文检索，闭环沉淀 |
| **通信** | 前端↔后端 HTTPS/WSS；服务端↔Agent mTLS+WebSocket；事件 Kafka | 安全与解耦 |
| **部署** | all-in-one (MVP) / 管控分离 (生产) 双模式 Docker Compose | 渐进式交付 |


### 1.3 模块依赖关系
```plain
项目初始化 (Phase 0)
    ├── 认证授权 RBAC ─────────────────────────────────────────────────┐
    ├── 工具管理器 ToolManager ─── 智能体引擎 LangGraph ──── 对话API ──┤
    │                                    │                            │
    │                          ┌─────────┼─────────┐                  │
    │                          ▼         ▼         ▼                  │
    │                    人工介入    告警中心   定时/触发规则           │
    │                                     │                           │
    │                                     ▼                           │
    │                               知识库 LLM-Wiki                    │
    │                                                                 │
    └── 前端框架 ──── 对话核心组件 ──── 各功能页面 ─────────────────────┘
                                                      │
                                         客户端 Agent (并行开发)
```

---

## 二、开发 Task List
### Phase 0：项目初始化与环境搭建 (Week 1)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 0.1 | 初始化后端项目 (Poetry, FastAPI, SQLAlchemy 2.0 async, Alembic, Celery) | P0 | 4h |
| 0.2 | 初始化前端项目 (Vite 5 + React 18 + TS + Ant Design 5 + Zustand + TanStack Query) | P0 | 4h |
| 0.3 | 编写开发环境 Docker Compose (PostgreSQL 15+pgvector, Redis 7, Kafka) | P0 | 3h |
| 0.4 | 创建数据库迁移脚本 (13张核心表: users, roles, permissions, role_permissions, user_roles, agents, tools, mcp_servers, scenarios, scenario_tools, scenario_agents, schedules, schedule_executions, scene_triggers, alerts, sessions, messages, memories, notification_channels, user_channels, agent_profiles) | P0 | 6h |
| 0.5 | 实现基础 JWT 认证 (登录/注册/刷新令牌) + RBAC 权限中间件 | P0 | 6h |
| 0.6 | 创建项目完整目录结构 (按 `项目目录规划.txt` 标准) | P0 | 2h |
| 0.7 | 配置 CI 流水线 (GitHub Actions: lint, test) + ESLint/Prettier/Black/Ruff | P1 | 3h |
| 0.8 | 前端搭建：路由框架、MainLayout(侧边栏+顶栏+内容区)、深色主题token、i18n基础 | P0 | 5h |
| 0.9 | 编写 `.env.example` 和配置管理模块 (`config.py` based on Pydantic Settings) | P0 | 2h |


### Phase 1：核心智能体调用链路 (Week 2-3)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 1.1 | **ToolManager**：实现工具注册表 (从DB加载Skill/MCP、封装LangChain Tool、热加载接口) | P0 | 8h |
| 1.2 | **MCP Connector**：MCP 客户端连接池 (stdio/SSE传输)、工具自动发现与缓存、健康检查 | P1 | 6h |
| 1.3 | **MemoryCore**：基于 pgvector 的长期记忆存储与检索 (`memories` 表，cosine相似度搜索) | P1 | 5h |
| 1.4 | **LangGraph 主图**：实现8个节点完整流程 (`graph.py`, `state.py`, 全部 nodes/) | P0 | 12h |
| 1.5 | **子智能体工厂**：告警分析 Agent (ReAct模式)、根因定界 Agent | P0 | 8h |
| 1.6 | **场景服务**：场景模型 CRUD + 场景-工具/子智能体关联 + 场景加载器 | P0 | 6h |
| 1.7 | **对话 API**：`POST /api/v1/chat`（同步版）、`GET /api/v1/sessions`、消息持久化 | P0 | 8h |
| 1.8 | **流式对话**：WebSocket `/ws/chat/{session_id}`、事件推送（intent/plan/exec/final） | P0 | 8h |
| 1.9 | **前端对话页面基础版**：ChatContainer, ChatMessageList (react-virtuoso), 消息发送、文本回复 | P0 | 10h |
| 1.10 | **前端意图卡片+规划卡片**：IntentCard, PlanCard (状态图标、展开详情) | P0 | 6h |
| 1.11 | Mock Prometheus MCP 工具 + Mock Skill，端到端连通验证 | P0 | 4h |


### Phase 2：告警中心与自动化基础 (Week 4)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 2.1 | **告警数据模型**：`alerts` 表 + 状态机 (pending→analyzing→awaiting_review→confirmed/dismissed→closed) | P0 | 4h |
| 2.2 | **Kafka Consumer**：消费告警事件、标准化为 OpsEvent、去重与分组 | P0 | 8h |
| 2.3 | **告警自动分析**：匹配触发规则 → 自动调用智能体分析 → 结果回写 `analysis_result` | P0 | 8h |
| 2.4 | **告警 API**：列表查询(分页/筛选/排序)、详情、手动分析、确认/驳回、批量操作 | P0 | 6h |
| 2.5 | **前端告警中心页面**：列表卡片(状态指示灯)、内联展开详情、操作按钮、批量操作 | P0 | 8h |
| 2.6 | **定时任务调度**：Celery Beat + RedBeat 动态管理 Cron、CRUD API | P0 | 8h |
| 2.7 | **触发规则引擎**：条件匹配器(简单模式+表达式模式)、关联场景、频率控制 | P1 | 8h |
| 2.8 | **前端自动化页面**：定时任务列表/编辑、CronBuilder 可视化、触发规则列表/编辑 | P1 | 6h |


### Phase 3：人工介入与通知 (Week 5)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 3.1 | **human_interrupt_node**：基于干预类型(approval/form)挂起会话、InterruptManager | P0 | 8h |
| 3.2 | **安全确认卡片**：前端 SecurityConfirmCard (风险等级、代码预览、影响范围、确认/拒绝) | P0 | 6h |
| 3.3 | **参数收集表单**：前端 InlineParameterForm (JSON Schema 动态生成、@rjsf/antd) | P0 | 6h |
| 3.4 | **通知渠道插件框架**：NotificationChannel 抽象基类、渠道管理器(注册/路由) | P0 | 6h |
| 3.5 | **内置渠道实现**：钉钉、企业微信、Webhook (含回调签名验证) | P0 | 8h |
| 3.6 | **渠道回调统一入口**：`POST /api/v1/callbacks/{channel_type}`、解析回调、恢复中断 | P1 | 4h |
| 3.7 | **前端渠道管理页面**：渠道列表、配置、测试发送 | P1 | 5h |
| 3.8 | **前端执行卡片**：ExecutionCard (工具调用详情、耗时、缓存状态、展开/折叠) | P0 | 4h |
| 3.9 | **前端知识引用卡片**：KnowledgeRefCard (检索片段、相似度展示) | P1 | 3h |
| 3.10 | **对话输入框增强**：斜杠命令面板 `/`、场景快捷标签 Pills、附件上传(📎)、停止按钮 | P1 | 6h |


### Phase 4：知识库 LLM-Wiki (Week 5-6)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 4.1 | **raw/ 层管理 API**：文件上传、列表、详情、删除、编译触发 | P0 | 6h |
| 4.2 | **LLM-Wiki 编译引擎**：ingest→compile→cross-reference→lint→publish 流水线 | P0 | 12h |
| 4.3 | **告警确认→知识入库闭环**：提取分析结果→生成wiki页面→关联 knowledge_entry_id | P0 | 6h |
| 4.4 | **知识检索 API**：全文搜索 wiki/ 内容、相关性排序 | P0 | 4h |
| 4.5 | **Lint 体检**：全量一致性扫描、健康评分、问题分类(错误/警告/建议)、自动修复 | P1 | 6h |
| 4.6 | **前端知识库 raw/ 管理页**：文件列表(上传/拖拽)、详情抽屉、编译状态、告警联动 | P0 | 8h |
| 4.7 | **前端知识库 wiki/ 浏览页**：树形目录、Markdown渲染(react-markdown)、搜索、双向链接 | P0 | 8h |
| 4.8 | **前端编译日志/Lint报告页**：时间轴日志、健康评分、问题列表、一键修复 | P1 | 6h |
| 4.9 | **Hermes 自进化引擎骨架**：`.hermes/skills/` 目录结构、反思循环触发点 | P2 | 6h |


### Phase 5：客户端 Agent MVP (Week 6-7，可并行)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 5.1 | **客户端基础框架 (Go)**：mTLS WebSocket 客户端、认证、心跳(10s间隔)、注册 | P1 | 12h |
| 5.2 | **配置缓存**：加密本地存储 `config.enc`、签名验证、热加载(WebSocket diff) | P1 | 8h |
| 5.3 | **SkillExecutor**：Docker 沙箱执行、资源限制(cpu/mem)、结果回传 | P1 | 10h |
| 5.4 | **服务端 Agent Profile 管理**：CRUD API、版本管理、配置推送、批量下发 | P1 | 8h |
| 5.5 | **前端 Agent 管理页面**：在线状态面板、Profile 编辑器(JSON/YAML)、Skills 白名单配置 | P1 | 8h |
| 5.6 | **Collector 基础**：CPU/内存/磁盘/网络指标采集、批量上报 | P2 | 6h |
| 5.7 | **RuleEngine 基础**：自治规则加载、条件评估、动作执行 | P2 | 8h |
| 5.8 | **Ansible Playbook**：批量部署 `deploy_agents.yml` | P2 | 4h |


### Phase 6：个性化助理、权限与企业功能 (Week 7-8)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 6.1 | **私人助理配置 API**：`personal_assistant_configs` 表、启用的子智能体/工具/场景/提示词 | P1 | 6h |
| 6.2 | **前端"我的助理"面板**：抽屉式配置、拖拽启/禁用子智能体、工具收藏、场景快捷设置 | P1 | 6h |
| 6.3 | **能力级权限**：`sub_agent:use`, `skill:use`, `mcp:use` 细粒度授权 | P1 | 6h |
| 6.4 | **前端角色权限矩阵**：权限矩阵表格视图(资源×操作)、树状能力授权、LDAP配置向导 | P1 | 8h |
| 6.5 | **企业品牌设置**：Logo/名称/Favicon 上传、实时预览、热更新 | P2 | 4h |
| 6.6 | **LDAP 集成**：四步配置向导、连接测试、用户同步(手动/定时)、组映射角色 | P2 | 8h |
| 6.7 | **前端总览仪表板**：统计卡片、最近告警/会话/编译时间线、快速入口 | P1 | 6h |


### Phase 7：集成测试、文档与部署 (Week 8)
| # | 任务 | 优先级 | 估算 |
| --- | --- | --- | --- |
| 7.1 | **端到端自动化测试**：告警→分析→确认→入库→知识检索 全链路 | P0 | 8h |
| 7.2 | **前端 E2E 测试**：Playwright 关键用户路径 (登录/对话/告警确认/知识库浏览) | P1 | 6h |
| 7.3 | **OpenAPI 文档**：Swagger UI 嵌入、API 文档导出与校验 | P1 | 3h |
| 7.4 | **Docker Compose 生产配置**：docker-compose.yml (管控分离模式) + Nginx 分流配置 | P0 | 4h |
| 7.5 | **Docker Compose 开发配置**：docker-compose.dev.yml (热重载、调试端口) | P1 | 3h |
| 7.6 | **后端 Dockerfile**：Python 3.11-slim + Poetry + uvicorn | P0 | 2h |
| 7.7 | **前端 Dockerfile**：Node 20 构建 + Nginx 部署、gzip/br 压缩 | P0 | 2h |
| 7.8 | **用户手册**：getting-started.md, scenarios.md, knowledge.md, alerts.md | P1 | 8h |
| 7.9 | **管理员手册**：deployment.md, control-plane.md, security.md | P1 | 6h |
| 7.10 | **性能压测**：对话并发、告警吞吐、知识检索延迟 | P2 | 8h |


---

## 三、验证计划
### 3.1 单模块单元测试
| 模块 | 验证内容 | 工具 |
| --- | --- | --- |
| 认证授权 | JWT签发/验证/过期、RBAC权限校验中间件、LDAP模拟登录 | pytest + httpx |
| ToolManager | 工具热加载、缓存命中、限流触发、MCP工具自动发现 | pytest |
| LangGraph 节点 | 每个节点独立输入输出验证、状态转换正确性 | pytest + langgraph-test |
| 告警状态机 | 6种状态流转合法性(含非法跳转拦截) | pytest |
| 知识编译 | raw→wiki编译正确性、交叉引用生成、lint报告 | pytest |
| Sandbox | Docker容器资源限制、逃逸防护、超时终止 | pytest + docker sdk |


### 3.2 集成测试场景
**场景一：告警分析全链路 (核心MVP)**

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 通过 Kafka 生产测试告警 `{"host":"pay-01","metric":"cpu_usage","value":95,"threshold":90}` | 告警中心出现新告警，状态 `pending` |
| 2 | 触发规则匹配 → 自动分析 | 状态变为 `analyzing`，对话自动创建新会话 |
| 3 | 对话界面展示意图卡片("告警分析")、规划卡片(4个子任务) | 子任务依次执行，状态图标变化 |
| 4 | 展开 ExecutionCard 查看工具调用详情 | 显示 query_cmdb / prometheus_query 的请求参数和返回结果 |
| 5 | 分析完成，告警状态变为 `awaiting_review` | 最终回答卡片含根因结论和引用 |
| 6 | 点击"确认"按钮 → 弹出知识入库预览 | 预填标题/标签/摘要，可修改 |
| 7 | "确认并入库" → 知识库 wiki/ 生成新页面 | wiki/ 索引可见新条目，含反向链接 |
| 8 | 知识检索 API 搜索该条目 | 返回结果，相似度 > 0.8 |


**场景二：人工介入安全确认**

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 对话中触发"重启 pay-01 服务"操作 | 安全确认卡片弹出，风险等级"高" |
| 2 | 查看代码预览(语法高亮)和影响范围 | 显示 `systemctl restart pay-01`，主机 10.2.3.15 |
| 3 | 点击"拒绝" | 任务取消，对话提示"操作已拒绝" |
| 4 | 重新触发 → 点击"确认执行" | 操作继续，返回执行结果 |


**场景三：定时巡检**

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 创建定时任务 Cron `*/1 * * * *`，关联"日常巡检"场景 | RedBeat 注册成功 |
| 2 | 等待 1 分钟 | Worker 执行，自动创建会话并运行巡检 |
| 3 | 巡检完成 | 通知渠道(钉钉)收到巡检报告 |
| 4 | 定时任务历史列表 | 可见执行记录和巡检摘要 |


**场景四：客户端 Agent 协同**

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 在测试服务器安装 Agent，配置 mTLS 证书，启动 | Agent 注册成功，在线状态面板显示"在线" |
| 2 | 服务端下发"检查磁盘空间"Skill 到 Agent | Agent 本地 Skills 目录出现该 Skill |
| 3 | 对话中输入"检查 10.2.3.15 的磁盘" | 主智能体规划后调用 Skill，经 Execution Plane 下发到 Agent |
| 4 | 查看 ExecutionCard 返回结果 | 显示磁盘使用率、挂载点、inode 信息 |
| 5 | 修改 Agent Profile 采集间隔(30s → 60s) | 客户端日志输出采集频率变化 |


**场景五：权限控制**

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 创建只读角色(仅 `alerts.view`)，分配用户 | — |
| 2 | 该用户登录，进入告警中心 | "分析"按钮灰显，提示"无权操作" |
| 3 | 管理员在"我的助理"中为用户指定可用子智能体列表 | — |
| 4 | 用户在对话中查看可用场景/工具 | 仅显示被授权的子智能体和工具 |


### 3.3 性能验收指标
| 指标 | 目标值 | 压测方法 |
| --- | --- | --- |
| 告警接入→分析启动 | < 5s | Kafka 批量生产 100条告警，监控 analyzing 状态变更延迟 |
| 单次对话首Token延迟 | < 2s | 10并发用户发送对话请求 |
| 知识检索响应 | < 500ms | 全文搜索 1000+ wiki 页面 |
| 告警列表查询(千条) | < 200ms | 带筛选、排序、分页 |
| Agent 心跳延迟 | < 5s | 监控 heartbeat 时间戳 |
| 前端首屏加载 | < 2s | Lighthouse 评分 |


### 3.4 部署验收
| 环境 | 验收项 |
| --- | --- |
| 开发环境 | `docker-compose -f deploy/docker-compose.dev.yml up` 一键启动，前端 HMR 正常，后端热重载正常 |
| 单体模式 | `docker-compose -f deploy/docker-compose.allinone.yml up` 访问 localhost:3000 全功能可用 |
| 生产分离模式 | `docker-compose -f deploy/docker-compose.yml up` control:8001 + execution:8002 + 各 Worker 独立运行，Nginx 路径分流正确 |
| 客户端部署 | `ansible-playbook deploy/ansible/deploy_agents.yml` 批量安装成功，Agent 注册在线 |


---

## 四、风险与建议
1. **LangGraph 复杂度**：8节点主图涉及大量状态管理，建议 Phase 1 先实现简化版 `init→plan→exec_task→synthesize→final_answer` 贯通，后续迭代加入 human_interrupt/reflect/evolve。
2. **Kafka 与 MCP 可降级**：Phase 0/1 开发时可先 Mock Kafka 和 MCP（通过内存队列和本地文件模拟），减少基础设施依赖。
3. **客户端语言选型**：文档建议 Go 但目录规划用 Rust。建议 Phase 5 启动前做技术验证，Go 开发效率更高适合 MVP，Rust 性能更好适合生产。
4. **知识库文件存储**：`knowledge-base/` 作为 Docker Volume 挂载适合单机，若需多实例扩展后续可考虑 S3/MinIO 后端。
5. **建议增量交付节奏**：每 2 周一个可演示版本 (Phase 0-1 → 最小对话demo; Phase 2 → 告警闭环; Phase 3-4 → 完整人机协作; Phase 5-6 → 全栈就绪)。

---

是否需要我对某个阶段或模块做更详细的任务拆解，或者讨论某个具体的技术选型？

