#!/bin/bash

set -euo pipefail

EMAIL_BACKUP_ENV_FILE=${EMAIL_BACKUP_ENV_FILE:-}
REDIS_DATA_PATH="/var/lib/redis"
REDIS_BACKUP_FILE_NAME="redis_backup_$(date "+%Y-%m-%dT%H:%M:%S%:z").zip"
EMAIL_PAYLOAD_FILE=""

usage() {
    echo "EMAIL_BACKUP_ENV_FILE must point to a readable env file"
    exit 1
}

require_var() {
    local var_name=$1
    if [ -z "${!var_name:-}" ]; then
        echo "$var_name is required"
        exit 1
    fi
}

cleanup() {
    rm -f "$EMAIL_PAYLOAD_FILE"
    sudo rm -f "$REDIS_BACKUP_FILE_NAME"
}

if [ -z "$EMAIL_BACKUP_ENV_FILE" ] || [ ! -r "$EMAIL_BACKUP_ENV_FILE" ]; then
    usage
fi

# Keep secrets off the cron command line by loading them from a protected env file.
# shellcheck disable=SC1090
source "$EMAIL_BACKUP_ENV_FILE"

require_var RESEND_API_KEY
require_var EMAIL_RECIPIENT
require_var EMAIL_SENDER
require_var REDIS_KEY
require_var RESEND_REDIS_TEMPLATE_ID
require_var STOCKS_TELEGRAM_BOT_TOKEN
require_var STOCKS_TELEGRAM_CHANNEL_ID

trap cleanup EXIT

backup_redis() {
    # Only back up Redis operational state here; TLS private keys are intentionally excluded.
    sudo sh -c "cd $REDIS_DATA_PATH && zip -r $REDIS_BACKUP_FILE_NAME ."
    sudo mv "$REDIS_DATA_PATH/$REDIS_BACKUP_FILE_NAME" .
}

send_redis_mail() {
    local redis_score
    local backup_date
    local email_body

    redis_score=$(redis-cli --raw zrange "$REDIS_KEY" -1 -1 withscores | tail -1)
    if ! [[ "$redis_score" =~ ^[0-9]+$ ]]; then
        echo "Unable to derive backup date from Redis score for key: $REDIS_KEY" >&2
        exit 1
    fi
    if (( redis_score > 9999999999 )); then
        redis_score=$((redis_score / 1000))
    fi
    backup_date=$(date -u -d @"$redis_score" '+%Y-%m-%d')
    email_body="Full TradingView redis payload is attached in ${REDIS_BACKUP_FILE_NAME}."
    EMAIL_PAYLOAD_FILE=$(mktemp /tmp/resend-email-backup.XXXXXX.json)

    python3 - "$EMAIL_PAYLOAD_FILE" \
        "$EMAIL_SENDER" \
        "$EMAIL_RECIPIENT" \
        "$RESEND_REDIS_TEMPLATE_ID" \
        "$email_body" \
        "$backup_date" \
        "$REDIS_BACKUP_FILE_NAME" <<'PY'
import base64
import json
import sys

payload_file = sys.argv[1]
email_sender = sys.argv[2]
email_recipient = sys.argv[3]
template_id = sys.argv[4]
email_body = sys.argv[5]
backup_date = sys.argv[6]
backup_filename = sys.argv[7]

with open(backup_filename, "rb") as handle:
    backup_b64 = base64.b64encode(handle.read()).decode("ascii")

payload = {
    "from": email_sender,
    "to": [email_recipient],
    "template": {
        "id": template_id,
        "variables": {
            "email_body": email_body,
            "backup_date": backup_date,
            # Keep the original variable names until the hosted template is updated.
            "redis_data": email_body,
            "redis_data_date": backup_date,
        },
    },
    "attachments": [
        {
            "filename": backup_filename,
            "content": backup_b64,
        }
    ],
    "headers": {
        "X-Entity-Ref-ID": backup_filename,
    },
}

with open(payload_file, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

    curl --fail --silent --show-error --request POST \
        --url "https://api.resend.com/emails" \
        --header "accept: application/json" \
        --header "Content-Type: application/json" \
        --header "Authorization: Bearer ${RESEND_API_KEY}" \
        --data-binary "@${EMAIL_PAYLOAD_FILE}"
}

notify_telegram() {
    local now

    now=$(date +%Y-%m-%dT%H:%M:%S%:z)
    curl --fail --silent --show-error --request POST \
        "https://api.telegram.org/bot${STOCKS_TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${STOCKS_TELEGRAM_CHANNEL_ID}" \
        --data-urlencode "text=[Email backup] Market data notification redis data /var/lib/redis backed up to email at ${now}."
}

backup_redis
send_redis_mail
notify_telegram
