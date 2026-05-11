---
name: grafana
description: Fetch data from Grafana — dashboards, panels, alerts, annotations, datasources,
  and direct datasource queries via Grafana API.
version: 1.0.0
metadata:
  tags: grafana, monitoring, observability, dashboards, alerts, metrics, datasources,
    prometheus, visualization
  author: AIOpsOS
  related_skills: ''
license: MIT
---

# Grafana — Data Fetch & Query

Retrieve dashboards, panel data, alerts, annotations, datasource configurations, and execute queries against Grafana datasources (Prometheus, InfluxDB, etc.) through the Grafana HTTP API.

## When to Use

- List and search Grafana dashboards
- Get dashboard JSON model or panel data
- Query Prometheus/InfluxDB/etc via Grafana's datasource proxy
- Fetch alert rules and alert states
- Get and create annotations
- List datasource configurations (type, URL, status)
- Export dashboard definitions
- Integrate Grafana metrics into automated reports

## Prerequisites

- A running Grafana instance (URL + port)
- **API Key** or **Service Account Token** with appropriate permissions
  - Generate in Grafana UI: **Configuration → API Keys** or **Administration → Service Accounts**
  - Read-only operations need at least `Viewer` role
- `curl` and `jq` installed (for interactive use)

## Configuration

Set these environment variables or pass them inline:

```bash
export GRAFANA_URL="http://localhost:3000"
export GRAFANA_API_KEY="glsa_XXXXXXXXXXXXXXXXXXXXXXXX"
```

> **Security**: Prefer **Service Account tokens** over API keys. Tokens are user-bound and auditable. Never hardcode tokens in scripts.

## Usage

All examples use `curl` with `jq` for formatting. The pattern is:

```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/<endpoint>" | jq
```

---

### Dashboards

#### Search/List Dashboards

```bash
# List all dashboards (basic info)
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/search?type=dash-db" | jq

# Filter by folder or tag
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/search?type=dash-db&query=nginx&tag=production" | jq

# Starred dashboards only
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/search?type=dash-db&starred=true" | jq
```

#### Get Dashboard Detail (Full JSON Model)

```bash
# Replace UID with actual dashboard UID
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/dashboards/uid/YOUR_DASHBOARD_UID" | jq
```

Key fields in the response:
- `.dashboard.title` — Dashboard name
- `.dashboard.panels[]` — Array of panels with targets/queries
- `.dashboard.templating.list[]` — Template variables
- `.meta.slug` — URL-friendly slug
- `.meta.folderTitle` — Folder name

#### Get Dashboard Tags

```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/dashboards/tags" | jq
```

#### Get Home Dashboard

```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/dashboards/home" | jq
```

---

### Datasource Queries (via Grafana Proxy)

Query a datasource directly through Grafana's `/api/ds/query` endpoint. This is the most **powerful** feature — it lets you run raw PromQL, InfluxQL, SQL, etc. through Grafana without needing direct datasource credentials.

#### Step 1: Find Datasource UID

```bash
# List all datasources to find the UID
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources" | jq '[.[] | {name, type, uid, url}]'
```

#### Step 2: Query the Datasource

```bash
# PromQL query via Grafana's proxy
curl -s -X POST -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "queries": [{
      "refId": "A",
      "datasource": {"uid": "YOUR_DS_UID", "type": "prometheus"},
      "expr": "avg(rate(http_requests_total[5m])) by (status)",
      "range": {"from": "now-1h", "to": "now"},
      "intervalMs": 60000,
      "maxDataPoints": 100
    }]
  }' \
  "$GRAFANA_URL/api/ds/query" | jq
```

**Query parameters explained:**
| Field | Description |
|-------|-------------|
| `refId` | Arbitrary query ID (A, B, C...) |
| `datasource.uid` | Datasource UID from Step 1 |
| `datasource.type` | e.g. `prometheus`, `influxdb`, `elasticsearch` |
| `expr` | Raw query (PromQL, InfluxQL, etc.) depending on datasource type |
| `range.from` / `range.to` | Time range (ISO8601, relative like `now-1h`, or epoch ms) |
| `intervalMs` | Sampling interval in milliseconds |
| `maxDataPoints` | Max data points returned |

#### Simplified Bash Helper

```bash
# One-liner for quick PromQL queries
grafana_query() {
  local expr="$1"
  local ds_uid="${2:-YOUR_DS_UID}"
  local range="${3:-now-1h}"
  curl -s -X POST -H "Authorization: Bearer $GRAFANA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"quques\": [{
        \"refId\": \"A\",
        \"datasource\": {\"uid\": \"$ds_uid\", \"type\": \"prometheus\"},
        \"expr\": \"$expr\",
        \"range\": {\"from\": \"$range\", \"to\": \"now\"},
        \"intervalMs\": 60000,
        \"maxDataPoints\": 100
      }]
    }" \
    "$GRAFANA_URL/api/ds/query" | jq '.results.A.frames[0].data.values'
}

# Usage:
# grafana_query 'avg(rate(http_requests_total[5m]))'
```

