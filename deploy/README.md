# AIOpsOS 部署指南

## 环境要求

- **Docker** 24.0+
- **Docker Compose** 2.20+
- 空闲端口: `80`, `8000`, `5432`, `6379`, `9092`

## 快速开始

```bash
cd deploy

# 1. 创建配置文件
cp .env.example .env
vim .env   # 修改 SECRET_KEY、POSTGRES_PASSWORD 等必要配置

# 2. 初始化数据目录并设置权限
chmod +x init-dirs.sh
./init-dirs.sh

# 3. 构建并启动
docker compose build
docker compose up -d
```

启动后访问：
- 前端: `http://localhost`
- API 文档: `http://localhost:8000/docs`

### 首次配置

1. 访问前端，注册管理员账号
2. 进入 **控制中心 → 模型配置**
3. 添加 LLM 模型服务商（必须）
4. 添加 Embedding 模型服务商（知识库功能需要）

## 配置说明

所有配置通过 `deploy/.env` 管理：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_DB` | `aiopsos` | 数据库名称 |
| `POSTGRES_USER` | `aiopsos` | 数据库用户 |
| `POSTGRES_PASSWORD` | `aiopsos123` | 数据库密码（**生产环境务必修改**） |
| `POSTGRES_PORT` | `5432` | 数据库端口（宿主机） |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 异步数据库连接串 |
| `SYNC_DATABASE_URL` | `postgresql://...` | 同步数据库连接串（Alembic） |
| `REDIS_URL` | `redis://redis:6379` | Redis 连接串 |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka 地址 |
| `SECRET_KEY` | — | JWT 签名密钥（**生产环境务必修改**） |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 登录过期时间（分钟） |
| `SERVICE_TYPE` | `server` | 服务模式: server / worker / allinone |
| `PUBLIC_URL` | `http://localhost:8000` | 公开访问 URL |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `WIKI_PATH` | `data/knowledge` | 知识库存储路径 |
| `KB_MONITOR_ENABLED` | `true` | 知识库监控开关 |
| `KB_MONITOR_POLL_INTERVAL` | `30` | 知识库监控轮询间隔（秒） |
| `PROGRESS_ANALYSIS_INTERVAL` | `300` | 应急协同进度分析间隔（秒） |
| `PROGRESS_ANALYSIS_ENABLED` | `true` | 应急协同自动分析开关 |

### 模型配置

> **注意**: LLM 和 Embedding 模型配置已迁移到平台管理后台。
> 
> 首次部署后，请登录平台进入 **控制中心 → 模型配置** 添加模型服务商。

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
| `aiopsos-worker` | `aiopsos-server:latest` | — |
| `aiopsos-web` | `aiopsos-web:latest` | 80 |

## 数据持久化

所有数据挂载到 `deploy/` 下的本地目录，容器重启不会丢失：

| 目录 | 内容 | 权限 (UID:GID) |
|------|------|----------------|
| `deploy/db_data/` | PostgreSQL 数据库文件 | 999:999 |
| `deploy/redis_data/` | Redis 持久化数据 | 默认 |
| `deploy/kafka_data/` | Kafka 消息日志 | 1000:1000 |
| `deploy/server_uploads/` | 文件上传 | 默认 |
| `deploy/server_data/` | 应用数据（日志、知识库） | 默认 |

**首次部署前**，运行 `init-dirs.sh` 脚本创建目录并设置正确权限：
```bash
chmod +x init-dirs.sh
./init-dirs.sh
```

**手动设置权限**（如遇权限问题）：
```bash
# PostgreSQL 需要 UID 999
sudo chown -R 999:999 db_data
chmod 700 db_data

# Kafka 需要 UID 1000
sudo chown -R 1000:1000 kafka_data
```

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
docker compose down && rm -rf db_data redis_data kafka_data
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
docker compose exec -T db psql -U aiopsos aiopsos < backup_20260511.sql
```

## 数据备份与恢复

```bash
# 停止服务（确保数据一致性）
docker compose stop

# 备份所有数据目录
tar czf aiopsos_backup_$(date +%Y%m%d).tar.gz \
    db_data/ redis_data/ kafka_data/ server_data/ server_uploads/

# 重新启动服务
docker compose start

# 恢复数据
docker compose down
tar xzf aiopsos_backup_20260511.tar.gz
./init-dirs.sh  # 重新设置权限
docker compose up -d
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

**数据库权限问题**
```bash
# 检查目录权限
ls -la db_data/
# 重新设置权限
sudo chown -R 999:999 db_data
chmod 700 db_data
```

**前端页面空白**
```bash
curl -s http://localhost:8000/docs | head -20
curl -s http://localhost/api/v1/health
```

## 详细部署检查清单

完整的部署检查清单请参考 [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md)
