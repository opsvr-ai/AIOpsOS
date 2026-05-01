# AIOpsOS 数据接入架构设计

**日期**: 2026-05-01
**状态**: 设计完成，待开发落地

---

## 一、需求背景与目标

### 1.1 当前状态

平台已有两个数据通道：
- **数据接入（性能指标）**: DataSource 模型支持 Webhook / API Poller / Kafka Consumer 三种通道，指标数据进入 Kafka 用于性能分析
- **事件接入（告警）**: 同上 DataSource 体系，告警数据归一化后生成 Alert 实体，触发 SceneTrigger 匹配 + LLM 分析

### 1.2 扩展目标

新增三个数据通道 + 一个数据智能体，支撑完整的故障全生命周期管理：

| 目标能力 | 依赖数据 | 当前状态 |
|---------|---------|---------|
| 告警诊断分析 | 事件 + 指标 | 部分实现 |
| 故障根因定界 | 事件 + 日志 + 配置 | 需扩展 |
| 故障处置推荐 | 配置 + 流程 + 历史 | 需扩展 |
| 应急响应 | 流程 + 配置 | 未实现 |
| 容量预测/管理 | 指标 + 配置 | 未实现（后续） |

---

## 二、总体架构：统一采集管道

### 2.1 设计决策

选择 **方案 A：统一采集管道**，在现有 DataSource 架构上扩展。

- 复用现有 DataSource CRUD 和三种接入通道（Webhook/API/Kafka）
- 每种新数据类型只需增加 Processor（normalize + enrich + store）
- 新增 Filebeat 采集通道用于日志
- 按数据类型选择最优存储引擎，统一查询层屏蔽差异

### 2.2 五通道全景

```
数据源 → 统一接入层 → 类型Processor层 → 分类型存储 → 统一查询层 → Agent

指标(Kafka/API)  →  MetricsProcessor  →  时序DB / Kafka
事件(Webhook/API) →  AlertProcessor    →  PG (alerts)
日志(Filebeat/Kafka)→ LogProcessor    →  PG 分区表 (30min窗口)
ITSM(API/Webhook)  →  ItsmProcessor   →  PG (itsm_tickets)
CMDB(API/Skill)    →  CmdbProcessor   →  PG 属性图 (nodes + edges)
```

### 2.3 DataSource 扩展

DataSource.source_type 枚举扩展：
- 已有：`kafka`, `webhook`, `api`
- 新增：`log`, `itsm`, `cmdb`

每种新类型的 DataSource.config JSONB 携带类型特定配置（采集命令、分区策略、映射规则路径等）。

---

## 三、日志接入

### 3.1 约束

- 核心场景是故障定界，日志只保留最近 **30 分钟**
- 全栈日志：应用日志 + 系统日志 + 中间件日志（MySQL/Nginx/Redis）
- 量级不确定，需要可扩展架构

### 3.2 架构

```
采集: Filebeat / Vector / Kafka Consumer
缓冲: Redis Streams（削峰，500ms / 1000条批量 flush）
存储: log_events 按小时分区表
清理: pg_cron 每分钟 DELETE WHERE ingested_at < NOW() - 30min
检索: Agent Tool → search_logs / get_error_context / count_logs
```

不引入 ES/Loki —— 30 分钟窗口下 PG 分区表性能充足（即使 500GB/天流量，活跃数据仅 ~10GB）。

### 3.3 数据模型

```sql
CREATE TABLE log_events (
    id UUID DEFAULT gen_random_uuid(),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- 分区键
    timestamp   TIMESTAMPTZ,                          -- 日志原始时间
    service     VARCHAR(128),                         -- 服务名 (B-tree index)
    host        VARCHAR(128),                         -- 主机名 (index)
    level       VARCHAR(16),                          -- ERROR/WARN/INFO/DEBUG
    trace_id    VARCHAR(64),                          -- 分布式追踪ID (index)
    message     TEXT,                                 -- 日志正文 (GIN index)
    raw         JSONB,                                -- 原始日志完整数据
    datasource_id UUID FK
) PARTITION BY RANGE (ingested_at);
```

