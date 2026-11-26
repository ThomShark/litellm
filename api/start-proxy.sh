#!/usr/bin/env bash
# Usage: start-proxy.sh <checkout dir> <port> <database name>
# Starts the QA proxy the way the repo's AGENTS.md says, against the dedicated
# Redis (127.0.0.1:6390) and Postgres (127.0.0.1:5440) containers.
set -euo pipefail
checkout=$1 port=$2 db=$3
here=$(cd "$(dirname "$0")" && pwd)
# NODECODE_API_KEY must already be exported by the caller (never written to disk here)
: "${NODECODE_API_KEY:?export NODECODE_API_KEY first}"
export DATABASE_URL="postgresql://proof:proof@127.0.0.1:5440/${db}"
export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-sk-local-proof-master-key}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export NO_PROXY=localhost,127.0.0.1
export NODECODE_INVALID_API_KEY=not-a-real-nodecode-key
cd "$checkout"
exec .venv/bin/python litellm/proxy/proxy_cli.py --config "$here/config.yaml" --host 127.0.0.1 --port "$port" \
  --num_workers 2 --detailed_debug --reload --use_v2_migration_resolver
