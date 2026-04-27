# 安全指南

## 认证

系统使用 JWT Bearer Token 认证，登录接口返回 access token：

```
Authorization: Bearer <token>
```

## 权限模型 (RBAC)

```
User → UserRole → Role → RolePermission → Permission
```

- **用户** (User) — 系统使用者
- **角色** (Role) — 权限集合（如 admin / operator / viewer）
- **权限** (Permission) — 具体操作许可（如 alerts.view / tools.manage）

## 用户管理

管理员在「控制中心 → 身份与权限」中管理：
- 创建/编辑/禁用用户
- 分配角色
- 创建自定义角色和权限

## 生产环境检查清单

- [ ] 修改 `SECRET_KEY` 为强随机字符串
- [ ] 修改数据库密码 `POSTGRES_PASSWORD`
- [ ] 修改默认 admin 密码
- [ ] 配置 HTTPS（Nginx 反向代理 + Let's Encrypt）
- [ ] 限制 `CORS` 允许的域名
- [ ] 配置日志级别为 INFO 或 WARNING
- [ ] LLM API Key 使用最小权限密钥
