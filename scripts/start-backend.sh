#!/usr/bin/env bash
# AIOpsOS Backend Startup (Linux / macOS / Git Bash)
# For local dev: assumes DB/Redis/Kafka are running in Docker already.
# Usage: ./scripts/start-backend.sh [--no-env] [--no-migrate]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

NO_ENV=false
NO_MIGRATE=false
for arg in "$@"; do
  case "$arg" in
    --no-env)      NO_ENV=true ;;
    --no-migrate)  NO_MIGRATE=true ;;
  esac
done

echo "=============================================="
echo "  AIOpsOS - Backend Startup"
echo "=============================================="

# 0. Python env --------------------------------------------------------
init_python_env() {
  local hash_file="$ROOT_DIR/server/.poetry-hash"
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
    cat pyproject.toml poetry.lock 2>/dev/null | sha256sum | cut -d' ' -f1 > "$hash_file"
    return
  fi

  local current_hash; current_hash=$(cat pyproject.toml poetry.lock 2>/dev/null | sha256sum | cut -d' ' -f1)
  local saved_hash=""; [ -f "$hash_file" ] && saved_hash=$(cat "$hash_file")

  if [ "$current_hash" != "$saved_hash" ]; then
    echo "  Dependencies changed. Running poetry install..."
    poetry install --no-root
    echo "  Done."
    echo "$current_hash" > "$hash_file"
  else
    echo "  Python environment is up to date."
  fi
}

if [ "$NO_ENV" = false ]; then
  init_python_env
else
  echo ""
  echo "[0/3] Skipping environment (--no-env)"
fi

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
if [ "$NO_MIGRATE" = false ]; then
  echo ""
  echo "[2/3] Running database migrations..."
  cd "$ROOT_DIR/server"
  poetry run alembic upgrade head
  echo "  Migrations complete."
else
  echo ""
  echo "[2/3] Skipping migrations (--no-migrate)"
fi

# 3. FastAPI Server ----------------------------------------------------
echo ""
echo "[3/3] Starting FastAPI server..."
echo "  -> http://localhost:8000"
echo "  -> API docs: http://localhost:8000/docs"
echo ""

cd "$ROOT_DIR/server"
poetry run python run_server.py
