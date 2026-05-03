"""CmdbIngestionAgent — orchestrates CMDB → property graph sync with LLM-driven mapping.

Follows MemoryConsolidationAgent pattern: optional model injection, lazy _get_llm().
State machine: idle → fetching → transforming → validating → reviewing → writing → idle

Triggered by:
  - API: POST /api/v1/datasources/{id}/sync
  - Cron: Celery Beat periodic task (per DataSource.config.sync_schedule)
  - Manual: UI sync button on CmdbPage
"""

from __future__ import annotations

import json as _json
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import delete, func, select

from src.models.base import async_session_factory
from src.models.cmdb import (
    CmdbEdge,
    CmdbMappingRule,
    CmdbNode,
    CmdbReviewItem,
    CmdbSyncLog,
)
from src.models.datasource import DataSource

logger = logging.getLogger(__name__)

CMDB_DISCOVERY_PROMPT = """你是 CMDB 配置管理专家。将以下原始 CI 数据转换为运维平台的属性图模型。

## CI 类型分类规则
- 包含 ip_address / os_version / cpu_cores / ram_gb 的 → server
- 名称含 -app / -svc / -api 后缀，或有 deploy_info / version / url 的 → app
- 名称含 -db / -mysql / -pg / -redis 或端口 3306/5432/6379 的 → db
- 名称含 vip / virtual_ip / loadbalancer / lb 的 → vip
- 名称含 rack- / 机柜 / 机房 的 → rack
- 无法匹配以上任何规则 → unknown

## 关系推断规则
- CI 有 runs_on / deployed_on / host 字段指向其他 CI → runs_on
- CI 有 depends_on / connects_to / uses 字段指向其他 CI → depends_on
- CI 有 contains / members / children 字段 → contains
- 端口为 3306/5432/6379/27017 的连接 → depends_on (数据库依赖)
- 包含在同一个应用组/集群 → connects_to

## 输出格式
严格返回 JSON:
{
  "nodes": [
    {"external_id": "原始ID", "name": "标准化名称", "ci_type": "server|app|db|vip|rack|unknown",
     "properties": {"ip": "...", "os": "...", "cpu": 4, ...}}
  ],
  "edges": [
    {"source_external_id": "", "target_external_id": "",
     "relation_type": "depends_on|runs_on|contains|connects_to"}
  ],
  "new_rules": [
    {"ci_type": "检测到的新CI类型", "name_pattern": "命名规律",
     "id_field": "ID字段名", "properties_to_extract": ["属性1"],
     "relation_mapping": {"field": "关联字段", "rule": "转换规则"}}
  ]
}

最多处理 50 条 CI 数据。没有关系或新规则时返回空数组。"""

CMDB_SEMANTIC_VALIDATION_PROMPT = """你是CMDB数据质量审核专家。比对原始CI数据与转换后的属性图数据，评估转换质量。

对每条记录给出：
- confidence: 0-100 的置信度评分
- reason: 审核意见（中文，≤50字）
- issues: 发现的具体问题列表

返回JSON数组: [{"index": 0, "confidence": 85, "reason": "字段映射正确", "issues": []}]

标记原则：
- 95-100: 完美转换，所有关键字段正确
- 80-94: 基本正确，非关键字段可能有遗漏
- 60-79: 有明确问题需要人工复核
- <60: 转换错误，不应自动入库"""


# ── Data Fetcher abstraction ────────────────────────────────────────────


class CmdbDataFetcher(ABC):
    """Pluggable CMDB data fetcher. Replace with Skill-based fetcher later."""

    @abstractmethod
    async def fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        """Fetch raw CI data from the CMDB source."""
        ...


