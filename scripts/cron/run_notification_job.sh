#!/bin/bash

set -euo pipefail

usage() {
    echo "usage: $0 <container_name> <job_path> [force_run] [test_mode]"
    exit 1
}

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
    usage
fi

CONTAINER_NAME=$1
JOB_PATH=$2
FORCE_RUN=${3:-0}
TEST_MODE=${4:-0}

RUNTIME_COMMAND='python'
if docker exec -i -w /app "$CONTAINER_NAME" test -x /app/.venv/bin/python; then
    RUNTIME_COMMAND='/app/.venv/bin/python'
elif docker exec -i -w /app "$CONTAINER_NAME" test -x /root/.local/bin/poetry; then
    RUNTIME_COMMAND='/root/.local/bin/poetry run python'
fi

# Run inside the existing app container so cron uses the deployed application and dependency set.
docker exec -i -w /app "$CONTAINER_NAME" sh -c \
    "$RUNTIME_COMMAND \"$JOB_PATH\" --force_run \"$FORCE_RUN\" --test_mode \"$TEST_MODE\""
