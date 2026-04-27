# API 概览

## API 文档

启动服务后访问 Swagger UI: `http://localhost:8000/docs`

## 主要 API 分组

### 控制面 (`/api/v1`)

| 分组 | 路径 | 说明 |
|------|------|------|
| users | `/api/v1/users` | 用户 CRUD |
| agents | `/api/v1/agents` | 智能体管理 |
| tools | `/api/v1/tools` | 工具注册 |
| schedules | `/api/v1/schedules` | 定时任务 |
| cron | `/api/v1/cron` | Cron 任务 |
| channels | `/api/v1/channels` | 通知渠道 |
| knowledge | `/api/v1/knowledge` | 知识库 |
| memory | `/api/v1/memories` | 记忆管理 |
| docs | `/api/v1/docs` | 文档中心 |

### 执行面

| 分组 | 路径 | 说明 |
|------|------|------|
| chat | `/api/v1/chat` | 对话接口 |
| chat/stream | `/api/v1/chat/stream` | 流式对话 |
| sessions | `/api/v1/sessions` | 会话管理 |
| alerts | `/api/v1/alerts` | 告警管理 |

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/login` | 登录 |
| POST | `/api/v1/auth/register` | 注册 |
| POST | `/api/v1/auth/refresh` | 刷新令牌 |

## 认证方式

在请求头中携带 JWT Token：

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```
