# 配置说明

## 环境变量

所有配置通过 `deploy/.env` 管理。

### 数据库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POSTGRES_DB` | `aiopsos` | 数据库名称 |
| `POSTGRES_USER` | `aiopsos` | 数据库用户 |
| `POSTGRES_PASSWORD` | `aiopsos123` | 数据库密码 |
| `POSTGRES_PORT` | `5432` | 宿主机端口 |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 异步连接（应用） |
| `SYNC_DATABASE_URL` | `postgresql://...` | 同步连接（Alembic） |

### LLM

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | — | API 密钥 (必填) |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | API 地址 |
| `EMBEDDING_MODEL` | — | Embedding 模型 |
| `EMBEDDING_API_KEY` | — | Embedding 密钥 |
| `EMBEDDING_BASE_URL` | — | Embedding 地址 |

### 安全

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECRET_KEY` | — | JWT 签名密钥 (生产环境务必修改) |
| `JWT_ALGORITHM` | `HS256` | JWT 算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 登录过期时间 |

### 服务模式

| `SERVICE_TYPE` | 说明 |
|----------------|------|
| `allinone` | 单体模式（默认） |
| `control` | 仅管控面 |
| `execution` | 仅执行面 |

## 数据库迁移

Server 启动时自动执行 `alembic upgrade head`，也可手动：

```bash
docker compose exec server alembic upgrade head
docker compose exec server alembic downgrade -1  # 回滚
```
