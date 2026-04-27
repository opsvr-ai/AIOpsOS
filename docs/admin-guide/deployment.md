# 部署指南

## 环境要求

- Docker 24.0+ / Docker Compose 2.20+
- 空闲端口: 80, 8000, 5432, 6379, 9092
- 4GB+ 内存推荐

## Docker Compose 一键部署

```bash
cd deploy
cp .env.example .env
vim .env   # 配置 LLM_API_KEY
docker compose up -d
```

## 镜像构建

```bash
cd deploy
./build.sh                           # 构建全部
./build.sh --version v1.0.0 --push  # 指定版本并推送
```

## 开发模式

基础设施（db/redis/kafka）用 Docker，前后端本地运行：

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
cd server && uv run uvicorn src.main:app --reload --port 8000
cd web && pnpm dev
```

## 数据持久化

所有数据卷挂载到 `deploy/` 目录下：

| 目录 | 内容 |
|------|------|
| `db_data/` | PostgreSQL |
| `redis_data/` | Redis |
| `kafka_data/` | Kafka |
| `server_uploads/` | 文件上传 |
| `server_data/` | 应用数据 |

## 备份恢复

```bash
# 备份数据库
docker compose exec db pg_dump -U aiopsos aiopsos > backup.sql
# 恢复
docker compose exec -T db psql -U aiopsos aiopsos < backup.sql
```