### 3.4 LogProcessor 归一化规则

| 目标字段 | 提取来源（优先级从高到低） |
|---------|------------------------|
| timestamp | @timestamp → time → timestamp → 正则提取 |
| service | service → app → container_name → namespace → host反查CMDB |
| level | level → severity → log_level → 正则匹配 |
| trace_id | trace_id → traceId → x-trace-id → OpenTelemetry 标准字段 |
| message | message → msg → log → body |

### 3.5 Agent 检索接口

| Tool | 参数 | 用途 |
|------|-----|------|
| `search_logs` | service, level, keyword, time_range, trace_id, limit | 条件检索 |
| `get_error_context` | trace_id, before_seconds, after_seconds | 按 trace_id 查周边日志 |
| `count_logs` | service, level, time_range | 聚合统计（错误率） |

---

## 四、ITSM 流程接入

### 4.1 约束

- 支持 Webhook 接收 + API 主动抓取
- API 抓取涉及多步请求依赖（先拿列表，再逐个拿详情）
- 复用现有 ApiPoller 的 request_chain 机制

### 4.2 工单类型与故障场景

| 工单类型 | 来源 | 故障定界用途 |
|---------|-----|------------|
| 事件单 (Incident) | 告警自动/人工报修 | 关联告警 → 还原处置过程 |
| 变更单 (Change) | 发布系统/变更审批 | 故障时间线："告警前5分钟有变更" |
| 问题单 (Problem) | 事件升级/复盘 | 历史根因分析 → 处置推荐 |
| 服务请求 (Request) | 用户提交 | 变更影响范围评估 |

### 4.3 数据模型

```sql
CREATE TABLE itsm_tickets (
    id UUID PRIMARY KEY,
    external_id VARCHAR(128) UNIQUE NOT NULL,          -- ITSM原始ID（去重）
    ticket_type VARCHAR(32),                           -- incident/change/problem/request
    title VARCHAR(512),
    status VARCHAR(32),                                -- new/in_progress/resolved/closed
    priority VARCHAR(16),                              -- critical/high/medium/low
    affected_service VARCHAR(128),                     -- 关联服务名
    created_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    raw_data JSONB,                                    -- 原始工单数据
    linked_alert_ids UUID[],                           -- 关联的平台告警
    datasource_id UUID FK,
    space_id UUID FK
);
```

### 4.4 request_chain 配置示例

```json
{
  "request_chain": [
    {
      "path": "/api/itsm/incidents",
      "method": "GET",
      "params": { "updated_since": "{{last_sync}}", "limit": 100 },
      "extract": { "incident_ids": "$.data[*].id" }
    },
    {
      "path": "/api/itsm/incidents/{{incident_id}}/timeline",
      "method": "GET",
      "foreach": "incident_ids"
    },
    {
      "path": "/api/itsm/changes",
      "method": "GET",
      "params": { "start_time": "{{last_sync}}", "limit": 100 }
    }
  ],
  "poll_interval_seconds": 300
}
```

### 4.5 ItsmProcessor 富化逻辑

1. **归一化**: 不同 ITSM 系统字段映射到标准字段
2. **服务关联**: 从 title/description 提取服务名 → 匹配 cmdb_nodes
3. **告警关联**: 按时间窗口（前后30分钟）+ 服务名匹配已有 Alert
4. **去重**: external_id 唯一约束，重复则更新 status + raw_data

### 4.6 Agent 检索接口

| Tool | 参数 | 用途 |
|------|-----|------|
| `search_tickets` | service, type, status, time_range, keyword | 查相关变更/事件，构建故障时间线 |
| `get_ticket_detail` | ticket_id | 查看工单详情 + 处置记录 |
| `get_service_timeline` | service, time_range | 聚合告警+变更+事件 → 故障时间线 |

