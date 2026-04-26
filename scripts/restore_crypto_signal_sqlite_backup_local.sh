#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_FILE=""
OUTPUT_DB="${REPO_ROOT}/var/crypto_signal/prod-review/crypto_signal.sqlite3"
OVERWRITE="false"
TMP_DB=""

usage() {
    cat <<EOF
Usage: $(basename "$0") --backup PATH [--output PATH] [--overwrite]

Restores a downloaded crypto-signal SQLite backup into a separate local review
database. The default output path intentionally does not replace the normal
local crypto_signal.sqlite3 file.

Options:
  --backup     Backup file to restore. Supports .sqlite3 and .sqlite3.gz.
  --output     Local SQLite output path.
               Default: var/crypto_signal/prod-review/crypto_signal.sqlite3
  --overwrite  Replace the output file if it already exists.
  -h, --help   Show this help message

Example:
  $(basename "$0") --backup /tmp/crypto_signal_backups/crypto_signal_20260426T120000Z.sqlite3.gz
EOF
}

cleanup() {
    if [[ -n "${TMP_DB}" && -f "${TMP_DB}" ]]; then
        rm -f "${TMP_DB}"
    fi
}

trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup)
            BACKUP_FILE="${2:-}"
            shift 2
            ;;
        --output)
            OUTPUT_DB="${2:-}"
            shift 2
            ;;
        --overwrite)
            OVERWRITE="true"
            shift
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

if [[ -z "${BACKUP_FILE}" ]]; then
    echo "--backup is required" >&2
    usage >&2
    exit 1
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
    echo "Backup file does not exist: ${BACKUP_FILE}" >&2
    exit 1
fi

if [[ -z "${OUTPUT_DB}" ]]; then
    echo "--output must not be empty" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 is required but was not found on PATH" >&2
    exit 1
fi

if [[ "${BACKUP_FILE}" == *.gz ]] && ! command -v gzip >/dev/null 2>&1; then
    echo "gzip is required for .gz backups but was not found on PATH" >&2
    exit 1
fi

output_dir="$(dirname "${OUTPUT_DB}")"
mkdir -p "${output_dir}"

if [[ -e "${OUTPUT_DB}" && "${OVERWRITE}" != "true" ]]; then
    echo "Output DB already exists: ${OUTPUT_DB}" >&2
    echo "Pass --overwrite to replace it, or choose a different --output path." >&2
    exit 1
fi

TMP_DB="$(mktemp "${output_dir}/crypto_signal_restore.XXXXXX.sqlite3")"

if [[ "${BACKUP_FILE}" == *.gz ]]; then
    gzip -cd "${BACKUP_FILE}" > "${TMP_DB}"
else
    cp "${BACKUP_FILE}" "${TMP_DB}"
fi

integrity_result="$(sqlite3 "${TMP_DB}" 'pragma integrity_check;')"
if [[ "${integrity_result}" != "ok" ]]; then
    echo "SQLite integrity check failed:" >&2
    echo "${integrity_result}" >&2
    exit 1
fi

mv "${TMP_DB}" "${OUTPUT_DB}"
TMP_DB=""

echo "Restored crypto signal review DB: ${OUTPUT_DB}"
echo "SQLite integrity_check: ok"
echo
sqlite3 "${OUTPUT_DB}" <<'SQL'
select 'runs', count(*) from crypto_signal_runs;
select 'snapshots', count(*) from crypto_signal_coin_snapshots;
select 'latest_run_utc', max(run_timestamp_utc) from crypto_signal_runs;
SQL

relative_output="${OUTPUT_DB}"
case "${OUTPUT_DB}" in
    "${REPO_ROOT}"/*)
        relative_output="${OUTPUT_DB#"${REPO_ROOT}/"}"
        ;;
esac

echo
echo "Render without Telegram or provider calls with:"
echo "  ENV=dev CRYPTO_SIGNAL_DB_PATH='${relative_output}' PYTHONPATH=\"\$(pwd)\" poetry run python src/job/crypto/crypto_signal_report.py --window 7d --limit 3 --send_telegram=0 --test_mode=0"