---

### Alerts

#### List Alert Rules

```bash
# Grafana-managed alerts (v8+)
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/ruler/grafana/api/v1/rules" | jq

# Legacy dashboard alerts
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/alerts" | jq

# Alertmanager status
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/alertmanager/grafana/api/v2/alerts" | jq
```

#### Get Alert by ID

```bash
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/alerts/1" | jq
```

---

### Annotations

#### List Annotations

```bash
# All annotations (last hour)
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/annotations?from=$(date -d '1 hour ago' +%s)000&to=$(date +%s)000" | jq

# Filter by dashboard ID or tags
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/annotations?dashboardId=1&tags=deploy,production" | jq

# Grafana Annotations v2 (tags-based filtering)
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/annotations?type=annotation&limit=50" | jq
```

#### Create Annotation

```bash
curl -s -X POST -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dashboardUID": "YOUR_DASHBOARD_UID",
    "panelId": 1,
    "time": '"$(date +%s)000"',
    "text": "Deployment v1.2.3 completed",
    "tags": ["deploy", "production"]
  }' \
  "$GRAFANA_URL/api/annotations" | jq
```

---

### Datasources

```bash
# List all datasources
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources" | jq

# Get datasource by ID
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources/1" | jq

# Get datasource by name
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources/name/Prometheus" | jq

# Check datasource health (ping)
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources/1/health" | jq
```

---

### Organization & Users

```bash
# Current org
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/org" | jq

# Org users
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/org/users" | jq
```

---

### Health Check

```bash
# Grafana server health
curl -s "$GRAFANA_URL/api/health" | jq

# Returned example:
# {"database":"ok","version":"10.2.0"}
```

---

## Real-World Examples

### Example 1: Quick Health Report

```bash
#!/bin/bash
echo "=== Grafana Health Report ==="
echo ""
echo "Server Status:"
curl -s "$GRAFANA_URL/api/health" | jq -r '. | "  Version: \(.version), DB: \(.database)"'
echo ""
echo "Datasources:"
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources" | jq -r '.[] | "  \(.name) (\(.type)) — \(.url)"'
echo ""
echo "Active Alerts:"
curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/alerts?state=alerting" | jq -r '.[] | "  [\(.state)] \(.name)"'
```

### Example 2: Export Dashboard JSON

```bash
export_dashboard() {
  local uid="$1"
  local output="${2:-dashboard_${uid}.json}"
  curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
    "$GRAFANA_URL/api/dashboards/uid/$uid" | jq '.dashboard' > "$output"
  echo "Exported to $output"
}
# Usage: export_dashboard "your-dashboard-uid"
```

### Example 3: CPU/Memory Usage from Prometheus

```bash
DS_UID=$(curl -s -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/datasources" | jq -r '.[] | select(.type=="prometheus") | .uid' | head -1)

curl -s -X POST -H "Authorization: Bearer $GRAFANA_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"quques\": [{
      \"refId\": \"A\",
      \"datasource\": {\"uid\": \"$DS_UID\", \"type\": \"prometheus\"},
      \"expr\": \"100 - (avg(rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)\",
      \"range\": {\"from\": \"now-15m\", \"to\": \"now\"},
      \"intervalMs\": 15000,
      \"maxDataPoints\": 20
    }]
  }" \
  "$GRAFANA_URL/api/ds/query" | jq '.results.A.frames[0].data.values'
```

---

## Error Handling

| HTTP Code | Meaning | Likely Cause |
|-----------|---------|-------------|
| 401 | Unauthorized | Invalid/expired API key |
| 403 | Forbidden | Key lacks permissions |
| 404 | Not found | Wrong dashboard UID or endpoint |
| 408/504 | Timeout | Datasource unreachable or query too broad |
| 422/400 | Bad request | Malformed JSON or invalid query syntax |
| 500 | Server error | Grafana internal or datasource error |

```bash
# Quick connection test
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $GRAFANA_API_KEY" \
  "$GRAFANA_URL/api/health"
# Returns 200 if reachable + key works
```

## Notes

- API documentation: https://grafana.com/docs/grafana/latest/developers/http_api/
- The `/api/ds/query` endpoint may be called `/api/tsdb/query` in older Grafana versions (< v8)
- Rate limits apply per API key; excessive calls may be throttled
- For production automation, paginate large result sets with `limit` and `offset` parameters where supported