---

## 五、CMDB 配置数据接入

### 5.1 策略：拓扑同步 + 技能补全（方案 C）

- **定时同步**: 服务依赖拓扑 + 服务-主机映射（每1小时）+ 主机清单（每24小时）
- **按需补全**: 设备详细属性通过 Agent Skill 实时从 CMDB API 获取，不存储
- **不选全量同步**：数据量大、24h 滞后、大量无关属性
- **不选纯技能**：故障时 CMDB 可能不可达

### 5.2 数据模型：属性图

不做 CI 类型建模，用两张表统一抽象任意 CMDB 的 CI 和关系：

```sql
-- 节点表
CREATE TABLE cmdb_nodes (
    id UUID PRIMARY KEY,
    ci_type VARCHAR(64),                -- server/app/db/lb/vip/rack...
    name VARCHAR(256),
    external_id VARCHAR(256),           -- CMDB原始ID（去重）
    source VARCHAR(64),                 -- cmdb-itop / cmdb-servicenow
    properties JSONB,                   -- 所有属性 (GIN index)
    synced_at TIMESTAMPTZ,
    space_id UUID FK
);

-- 边表
CREATE TABLE cmdb_edges (
    id UUID PRIMARY KEY,
    source_node_id UUID FK REFERENCES cmdb_nodes(id),
    target_node_id UUID FK REFERENCES cmdb_nodes(id),
    relation_type VARCHAR(32),          -- depends_on / runs_on / contains / connects_to
    properties JSONB,                   -- 端口、协议等 (GIN index)
    source VARCHAR(64)
);
```

### 5.3 转换管道：LLM 发现 + 规则执行

```
首次同步（发现模式）:
  CMDB 原始数据 → LLM 分片分析 → 生成映射规则 v1 + Node/Edge → 人工审核规则

后续同步（增量模式）:
  CMDB 原始数据 → 已有规则转换 → 新CI类型 LLM补充 → 增量 upsert
```

### 5.4 Agent 查询接口

| Tool | 用途 | 实现方式 |
|------|-----|---------|
| `search_node(name, ci_type?)` | 按名称模糊搜索 CI | ILIKE + GIN on properties |
| `get_dependencies(name, direction, depth)` | 上游/下游依赖链 | 递归 CTE 沿 edges 遍历 |
| `get_blast_radius(name)` | 故障影响面 | 下游 BFS 所有可达节点 |
| `find_path(from, to)` | 两个 CI 间最短路径 | BFS 最短路径 |
| `get_topology(ci_types[], depth)` | 子图拓扑 | 过滤 ci_type + 递归遍历 |

### 5.5 映射规则示例

```yaml
# cmdb_itop_mapping.yaml
ci_types:
  Server:
    node_type: server
    name_field: name
    id_field: id
    extract: [ip_address, os_version, cpu_cores, ram_gb, rack_id, environment]
    relations:
      - field: services_list
        target_type: app
        relation: runs_on
        direction: incoming
  ApplicationSolution:
    node_type: app
    name_field: name
    extract: [status, version, url, owner_team]
    relations:
      - field: providercontracts_list
        target_type: app
        relation: depends_on
        direction: outgoing
```

---

## 六、数据准确性校验

### 6.1 三层校验管道

```
L1 结构校验（自动，零成本）
  - 必填字段完整性、ci_type 枚举范围、Edge 端点有效性
  - 无孤立 Edge、无自环、external_id 无重复
  - 不通过 → 拒绝入库

L2 语义校验（LLM 辅助）
  - 抽样 5~10% 的转换结果
  - LLM 比对原始数据 vs 转换结果，给出置信度（0-100）
  - 置信度 < 80 → 入审核队列

L3 异常检测（统计驱动）
  - 与上次同步 diff：新增>20% / 删除>10% / 类型变更>5%
  - 关键节点变更强制标记
  - 异常 → 暂停同步 + 告警
```

