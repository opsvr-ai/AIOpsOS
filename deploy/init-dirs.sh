#!/bin/bash
# AIOpsOS 数据目录初始化脚本
# 在首次部署前运行此脚本，创建必要的目录并设置正确的权限
#
# 使用方法:
#   cd deploy
#   chmod +x init-dirs.sh
#   ./init-dirs.sh

set -e

echo "=== AIOpsOS 数据目录初始化 ==="

# 创建所有数据目录
echo "[1/4] 创建数据目录..."
mkdir -p db_data
mkdir -p redis_data
mkdir -p kafka_data
mkdir -p server_data/logs
mkdir -p server_data/knowledge/wiki
mkdir -p server_data/knowledge/raw
mkdir -p server_data/knowledge/meta
mkdir -p server_uploads

# 设置 PostgreSQL 数据目录权限
# PostgreSQL 容器内部以 postgres 用户 (UID 999) 运行
echo "[2/4] 设置 PostgreSQL 目录权限 (UID 999)..."
sudo chown -R 999:999 db_data
chmod 700 db_data

# 设置 Kafka 数据目录权限
# Kafka 容器内部以 appuser (UID 1000) 运行
echo "[3/4] 设置 Kafka 目录权限 (UID 1000)..."
sudo chown -R 1000:1000 kafka_data
chmod 755 kafka_data

# 设置 Redis 和 Server 数据目录权限
# 这些容器以 root 运行，权限要求较宽松
echo "[4/4] 设置 Redis 和 Server 目录权限..."
chmod 755 redis_data
chmod -R 755 server_data
chmod -R 755 server_uploads

echo ""
echo "=== 初始化完成 ==="
echo ""
echo "目录结构:"
echo "  ./db_data/          - PostgreSQL 数据 (UID 999)"
echo "  ./redis_data/       - Redis 数据"
echo "  ./kafka_data/       - Kafka 数据 (UID 1000)"
echo "  ./server_data/      - 服务器数据 (日志、知识库等)"
echo "  ./server_uploads/   - 上传文件"
echo ""
echo "现在可以运行: docker compose up -d"
