#!/usr/bin/env bash
# Usage: reproduce-reasoning.sh <proxy base url> <output dir>
# Needs LITELLM_MASTER_KEY (and NODECODE_API_KEY for the cache family) in the environment.
exec "${PYTHON:-python3}" "$(dirname "$0")/proof_client.py" reasoning "$@"
