#!/usr/bin/env bash

set -euo pipefail

SSH_TARGET="production"
REMOTE_BASE_DIR="__HOME__/market_data_notification_jobs"
LOCAL_BACKUP_DIR="/tmp/crypto_signal_backups"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--ssh-target TARGET] [--remote-base-dir DIR] [--local-backup-dir DIR]

Creates a consistent production crypto-signal SQLite backup with sqlite3 .backup,
compresses it on the remote host, and downloads the gzip artifact locally.

Options:
  --ssh-target        SSH target or user@host. Default: production
  --remote-base-dir   Remote market_data_notification_jobs directory.
                      Default: \$HOME/market_data_notification_jobs
  --local-backup-dir  Local directory for downloaded backups.
                      Default: /tmp/crypto_signal_backups
  -h, --help          Show this help message

Example:
  $(basename "$0") --ssh-target production
  $(basename "$0") --ssh-target user@example.com --local-backup-dir /tmp/crypto_signal_backups
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssh-target)
            SSH_TARGET="${2:-}"
            shift 2
            ;;
        --remote-base-dir)
            REMOTE_BASE_DIR="${2:-}"
            shift 2
            ;;
        --local-backup-dir)
            LOCAL_BACKUP_DIR="${2:-}"
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

if [[ -z "${SSH_TARGET}" ]]; then
    echo "--ssh-target must not be empty" >&2
    exit 1
fi

if [[ -z "${REMOTE_BASE_DIR}" ]]; then
    echo "--remote-base-dir must not be empty" >&2
    exit 1
fi

if [[ -z "${LOCAL_BACKUP_DIR}" ]]; then
    echo "--local-backup-dir must not be empty" >&2
    exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
    echo "ssh is required but was not found on PATH" >&2
    exit 1
fi

if ! command -v scp >/dev/null 2>&1; then
    echo "scp is required but was not found on PATH" >&2
    exit 1
fi

mkdir -p "${LOCAL_BACKUP_DIR}"

printf -v REMOTE_BASE_DIR_QUOTED '%q' "${REMOTE_BASE_DIR}"

remote_output="$(
    ssh "${SSH_TARGET}" "REMOTE_BASE_DIR=${REMOTE_BASE_DIR_QUOTED} bash -s" <<'REMOTE'
set -euo pipefail

if ! command -v gzip >/dev/null 2>&1; then
    echo "gzip is required on the remote host" >&2
    exit 1
fi

if [[ "${REMOTE_BASE_DIR}" == "__HOME__/"* ]]; then
    remote_base_dir="${HOME}/${REMOTE_BASE_DIR#__HOME__/}"
else
    remote_base_dir="${REMOTE_BASE_DIR}"
fi
db="${remote_base_dir}/crypto_signal/crypto_signal.sqlite3"
backup_dir="${remote_base_dir}/backups/crypto_signal"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="${backup_dir}/crypto_signal_${stamp}.sqlite3"
backup_gz="${backup}.gz"

if [[ ! -f "${db}" ]]; then
    echo "missing crypto signal SQLite DB: ${db}" >&2
    exit 1
fi

mkdir -p "${backup_dir}"
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "${db}" ".backup '${backup}'"
elif command -v python3 >/dev/null 2>&1; then
    python3 - "${db}" "${backup}" <<'PY'
import sqlite3
import sys

source_path = sys.argv[1]
backup_path = sys.argv[2]

with sqlite3.connect(source_path) as source:
    with sqlite3.connect(backup_path) as destination:
        source.backup(destination)
PY
else
    echo "sqlite3 or python3 is required on the remote host" >&2
    exit 1
fi
gzip -kf "${backup}"
sha256="$(sha256sum "${backup_gz}" | awk '{print $1}')"

printf 'CRYPTO_SIGNAL_BACKUP_GZ=%s\n' "${backup_gz}"
printf 'CRYPTO_SIGNAL_BACKUP_SHA256=%s\n' "${sha256}"
REMOTE
)"

echo "${remote_output}"

remote_backup_gz="$(
    printf '%s\n' "${remote_output}" \
        | sed -n 's/^CRYPTO_SIGNAL_BACKUP_GZ=//p' \
        | tail -1
)"
remote_sha256="$(
    printf '%s\n' "${remote_output}" \
        | sed -n 's/^CRYPTO_SIGNAL_BACKUP_SHA256=//p' \
        | tail -1
)"

if [[ -z "${remote_backup_gz}" || -z "${remote_sha256}" ]]; then
    echo "Unable to parse remote backup output" >&2
    exit 1
fi

local_backup="${LOCAL_BACKUP_DIR}/$(basename "${remote_backup_gz}")"
scp "${SSH_TARGET}:${remote_backup_gz}" "${local_backup}"

local_sha256="$(sha256sum "${local_backup}" | awk '{print $1}')"
if [[ "${local_sha256}" != "${remote_sha256}" ]]; then
    echo "Downloaded backup checksum mismatch" >&2
    echo "remote: ${remote_sha256}" >&2
    echo "local:  ${local_sha256}" >&2
    exit 1
fi

echo "Downloaded crypto signal backup: ${local_backup}"
echo "SHA256: ${local_sha256}"
echo
echo "Restore locally with:"
echo "  ./scripts/restore_crypto_signal_sqlite_backup_local.sh --backup '${local_backup}'"
