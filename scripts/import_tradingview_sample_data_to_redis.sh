#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SAMPLE_DIR="${REPO_ROOT}/sample-data"

MODE="dev"
CONTAINER_NAME="redis"
REDIS_DB="0"
SCORE="$(date -u +%s)"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--mode dev|prod] [--container NAME] [--db N] [--score UNIX_SECONDS]

Imports TradingView sample payloads from sample-data/ into a local Docker Redis container.

Options:
  --mode       Redis key mode to load: dev or prod. Default: dev
  --container  Docker container name for Redis. Default: redis
  --db         Redis database number. Default: 0
  --score      Sorted-set score to use. Default: current UTC epoch seconds
  -h, --help   Show this help message

Examples:
  $(basename "$0")
  $(basename "$0") --mode prod
  $(basename "$0") --container redis --score 1774468860
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="${2:-}"
            shift 2
            ;;
        --container)
            CONTAINER_NAME="${2:-}"
            shift 2
            ;;
        --db)
            REDIS_DB="${2:-}"
            shift 2
            ;;
        --score)
            SCORE="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ "${MODE}" != "dev" && "${MODE}" != "prod" ]]; then
    echo "--mode must be either 'dev' or 'prod'" >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required but was not found on PATH" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but was not found on PATH" >&2
    exit 1
fi

if [[ ! -f "${SAMPLE_DIR}/stocks.json" || ! -f "${SAMPLE_DIR}/economy-indicator.json" ]]; then
    echo "Expected sample files were not found under ${SAMPLE_DIR}" >&2
    exit 1
fi

KEY_SUFFIX=""
if [[ "${MODE}" == "dev" ]]; then
    KEY_SUFFIX="-dev"
fi

STOCKS_KEY="tradingview-stocks${KEY_SUFFIX}"
ECONOMY_KEY="tradingview-economy_indicator${KEY_SUFFIX}"

normalize_json() {
    local input_file="$1"
    python3 - "$input_file" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
raw = path.read_text(encoding="utf-8").strip()
data = json.loads(raw)
while isinstance(data, str):
    data = json.loads(data)
print(json.dumps(data, separators=(",", ":")))
PY
}

STOCKS_JSON="$(normalize_json "${SAMPLE_DIR}/stocks.json")"
ECONOMY_JSON="$(normalize_json "${SAMPLE_DIR}/economy-indicator.json")"

docker exec "${CONTAINER_NAME}" redis-cli -n "${REDIS_DB}" DEL "${STOCKS_KEY}" "${ECONOMY_KEY}" >/dev/null
docker exec "${CONTAINER_NAME}" redis-cli -n "${REDIS_DB}" ZADD "${STOCKS_KEY}" "${SCORE}" "${STOCKS_JSON}" >/dev/null
docker exec "${CONTAINER_NAME}" redis-cli -n "${REDIS_DB}" ZADD "${ECONOMY_KEY}" "${SCORE}" "${ECONOMY_JSON}" >/dev/null

echo "Imported TradingView sample data into Redis container '${CONTAINER_NAME}'"
echo "Mode: ${MODE}"
echo "DB: ${REDIS_DB}"
echo "Score: ${SCORE}"
echo "Keys:"
echo "  ${STOCKS_KEY}"
echo "  ${ECONOMY_KEY}"
echo
echo "Verify with:"
echo "  docker exec ${CONTAINER_NAME} redis-cli -n ${REDIS_DB} ZRANGE ${STOCKS_KEY} 0 -1 WITHSCORES"
echo "  docker exec ${CONTAINER_NAME} redis-cli -n ${REDIS_DB} ZRANGE ${ECONOMY_KEY} 0 -1 WITHSCORES"
