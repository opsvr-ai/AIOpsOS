#!/bin/bash
# AIOpsOS 数据目录初始化脚本
# 在首次部署前运行此脚本，创建必要的目录并设置正确的权限
#
# 使用方法:
#   cd deploy
#   chmod +x init-dirs.sh
#   ./init-dirs.sh
#
# 目录结构:
#   ./data/                 - 所有数据的统一上级目录
#   ./data/postgres/        - PostgreSQL 数据 (UID 999)
#   ./data/redis/           - Redis 数据
#   ./data/kafka/           - Kafka 数据 (UID 1000)
#   ./data/server/          - 服务器数据 (日志、知识库等)
#   ./data/uploads/         - 上传文件

set -e

echo "=== AIOpsOS 数据目录初始化 ==="

# 创建统一的 data 目录及子目录
echo "[1/4] 创建数据目录..."
mkdir -p data/postgres
mkdir -p data/redis
mkdir -p data/kafka
mkdir -p data/server/logs
mkdir -p data/server/knowledge/wiki
mkdir -p data/server/knowledge/raw
mkdir -p data/server/knowledge/meta
mkdir -p data/uploads

# 设置 PostgreSQL 数据目录权限
# PostgreSQL 容器内部以 postgres 用户 (UID 999) 运行
echo "[2/4] 设置 PostgreSQL 目录权限 (UID 999)..."
sudo chown -R 999:999 data/postgres
chmod 700 data/postgres

# 设置 Kafka 数据目录权限
# Kafka 容器内部以 appuser (UID 1000) 运行
echo "[3/4] 设置 Kafka 目录权限 (UID 1000)..."
sudo chown -R 1000:1000 data/kafka
chmod 755 data/kafka

# 设置 Redis 和 Server 数据目录权限
# 这些容器以 root 运行，权限要求较宽松
echo "[4/4] 设置 Redis 和 Server 目录权限..."
chmod 755 data/redis
chmod -R 755 data/server
chmod -R 755 data/uploads

echo ""
echo "=== 初始化完成 ==="
echo ""
echo "目录结构:"
echo "  ./data/               - 统一数据目录 (方便迁移和备份)"
echo "  ./data/postgres/      - PostgreSQL 数据 (UID 999)"
echo "  ./data/redis/         - Redis 数据"
echo "  ./data/kafka/         - Kafka 数据 (UID 1000)"
echo "  ./data/server/        - 服务器数据 (日志、知识库等)"
echo "  ./data/uploads/       - 上传文件"
echo ""
echo "迁移说明:"
echo "  整个 data 目录可以直接打包迁移到新服务器"
echo "  tar -czvf aiopsos-data-backup.tar.gz data/"
echo ""
echo "现在可以运行: docker compose up -d"