### 6.2 审核队列

被 L2/L3 标记的数据进入 `cmdb_review_items` 表，含 source_data、transformed_data、llm_confidence、llm_reason、diff_summary。

前端提供 approve/reject 接口，确认后释放入库。

---

## 七、CmdbIngestionAgent 智能体

### 7.1 定位

独立子 Agent，类比 MemoryConsolidationAgent。负责 CMDB → 平台属性图的完整转换链路。LLM 驱动，支持后台定时 + 手动触发。

### 7.2 状态机

```
idle → fetching → transforming → validating → reviewing → writing → idle
  ↳ fetch_failed（CMDB不可达，告警，等下次cron重试）
  ↳ transform_failed（LLM异常，保留原始快照，告警）
```

### 7.3 运行模式

| 模式 | 触发 | 行为 |
|-----|-----|------|
| **discover** | 首次接入 CMDB | LLM 分析 schema → 生成映射规则 → 人工确认 |
| **incremental** | 定时 / 手动 | 规则驱动转换 + 新 CI 类型 LLM 补充 |
| **full** | 规则变更后 | 全量重跑，替换全部数据 |

### 7.4 核心方法

```python
class CmdbIngestionAgent:
    async def run_sync(datasource_id, mode="incremental") -> SyncResult

    # 阶段1：拉取
    async def _fetch(ds: DataSource) -> list[dict]

    # 阶段2：转换
    async def _discover_schema(raw_data) -> CmdbMappingRule
    async def _transform_batch(batch, rule) -> BatchResult

    # 阶段3：校验
    async def _validate_structure(nodes, edges) -> list[ValidationError]
    async def _validate_semantic(sample, raw_data) -> list[SemanticResult]
    async def _detect_anomaly(new_data, prev_sync) -> list[Anomaly]

    # 阶段4：写入
    async def _upsert_nodes(nodes) -> UpsertStats
    async def _upsert_edges(edges) -> UpsertStats
    async def _cleanup_stale(active_external_ids) -> int

    # 审核
    async def approve_review_item(item_id, reviewer) -> None
    async def reject_review_item(item_id, reviewer, reason) -> None
```

### 7.5 可插拔数据获取

数据获取通过抽象接口解耦，当前用 DataSource 配置的 API，后续替换为 Skill 不需改 Agent 代码：

```python
class CmdbDataFetcher(ABC):
    @abstractmethod
    async def fetch(self, ds: DataSource) -> list[dict]: ...

class ApiCmdbFetcher(CmdbDataFetcher):      # 当前实现
class SkillCmdbFetcher(CmdbDataFetcher):    # 后续替换
```

### 7.6 LLM 转换 Prompt 核心指令

```
你是 CMDB 配置管理专家。将以下原始 CI 数据转换为运维平台的属性图模型。
输出严格 JSON:
{
  "nodes": [{"external_id": "原始ID", "name": "标准化名称",
    "ci_type": "server/app/db/lb/vip/...", "properties": {...}}],
  "edges": [{"source_external_id": "", "target_external_id": "",
    "relation_type": "depends_on/runs_on/contains"}],
  "new_rules": [{"ci_type": "", "name_pattern": "", "relation_mapping": {}}]
}
```

### 7.7 新增数据模型

- **CmdbSyncLog**: datasource_id, status, nodes_created/updated/deleted, edges_count, review_count, errors_detail(JSONB), raw_snapshot_path, started_at, finished_at
- **CmdbMappingRule**: datasource_id, version, rule_content(JSONB), status(draft/active/superseded), approved_by, created_at
- **CmdbReviewItem**: sync_log_id, review_type, source_data(JSONB), transformed_data(JSONB), llm_confidence, llm_reason, diff_summary(JSONB), status(pending/approved/rejected)

### 7.8 API 端点

