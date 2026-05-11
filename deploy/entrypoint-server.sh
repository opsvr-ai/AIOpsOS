#!/bin/sh
# AIOpsOS server entrypoint.
#
# Runs ``alembic upgrade head`` once, seeds the database with default data
# and built-in skills, then hands off to ``uvicorn``. Kept in its own file
# (rather than inline in the Dockerfile CMD) so:
#
#   * The exit code of each step is distinguishable in the container logs.
#   * Retrying the migration on a transient DB-not-ready error is cheap
#     to wire in if we need it later.
#   * Docker-compose healthcheck + restart policy interact predictably —
#     a migration failure exits 1 cleanly, and the ``unless-stopped``
#     policy will NOT retry a migration we know is already broken.
#
# Spec: .kiro/specs/agent-runtime-optimization-evolution — task 29.2.
set -e

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head
echo "[entrypoint] alembic upgrade complete"

echo "[entrypoint] seeding database (roles, permissions, admin user, skills)"
python -m scripts.seed
echo "[entrypoint] seed complete"

echo "[entrypoint] starting uvicorn on :8000"
exec uvicorn src.main:app --host 0.0.0.0 --port 8000
