# AIOpsOS 部署指南

## 环境要求

- **Docker** 24.0+
- **Docker Compose** 2.20+
- 空闲端口: `80`, `8000`, `5432`, `6379`, `9092`

## 快速开始

```bash
cd deploy
cp .env.example .env
vim .env   # 修改 LLM_API_KEY 等必要配置
docker compose up -d
```

启动后访问：
- 前端: `http://localhost`
- API 文档: `http://localhost:8000/docs`

## 配置说明

所有配置通过 `deploy/.env` 管理：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_DB` | `aiopsos` | 数据库名称 |
| `POSTGRES_USER` | `aiopsos` | 数据库用户 |
| `POSTGRES_PASSWORD` | `aiopsos123` | 数据库密码 |
| `POSTGRES_PORT` | `5432` | 数据库端口（宿主机） |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 异步数据库连接串 |
| `SYNC_DATABASE_URL` | `postgresql://...` | 同步数据库连接串（Alembic） |
| `REDIS_URL` | `redis://redis:6379` | Redis 连接串 |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka 地址 |
| `SECRET_KEY` | — | JWT 签名密钥（**生产环境务必修改**） |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 登录过期时间（分钟） |
| `SERVICE_TYPE` | `allinone` | 服务模式 |
| `LLM_API_KEY` | — | LLM API 密钥（**必填**） |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | LLM API 地址 |
| `EMBEDDING_MODEL` | — | Embedding 模型名称 |
| `EMBEDDING_API_KEY` | — | Embedding API 密钥 |
| `EMBEDDING_BASE_URL` | — | Embedding API 地址 |

## 镜像构建

```bash
# 构建全部服务
./build.sh

# 指定版本 + 推送
./build.sh --version v1.0.0 --registry docker.1ms.run --push

# 仅构建 server，无缓存
./build.sh --services server --no-cache
```

完整选项：

```
Usage: ./build.sh [OPTIONS]

Options:
  --version VERSION    Image version tag (default: YYYYMMDD-HHMMSS)
  --registry REGISTRY  Docker registry prefix
  --push              Push images after building
  --no-cache          Build without Docker layer cache
  --services LIST     Comma-separated services (default: server,web)
```

## 服务架构

```
Browser :80 → web (nginx) → /api/ → server:8000 (FastAPI)
                                      │
                         ┌────────────┼────────────┐
                         ▼            ▼            ▼
                        db           redis        kafka
                      :5432         :6379        :9092
```

### 容器列表

| 容器 | 镜像 | 端口 |
|------|------|------|
| `aiopsos-db` | `pgvector/pgvector:pg15` | 5432 |
| `aiopsos-redis` | `redis:7-alpine` | 6379 |
| `aiopsos-kafka` | `cp-kafka:7.5.0` | 9092 |
| `aiopsos-server` | `aiopsos-server:latest` | 8000 |
| `aiopsos-web` | `aiopsos-web:latest` | 80 |

## 数据持久化

所有数据挂载到 `deploy/` 下的本地目录，容器重启不会丢失：

| 目录 | 内容 |
|------|------|
| `deploy/db_data/` | PostgreSQL 数据库文件 |
| `deploy/redis_data/` | Redis 持久化数据 |
| `deploy/kafka_data/` | Kafka 消息日志 |
| `deploy/server_uploads/` | 文件上传 |
| `deploy/server_data/` | 应用数据 |

## 常用操作

```bash
# 查看日志
docker compose logs -f server

# 查看所有容器状态
docker compose ps

# 重启单个服务
docker compose restart server

# 重建并重启（代码变更后）
./build.sh && docker compose up -d --force-recreate server

# 停止所有服务
docker compose down

# 停止并删除数据 (⚠️ 数据将丢失)
docker compose down -v
```

## 数据库

```bash
# Server 启动时自动执行迁移，手动运行：
docker compose exec server alembic upgrade head

# 进入数据库
docker compose exec db psql -U aiopsos -d aiopsos

# 备份
docker compose exec db pg_dump -U aiopsos aiopsos > backup_$(date +%Y%m%d).sql

# 恢复
docker compose exec -T db psql -U aiopsos aiopsos < backup_20260427.sql
```

## 开发模式

使用 `docker-compose.dev.yml` 仅启动基础设施，前后端在本地开发：

```bash
# 启动基础设施
docker compose -f docker-compose.dev.yml up -d

# 后端（本地热重载）
cd server && uv run uvicorn src.main:app --reload --port 8000

# 前端（本地 HMR）
cd web && pnpm dev
```

## 故障排查

**服务无法启动**
```bash
# 检查端口占用
ss -tlnp | grep -E '80|8000|5432|6379|9092'
# 查看日志
docker compose logs server
```

**数据库连接失败**
```bash
docker compose exec db pg_isready -U aiopsos -d aiopsos
```

**前端页面空白**
```bash
curl -s http://localhost:8000/docs | head -20
curl -s http://localhost/api/v1/health
```
