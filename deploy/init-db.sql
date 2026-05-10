-- AIOpsOS 数据库初始化脚本
-- 此脚本在 PostgreSQL 容器首次启动时自动执行

-- 启用 pgvector 扩展 (用于向量搜索/知识库)
CREATE EXTENSION IF NOT EXISTS vector;

-- 启用 uuid-ossp 扩展 (用于 UUID 生成)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 启用 pg_trgm 扩展 (用于模糊搜索)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 注意: 所有表结构由 Alembic 迁移脚本管理
-- 服务启动时会自动执行 `alembic upgrade head`
