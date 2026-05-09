#!/usr/bin/env bash
# AIOpsOS Full Stack Startup (Linux / macOS / Git Bash)
# For local dev: assumes DB/Redis/Kafka are running in Docker already.
# Ctrl+C stops everything.
# Usage: ./scripts/start-all.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

cleanup() {
  echo ""
  echo "Shutting down..."
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  echo "Stopped."
}
trap cleanup EXIT INT TERM

echo "=============================================="
echo "  AIOpsOS - Full Stack Startup"
echo "=============================================="

# 0. Python env --------------------------------------------------------
echo ""
echo "[0/3] Initializing Python environment..."

if ! command -v poetry &> /dev/null; then
  echo "  ERROR: Poetry is not installed."
  echo "  Install: curl -sSL https://install.python-poetry.org | python3 -"
  exit 1
fi

cd "$ROOT_DIR/server"
if [ ! -d ".venv" ]; then
  echo "  No venv found. Running poetry install..."
  poetry install --no-root
  echo "  Done."
  cat pyproject.toml poetry.lock 2>/dev/null | sha256sum | cut -d' ' -f1 > .poetry-hash
elif [ -f ".poetry-hash" ]; then
  current=$(cat pyproject.toml poetry.lock 2>/dev/null | sha256sum | cut -d' ' -f1)
  saved=$(cat .poetry-hash)
  if [ "$current" != "$saved" ]; then
    echo "  Dependencies changed. Running poetry install..."
    poetry install --no-root
    echo "  Done."
    echo "$current" > .poetry-hash
  else
    echo "  Python environment is up to date."
  fi
else
  echo "  Environment exists. Running poetry install to sync..."
  poetry install --no-root
  echo "  Done."
  cat pyproject.toml poetry.lock 2>/dev/null | sha256sum | cut -d' ' -f1 > .poetry-hash
fi
cd "$ROOT_DIR"

# 1. Check infrastructure connectivity ----------------------------------
check_port() {
  local host="$1" port="$2" name="$3"
  if timeout 2 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null; then
    echo "  $name ($host:$port) - OK"
  else
    echo "  ERROR: $name ($host:$port) is not reachable."
    echo "  Start infrastructure first: docker compose -f deploy/docker-compose.dev.yml up -d"
    exit 1
  fi
}

echo ""
echo "[1/3] Checking infrastructure connectivity..."
check_port "localhost" "5432" "PostgreSQL"
check_port "localhost" "6379" "Redis"
check_port "localhost" "9094" "Kafka"
echo "  All services reachable."

# 2. DB Migrations -----------------------------------------------------
echo ""
echo "[2/3] Running database migrations..."
cd "$ROOT_DIR/server"
poetry run alembic upgrade head
echo "  Migrations complete."

# 3. Backend + Frontend ------------------------------------------------
echo ""
echo "[3/3] Starting services..."
echo "  Backend  -> http://localhost:8000  (docs: http://localhost:8000/docs)"
echo "  Frontend -> http://localhost:5173"
echo ""

cd "$ROOT_DIR/server"
poetry run python run_server.py &
BACKEND_PID=$!

cd "$ROOT_DIR/web"
echo "  Syncing frontend dependencies..."
pnpm install

sleep 2
pnpm dev
