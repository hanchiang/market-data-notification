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
    local unix_timestamp
    local redis_data_date
    local redis_data
    local redis_file

    unix_timestamp=$(redis-cli --raw zrange "$REDIS_KEY" -1 -1 withscores | tail -1)
    redis_data_date=$(date -u -d @"$((unix_timestamp / 1000))" '+%Y-%m-%d')
    redis_data="Full TradingView redis payload is attached in ${REDIS_BACKUP_FILE_NAME}."
    redis_file=$(base64 -w0 "$REDIS_BACKUP_FILE_NAME")
    EMAIL_PAYLOAD_FILE=$(mktemp /tmp/resend-email-backup.XXXXXX.json)

    python3 - "$EMAIL_PAYLOAD_FILE" \
        "$EMAIL_SENDER" \
        "$EMAIL_RECIPIENT" \
        "$RESEND_REDIS_TEMPLATE_ID" \
        "$redis_data" \
        "$redis_data_date" \
        "$REDIS_BACKUP_FILE_NAME" \
        "$redis_file" <<'PY'
import json
import sys

payload_file = sys.argv[1]
email_sender = sys.argv[2]
email_recipient = sys.argv[3]
template_id = sys.argv[4]
redis_data = sys.argv[5]
redis_data_date = sys.argv[6]
backup_filename = sys.argv[7]
backup_b64 = sys.argv[8]

payload = {
    "from": email_sender,
    "to": [email_recipient],
    "template": {
        "id": template_id,
        "variables": {
            "redis_data": redis_data,
            "redis_data_date": redis_data_date,
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
