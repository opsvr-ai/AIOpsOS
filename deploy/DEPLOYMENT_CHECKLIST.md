# AIOpsOS 正式环境部署检查清单

## 1. 环境准备

### 1.1 服务器要求
- [ ] Docker 24.0+ 已安装
- [ ] Docker Compose v2.20+ 已安装
- [ ] 至少 4GB 内存
- [ ] 至少 20GB 磁盘空间
- [ ] 开放端口: 80 (Web), 8000 (API), 5432 (PostgreSQL), 6379 (Redis), 9092 (Kafka)

### 1.2 网络配置
- [ ] 配置域名解析 (如需要)
- [ ] 配置 SSL 证书 (生产环境强烈建议)
- [ ] 配置防火墙规则

## 2. 配置文件检查

### 2.1 .env 文件
```bash
cd deploy
cp .env.example .env
```

**必须修改的配置项:**
- [ ] `SECRET_KEY` - 生成随机字符串: `openssl rand -hex 32`
- [ ] `POSTGRES_PASSWORD` - 设置强密码
- [ ] `PUBLIC_URL` - 设置公开访问 URL

**平台初始化配置 (部署后在管理后台配置):**
- [ ] 模型服务商 - 控制中心 → 模型配置 → 添加 LLM 服务商
- [ ] Embedding 模型 - 控制中心 → 模型配置 → 添加 Embedding 服务商

**可选配置项:**
- [ ] `LOG_LEVEL` - 生产环境建议设为 `INFO` 或 `WARNING`

### 2.2 docker-compose.yml
- [ ] 检查镜像源是否可访问 (`docker.1ms.run`)
- [ ] 如需修改端口映射，更新 `ports` 配置
- [ ] 检查数据卷挂载路径

## 3. 数据库初始化

### 3.1 PostgreSQL 扩展
`init-db.sql` 会自动安装以下扩展:
- [x] `vector` - pgvector 向量搜索
- [x] `uuid-ossp` - UUID 生成

以下扩展由 Alembic 迁移脚本创建:
- [x] `pg_trgm` - 模糊搜索 (迁移 008_tool_search_idx)

### 3.2 数据库迁移
服务启动时会自动执行 `alembic upgrade head`，无需手动操作。

## 4. 部署步骤

### 4.1 首次部署
```bash
cd deploy

# 1. 创建配置文件
cp .env.example .env
# 编辑 .env 文件，修改必要配置

# 2. 构建镜像
docker compose build

# 3. 启动服务
docker compose up -d

# 4. 查看日志
docker compose logs -f server
```

### 4.2 检查服务状态
```bash
# 查看所有服务状态
docker compose ps

# 检查健康状态
docker compose exec server curl -s http://localhost:8000/health

# 查看数据库迁移状态
docker compose exec server alembic current
```

### 4.3 访问服务
- Web 界面: http://your-server:80
- API 文档: http://your-server:8000/docs

### 4.4 首次登录配置
1. 访问 Web 界面，注册管理员账号
2. 进入 控制中心 → 模型配置
3. 添加 LLM 模型服务商 (必须)
4. 添加 Embedding 模型服务商 (知识库功能需要)

## 5. 应急协同功能配置

### 5.1 企业微信配置 (可选)
如需使用应急协同的企业微信群聊功能:
1. 登录 AIOpsOS 管理后台
2. 进入 控制中心 → 消息渠道
3. 添加企业微信渠道，配置:
   - 企业 ID (corp_id)
   - 应用 Secret (corp_secret)
   - 应用 AgentId (agent_id)

### 5.2 邮件配置 (可选)
如需使用应急协同的邮件通知功能:
1. 登录 AIOpsOS 管理后台
2. 进入 控制中心 → 消息渠道
3. 添加邮件渠道，配置:
   - SMTP 服务器地址
   - SMTP 端口
   - 发件人邮箱
   - 认证信息

## 6. 数据备份

### 6.1 数据库备份
```bash
# 备份数据库
docker compose exec db pg_dump -U aiopsos aiopsos > backup_$(date +%Y%m%d).sql

# 恢复数据库
docker compose exec -T db psql -U aiopsos aiopsos < backup_20260511.sql
```

### 6.2 数据卷备份
```bash
# 备份所有数据卷
docker run --rm -v aiopsos_db_data:/data -v $(pwd):/backup alpine tar czf /backup/db_data.tar.gz /data
```

## 7. 升级步骤

```bash
cd deploy

# 1. 拉取最新代码
git pull

# 2. 重新构建镜像
docker compose build

# 3. 停止旧服务
docker compose down

# 4. 启动新服务 (会自动执行数据库迁移)
docker compose up -d

# 5. 检查服务状态
docker compose ps
docker compose logs -f server
```

## 8. 故障排查

### 8.1 常见问题

**数据库连接失败:**
```bash
# 检查数据库容器状态
docker compose logs db

# 检查网络连接
docker compose exec server ping db
```

**Kafka 连接失败:**
```bash
# 检查 Kafka 容器状态
docker compose logs kafka

# Kafka 需要较长启动时间，等待 30 秒后重试
```

**迁移失败:**
```bash
# 查看迁移历史
docker compose exec server alembic history

# 手动执行迁移
docker compose exec server alembic upgrade head
```

### 8.2 日志位置
- 服务日志: `docker compose logs [service_name]`
- 应用日志: `./server_data/logs/`
- 数据库日志: `docker compose logs db`

## 9. 安全建议

- [ ] 修改所有默认密码
- [ ] 配置 HTTPS (使用 nginx 反向代理 + Let's Encrypt)
- [ ] 限制数据库端口仅内网访问
- [ ] 定期备份数据
- [ ] 监控服务健康状态
- [ ] 配置日志轮转

## 10. 新增功能说明 (v2026.5.11)

### 10.1 场景运维增强
- 支持三种场景类型: 命令式、自然语言式、混合式
- 场景执行记录追踪
- 关联资源管理 (工具、智能体、知识库)

### 10.2 应急协同功能
- 自动创建企业微信群聊
- 邮件通知
- 消息同步
- 进度分析和 AI 建议

### 10.3 新增数据表
以下表由 Alembic 迁移自动创建:
- `scenario_executions` - 场景执行记录
- `collaboration_sessions` - 协同会话
- `collaboration_messages` - 协同消息
- `collaboration_recommendations` - AI 建议
- `scenario_knowledge_docs` - 场景-知识库关联
- `scenario_channels` - 场景-通知渠道关联
- `message_sync_failures` - 消息同步失败记录
- `progress_analysis_records` - 进度分析记录
