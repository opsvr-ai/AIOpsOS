#!/usr/bin/env bash
# AIOpsOS Frontend Startup (Linux / macOS / Git Bash)
# Usage: ./scripts/start-frontend.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "=============================================="
echo "  AIOpsOS - Frontend Startup"
echo "=============================================="

# 1. Node / pnpm ---------------------------------------------------------
echo ""
echo "[1/2] Checking Node.js environment..."

if ! command -v node &> /dev/null; then
  echo "  ERROR: Node.js is not installed."
  echo "  Install: https://nodejs.org/ (LTS recommended)"
  exit 1
fi

if ! command -v pnpm &> /dev/null; then
  echo "  pnpm not found. Installing via corepack..."
  corepack enable
  corepack prepare pnpm@latest --activate
fi

# 2. Install deps + Dev Server -------------------------------------------
echo ""
echo "[2/2] Syncing dependencies and starting Vite..."
cd "$ROOT_DIR/web"
pnpm install

echo ""
echo "Starting Vite dev server..."
echo "  -> http://localhost:5173"
echo ""

pnpm dev