| 方法 | 路径 | 说明 |
|-----|------|------|
| POST | /api/v1/datasources/{id}/sync | 触发CMDB同步（mode: discover/incremental/full） |
| GET | /api/v1/datasources/{id}/sync-logs | 同步历史 + 进度 |
| GET | /api/v1/cmdb/review-items | 待审核列表（分页/过滤） |
| POST | /api/v1/cmdb/review-items/{id}/approve | 通过审核 |
| POST | /api/v1/cmdb/review-items/{id}/reject | 驳回 |
| GET | /api/v1/cmdb/nodes | 查询CMDB节点 |
| GET | /api/v1/cmdb/topology | 拓扑查询 |
| PUT | /api/v1/cmdb/mapping-rules/{id} | 更新映射规则 |

---

## 八、现有架构改造清单

### 8.1 后端改造

| 文件 | 改动 | 说明 |
|-----|-----|------|
| `src/models/datasource.py` | 扩展 source_type 枚举 | 新增 log/itsm/cmdb |
| `src/models/knowledge.py` | 新增 CmdbSyncLog, CmdbMappingRule, CmdbReviewItem | CMDB 智能体模型 |
| 新建 `src/models/log.py` | 新增 LogEvent | log_events 分区表模型 |
| 新建 `src/models/itsm.py` | 新增 ItsmTicket | itsm_tickets 模型 |
| 新建 `src/models/cmdb.py` | 新增 CmdbNode, CmdbEdge | 属性图模型 |
| `src/services/api_poller.py` | 无改动 | request_chain 已支持 ITSM 多步拉取 |
| 新建 `src/services/log_processor.py` | LogProcessor | 日志归一化 + 批量写入 |
| 新建 `src/services/itsm_processor.py` | ItsmProcessor | ITSM 工单归一化 + 富化 + 关联 |
| 新建 `src/agent/sub_agents/cmdb_ingestion_agent.py` | CmdbIngestionAgent | CMDB 数据接入智能体 |
| `src/api/execution/datasources.py` | 扩展 test 逻辑 | 支持新 source_type 的连接测试 |
| 新建 `src/api/control/cmdb.py` | CMDB CRUD API | 节点/拓扑查询 + 审核 + 映射规则 |
| `src/main.py` | 注册新 router | 启动 cmdb_ingestion_agent 后台任务 |

### 8.2 前端改造

| 页面 | 改动 | 说明 |
|-----|-----|------|
| `DataSourcePage.tsx` | type 选择器扩展 | 新增 log/itsm/cmdb 类型 |
| `DataSourceFormModal.tsx` | 配置表单扩展 | 各类型特定配置字段 |
| 新建 `CmdbPage.tsx` | CMDB 管理页面 | 节点拓扑可视化 + 同步管理 + 审核队列 |
| 新建 `LogPage.tsx` | 日志查看页面 | 实时日志流 + 检索 + 错误上下文 |
| 新建 `ItsmPage.tsx` | ITSM 工单页面 | 工单列表 + 告警关联 + 时间线 |
| `Sidebar.tsx` | 导航菜单扩展 | 新增 CMDB/日志/ITSM 菜单项 |

---

## 九、需求确认记录

| 决策 | 结论 |
|-----|------|
| 流程接入定义 | ITSM 流程（事件单、变更单、问题单、服务请求） |
| 日志范围 | 全栈日志（应用 + 系统 + 中间件），量级待定 |
| 配置数据定义 | CMDB 资源台账 + 服务拓扑依赖 |
| 日志存储方案 | PG 分区表，30 分钟窗口，不引入 ES/Loki |
| ITSM 接入方式 | Webhook + API Poller request_chain 复用 |
| CMDB 接入策略 | 拓扑同步 + 技能补全（方案 C） |
| CMDB 数据模型 | 属性图（Node + Edge），LLM 发现 + 规则执行 |
| 数据校验 | L1 结构 + L2 语义 + L3 异常检测，审核队列 |
| CMDB 智能体 | 独立子 Agent，可插拔数据获取接口，后续替换为 Skill |