class ApiCmdbFetcher(CmdbDataFetcher):
    """Fetch CMDB data via REST API using DataSource.config."""

    async def fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        if not base_url:
            raise ValueError("CMDB api_base_url not configured")

        headers: dict[str, str] = {}
        auth = config.get("auth", {})
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth.get("type") == "basic":
            from base64 import b64encode
            creds = b64encode(
                f"{auth['username']}:{auth['password']}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"

        ci_endpoint = config.get("ci_endpoint", "/api/v1/cis")
        params = config.get("fetch_params", {"limit": 500})
        all_items: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=120) as client:
            while True:
                resp = await client.get(
                    f"{base_url}{ci_endpoint}",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                if isinstance(data, dict):
                    items = data.get("items", data.get("data", data.get("results", [])))
                    next_cursor = data.get("next_cursor") or data.get("next")
                elif isinstance(data, list):
                    items = data
                    next_cursor = None
                else:
                    items = []
                    next_cursor = None

                all_items.extend(items)
                if not next_cursor or len(items) == 0:
                    break
                params["cursor"] = next_cursor

        return all_items


# ── CMDB Ingestion Agent ────────────────────────────────────────────────


class CmdbIngestionAgent:
    """Autonomous agent for CMDB → property graph synchronization.

    Usage:
        agent = CmdbIngestionAgent()
        result = await agent.run_sync(datasource_id, mode="incremental")
    """

    def __init__(self, model=None) -> None:
        self._llm = model
        self._fetcher: CmdbDataFetcher = ApiCmdbFetcher()

    async def _get_llm(self):
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm

    # ── Public API ──────────────────────────────────────────────────────

    async def run_sync(
        self, datasource_id: str, mode: str = "incremental", space_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a full sync pipeline. Returns summary stats.

        Args:
            datasource_id: The cmdb-type DataSource to sync
            mode: discover (first-time schema detection), incremental (delta), or full (rebuild)
            space_id: Optional space context override
        """
        ds = await self._load_datasource(datasource_id)
        if ds is None:
            return {"success": False, "error": f"DataSource {datasource_id} not found"}
        if ds.source_type != "cmdb":
            return {"success": False, "error": "Only cmdb-type datasources support sync"}

        effective_space = space_id or (str(ds.space_id) if ds.space_id else None)
        sync_log = await self._create_sync_log(datasource_id, mode, effective_space)

        try:
            # Phase 1: Fetch
            phase_start = datetime.now(UTC)
            raw_data = await self._fetch(ds)
            logger.info(
                "Sync %s: fetched %d CI records in %.1fs",
                sync_log.id, len(raw_data),
                (datetime.now(UTC) - phase_start).total_seconds(),
            )

            # Phase 2: Transform
            phase_start = datetime.now(UTC)
            if mode == "discover":
                rule = await self._discover_schema(raw_data[:50], ds)
                if rule.get("data", {}).get("nodes") or rule.get("data", {}).get("new_rules"):
                    await self._store_mapping_rule(
                        datasource_id, rule, effective_space,
                    )
                    logger.info("Sync %s: discovered mapping rule", sync_log.id)

            nodes, edges = await self._transform_batch(raw_data, ds)
            logger.info(
                "Sync %s: transformed -> %d nodes, %d edges in %.1fs",
                sync_log.id, len(nodes), len(edges),
                (datetime.now(UTC) - phase_start).total_seconds(),
            )

            # Phase 3: Validate
            phase_start = datetime.now(UTC)
            struct_errors = self._validate_structure(nodes, edges)
            if struct_errors:
                logger.warning(
                    "Sync %s: %d L1 structural errors, rejecting batch",
                    sync_log.id, len(struct_errors),
                )
                await self._mark_sync_failed(
                    sync_log.id,
                    f"L1 structural validation: {len(struct_errors)} errors",
                )
                return {
                    "success": False,
                    "error": f"Structural validation failed: {len(struct_errors)} errors",
                    "validation_errors": struct_errors[:20],
                }

            sample_size = min(10, len(nodes))
            sample_nodes = nodes[:sample_size]
            sample_raw = raw_data[:sample_size]
            review_count = await self._validate_semantic(
                sample_nodes, sample_raw, ds, sync_log.id, effective_space,
            )
            anomaly_count = await self._detect_anomaly(
                nodes, datasource_id, sync_log.id, effective_space,
            )
            logger.info(
                "Sync %s: validation done in %.1fs — %d review items, %d anomalies",
                sync_log.id,
                (datetime.now(UTC) - phase_start).total_seconds(),
                review_count, anomaly_count,
            )

            # Phase 4: Write
            phase_start = datetime.now(UTC)
            node_stats = await self._upsert_nodes(nodes, datasource_id, effective_space)
            edge_stats = await self._upsert_edges(edges, datasource_id)
            cleaned = await self._cleanup_stale(
                datasource_id, [n["external_id"] for n in nodes],
            )
            logger.info(
                "Sync %s: write done in %.1fs — nodes +%d/~%d/-%d, edges +%d",
                sync_log.id,
                (datetime.now(UTC) - phase_start).total_seconds(),
                node_stats["created"], node_stats["updated"], cleaned,
                edge_stats["created"],
            )

            await self._finalize_sync_log(
                sync_log.id, node_stats, edge_stats, cleaned, review_count,
            )
            return {
                "success": True,
                "sync_log_id": str(sync_log.id),
                "nodes": node_stats,
                "edges": edge_stats,
                "cleaned": cleaned,
                "review_count": review_count,
                "anomaly_count": anomaly_count,
            }

        except Exception as exc:
            logger.exception("Sync %s failed", sync_log.id)
            await self._mark_sync_failed(sync_log.id, str(exc)[:1000])
            return {"success": False, "error": str(exc)}

    # ── Phase 1: Fetch ──────────────────────────────────────────────────

    async def _fetch(self, ds: DataSource) -> list[dict[str, Any]]:
        return await self._fetcher.fetch(ds)

    # ── Phase 2: Transform ──────────────────────────────────────────────

    async def _discover_schema(
        self, raw_data: list[dict[str, Any]], ds: DataSource,
    ) -> dict[str, Any]:
        """LLM-driven schema discovery: analyze raw CI data, generate mapping rules."""
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=CMDB_DISCOVERY_PROMPT),
            HumanMessage(
                content=f"分析以下 {len(raw_data)} 条CMDB原始数据，生成映射规则:\n"
                f"{_json.dumps(raw_data, ensure_ascii=False, default=str)}"
            ),
        ])
        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            return {"version": 1, "data": _json.loads(raw)}
        except Exception:
            logger.exception("Schema discovery LLM parse failed")
            return {"version": 1, "data": {"nodes": [], "edges": [], "new_rules": []}}

    async def _transform_batch(
        self, raw_data: list[dict[str, Any]], ds: DataSource,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Transform raw CI data into normalized nodes and edges."""
        rule = await self._load_active_rule(str(ds.id))
        if rule:
            return self._rule_based_transform(raw_data, rule)
        return await self._llm_transform(raw_data[:50])

    def _rule_based_transform(
        self, raw_data: list[dict[str, Any]], rule: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Apply a known mapping rule to transform data deterministically."""
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        for item in raw_data:
            if not isinstance(item, dict):
                continue
            ci_type_str = str(
                item.get("ci_type", item.get("type", item.get("class_name", "unknown")))
            ).lower()
            ext_id = str(item.get("id", item.get("external_id", item.get("sys_id", uuid4()))))
            name = str(
                item.get("name", item.get("display_name", item.get("hostname", ext_id)))
            )

            node: dict[str, Any] = {
                "external_id": ext_id,
                "name": name,
                "ci_type": ci_type_str,
                "properties": {
                    k: v for k, v in item.items()
                    if k not in ("id", "external_id", "sys_id", "name", "display_name",
                                  "hostname", "ci_type", "type", "class_name")
                },
            }
            if "ip_address" in item:
                node["properties"]["ip"] = item["ip_address"]
            nodes.append(node)

            for rel_field in ("depends_on", "runs_on", "relations", "linked_ci", "used_by"):
                if rel_field in item and item[rel_field]:
                    targets = (
                        item[rel_field]
                        if isinstance(item[rel_field], list)
                        else [item[rel_field]]
                    )
                    for target in targets:
                        target_id = (
                            target.get("id", target.get("external_id", str(target)))
                            if isinstance(target, dict)
                            else str(target)
                        )
                        rel_type = "depends_on" if "depend" in rel_field else "runs_on"
                        edges.append({
                            "source_external_id": ext_id,
                            "target_external_id": target_id,
                            "relation_type": rel_type,
                        })

        return nodes, edges

    async def _llm_transform(
        self, raw_data: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Use LLM to transform raw data (discover mode or fallback)."""
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=CMDB_DISCOVERY_PROMPT),
            HumanMessage(
                content=f"转换以下 {len(raw_data)} 条CMDB数据:\n"
                f"{_json.dumps(raw_data, ensure_ascii=False, default=str)}"
            ),
        ])
        try:
            raw_text = resp.content.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1].rsplit("\n```", 1)[0]
            result = _json.loads(raw_text)
            return result.get("nodes", []), result.get("edges", [])
        except Exception:
            logger.exception("LLM transform parse failed")
            return [], []

    # ── Phase 3: Validate ───────────────────────────────────────────────

    @staticmethod
    def _validate_structure(
        nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """L1 structural validation: required fields, valid refs, no self-loops."""
        errors: list[dict[str, Any]] = []
        node_ids = {n.get("external_id") for n in nodes if n.get("external_id")}
        valid_ci_types = {"server", "app", "db", "vip", "lb", "rack", "unknown"}

        for i, node in enumerate(nodes):
            if not node.get("external_id"):
                errors.append({"index": i, "type": "missing_external_id"})
            if not node.get("name"):
                errors.append({"index": i, "type": "missing_name"})
            if not node.get("ci_type"):
                errors.append({"index": i, "type": "missing_ci_type"})
            elif node["ci_type"] not in valid_ci_types:
                errors.append({
                    "index": i, "type": "unknown_ci_type",
                    "detail": f"ci_type '{node['ci_type']}' not in known types",
                })

        for i, edge in enumerate(edges):
            src = edge.get("source_external_id")
            tgt = edge.get("target_external_id")
            if not src or not tgt:
                errors.append({"index": i, "type": "missing_endpoint"})
                continue
            if src == tgt:
                errors.append({"index": i, "type": "self_loop", "detail": str(src)})
            if src not in node_ids:
                errors.append({"index": i, "type": "dangling_source", "detail": str(src)})
            if tgt not in node_ids:
                errors.append({"index": i, "type": "dangling_target", "detail": str(tgt)})

        return errors

    async def _validate_semantic(
        self,
        sample_nodes: list[dict[str, Any]],
        sample_raw: list[dict[str, Any]],
        ds: DataSource,
        sync_log_id: Any,
        space_id: str | None,
    ) -> int:
        """L2 semantic validation: LLM compares raw vs transformed for correctness.

        Flags items with confidence < 80 into the review queue.
        Returns count of review items created.
        """
        if not sample_nodes or not sample_raw:
            return 0

        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=CMDB_SEMANTIC_VALIDATION_PROMPT),
            HumanMessage(content=_json.dumps({
                "raw": sample_raw,
                "transformed": sample_nodes,
            }, ensure_ascii=False, default=str)),
        ])
        try:
            raw_text = resp.content.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1].rsplit("\n```", 1)[0]
            assessments = _json.loads(raw_text)
        except Exception:
            logger.exception("L2 semantic validation parse failed")
            return 0

        review_count = 0
        for assessment in assessments:
            confidence = assessment.get("confidence", 100)
            if confidence < 80:
                idx = assessment.get("index", 0)
                await self._create_review_item(
                    sync_log_id=sync_log_id,
                    review_type="semantic",
                    source_data=sample_raw[idx] if idx < len(sample_raw) else {},
                    transformed_data=sample_nodes[idx] if idx < len(sample_nodes) else {},
                    llm_confidence=confidence,
                    llm_reason=assessment.get("reason", ""),
                    space_id=space_id,
                )
                review_count += 1

        return review_count

    async def _detect_anomaly(
        self,
        nodes: list[dict[str, Any]],
        datasource_id: str,
        sync_log_id: Any,
        space_id: str | None,
    ) -> int:
        """L3 statistical anomaly detection: compare node count vs previous sync."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(func.count(CmdbNode.id)).where(
                    CmdbNode.datasource_id == datasource_id,
                )
            )
            prev_count = result.scalar() or 0

        if prev_count == 0:
            return 0

        new_count = len(nodes)
        change_pct = abs(new_count - prev_count) / prev_count * 100
        anomaly_count = 0

        if change_pct > 20:
            await self._create_review_item(
                sync_log_id=sync_log_id,
                review_type="anomaly",
                source_data={"previous_count": prev_count},
                transformed_data={"current_count": new_count},
                llm_confidence=0,
                llm_reason=f"Node count changed {change_pct:.1f}% (threshold 20%)",
                diff_summary={
                    "previous": prev_count, "current": new_count,
                    "change_pct": round(change_pct, 1), "threshold": 20,
                },
                space_id=space_id,
            )
            anomaly_count = 1

        return anomaly_count

    # ── Phase 4: Write ──────────────────────────────────────────────────

    async def _upsert_nodes(
        self, nodes: list[dict[str, Any]], datasource_id: str, space_id: str | None,
    ) -> dict[str, int]:
        """Upsert nodes into cmdb_nodes. Returns {created, updated}."""
        created = 0
        updated = 0
        source = nodes[0].get("source", "") if nodes else ""

        async with async_session_factory() as db:
            for node in nodes:
                result = await db.execute(
                    select(CmdbNode).where(
                        CmdbNode.external_id == node["external_id"],
                        CmdbNode.source == source,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.name = node.get("name", existing.name)
                    existing.ci_type = node.get("ci_type", existing.ci_type)
                    existing.properties = node.get("properties", existing.properties)
                    existing.synced_at = datetime.now(UTC)
                    updated += 1
                else:
                    db.add(CmdbNode(
                        external_id=node["external_id"],
                        name=node.get("name", ""),
                        ci_type=node.get("ci_type", "unknown"),
                        source=source,
                        properties=node.get("properties", {}),
                        datasource_id=datasource_id,
                        space_id=space_id,
                        synced_at=datetime.now(UTC),
                    ))
                    created += 1
            await db.commit()

        return {"created": created, "updated": updated}

    async def _upsert_edges(
        self, edges: list[dict[str, Any]], datasource_id: str,
    ) -> dict[str, int]:
        """Upsert edges into cmdb_edges. Returns {created, deleted}."""
        created = 0
        source = edges[0].get("source", "") if edges else ""

        async with async_session_factory() as db:
            for edge in edges:
                src_result = await db.execute(
                    select(CmdbNode.id).where(
                        CmdbNode.external_id == edge["source_external_id"],
                    )
                )
                tgt_result = await db.execute(
                    select(CmdbNode.id).where(
                        CmdbNode.external_id == edge["target_external_id"],
                    )
                )
                src_id = src_result.scalar_one_or_none()
                tgt_id = tgt_result.scalar_one_or_none()
                if src_id and tgt_id:
                    db.add(CmdbEdge(
                        source_node_id=src_id,
                        target_node_id=tgt_id,
                        relation_type=edge.get("relation_type", "depends_on"),
                        properties=edge.get("properties", {}),
                        source=source,
                        datasource_id=datasource_id,
                    ))
                    created += 1
            await db.commit()

        return {"created": created, "deleted": 0}

    async def _cleanup_stale(
        self, datasource_id: str, active_external_ids: list[str],
    ) -> int:
        """Remove nodes no longer present in the source CMDB."""
        async with async_session_factory() as db:
            result = await db.execute(
                delete(CmdbNode).where(
                    CmdbNode.datasource_id == datasource_id,
                    CmdbNode.external_id.notin_(active_external_ids),
                )
            )
            await db.commit()
            return result.rowcount

    # ── Review Queue ─────────────────────────────────────────────────────

    async def approve_review_item(
        self, item_id: str, reviewer: str | None = None,
    ) -> dict[str, Any]:
        """Approve a review item — its transformed data is already in the graph."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbReviewItem).where(CmdbReviewItem.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                return {"success": False, "error": "Review item not found"}
            item.status = "approved"
            item.reviewer = reviewer
            item.reviewed_at = datetime.now(UTC)
            await db.commit()
            return {"success": True, "status": "approved"}

    async def reject_review_item(
        self, item_id: str, reviewer: str | None = None, note: str | None = None,
    ) -> dict[str, Any]:
        """Reject a review item."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbReviewItem).where(CmdbReviewItem.id == item_id)
            )
            item = result.scalar_one_or_none()
            if not item:
                return {"success": False, "error": "Review item not found"}
            item.status = "rejected"
            item.reviewer = reviewer
            item.review_note = note
            item.reviewed_at = datetime.now(UTC)
            await db.commit()
            return {"success": True, "status": "rejected"}

    # ── Helpers ─────────────────────────────────────────────────────────

    async def _load_datasource(self, datasource_id: str) -> DataSource | None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(DataSource).where(DataSource.id == datasource_id)
            )
            return result.scalar_one_or_none()

    async def _load_active_rule(self, datasource_id: str) -> dict[str, Any] | None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbMappingRule)
                .where(CmdbMappingRule.datasource_id == datasource_id)
                .where(CmdbMappingRule.status == "active")
                .order_by(CmdbMappingRule.version.desc())
                .limit(1)
            )
            rule = result.scalar_one_or_none()
            return {"rule_content": rule.rule_content} if rule else None

    async def _store_mapping_rule(
        self, datasource_id: str, rule: dict[str, Any], space_id: str | None,
    ) -> None:
        async with async_session_factory() as db:
            db.add(CmdbMappingRule(
                datasource_id=datasource_id,
                version=rule.get("version", 1),
                rule_content=rule.get("data", rule),
                status="draft",
                space_id=space_id,
            ))
            await db.commit()

    async def _create_sync_log(
        self, datasource_id: str, mode: str, space_id: str | None,
    ) -> CmdbSyncLog:
        async with async_session_factory() as db:
            sync_log = CmdbSyncLog(
                datasource_id=datasource_id,
                mode=mode,
                status="running",
                started_at=datetime.now(UTC),
                space_id=space_id,
            )
            db.add(sync_log)
            await db.commit()
            await db.refresh(sync_log)
            return sync_log

    async def _finalize_sync_log(
        self, sync_log_id: Any, node_stats: dict[str, int],
        edge_stats: dict[str, int], cleaned: int, review_count: int,
    ) -> None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbSyncLog).where(CmdbSyncLog.id == sync_log_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = "completed"
                log.nodes_created = node_stats.get("created", 0)
                log.nodes_updated = node_stats.get("updated", 0)
                log.nodes_deleted = cleaned
                log.edges_count = edge_stats.get("created", 0)
                log.review_count = review_count
                log.finished_at = datetime.now(UTC)
                await db.commit()

    async def _mark_sync_failed(self, sync_log_id: Any, error: str) -> None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(CmdbSyncLog).where(CmdbSyncLog.id == sync_log_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = "failed"
                log.errors_detail = {"error": error}
                log.finished_at = datetime.now(UTC)
                await db.commit()

    async def _create_review_item(
        self,
        sync_log_id: Any,
        review_type: str,
        source_data: dict[str, Any],
        transformed_data: dict[str, Any],
        llm_confidence: int,
        llm_reason: str,
        space_id: str | None = None,
        diff_summary: dict[str, Any] | None = None,
    ) -> None:
        async with async_session_factory() as db:
            db.add(CmdbReviewItem(
                sync_log_id=sync_log_id,
                review_type=review_type,
                source_data=source_data,
                transformed_data=transformed_data,
                llm_confidence=llm_confidence,
                llm_reason=llm_reason,
                diff_summary=diff_summary,
                status="pending",
                space_id=space_id,
            ))
            await db.commit()
