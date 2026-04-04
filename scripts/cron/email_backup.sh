#!/bin/bash

set -euo pipefail

EMAIL_BACKUP_ENV_FILE=${EMAIL_BACKUP_ENV_FILE:-}
REDIS_DATA_PATH="/var/lib/redis"
REDIS_BACKUP_FILE_NAME="redis_backup_$(date "+%Y-%m-%dT%H:%M:%S%:z").zip"
MAILJET_PAYLOAD_FILE=""
REDIS_DATA_FILE=""

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
    rm -f "$MAILJET_PAYLOAD_FILE"
    rm -f "$REDIS_DATA_FILE"
    sudo rm -f "$REDIS_BACKUP_FILE_NAME"
}

if [ -z "$EMAIL_BACKUP_ENV_FILE" ] || [ ! -r "$EMAIL_BACKUP_ENV_FILE" ]; then
    usage
fi

# Keep secrets off the cron command line by loading them from a protected env file.
# shellcheck disable=SC1090
source "$EMAIL_BACKUP_ENV_FILE"

MAILJET_API_BASE_URL=${MAILJET_API_BASE_URL:-https://api.mailjet.com}

require_var MAILJET_API_KEY
require_var MAILJET_SECRET_KEY
require_var EMAIL_RECIPIENT
require_var EMAIL_SENDER
require_var REDIS_KEY
require_var MAILJET_REDIS_TEMPLATE_ID
require_var STOCKS_TELEGRAM_BOT_TOKEN
require_var STOCKS_TELEGRAM_CHANNEL_ID

trap cleanup EXIT

backup_redis() {
    # Only back up Redis operational state here; TLS private keys are intentionally excluded.
    sudo sh -c "cd $REDIS_DATA_PATH && zip -r $REDIS_BACKUP_FILE_NAME ."
    sudo mv "$REDIS_DATA_PATH/$REDIS_BACKUP_FILE_NAME" .
}

send_redis_mail() {
    local redis_data
    local unix_timestamp
    local redis_data_date
    local from_name

    redis_data=$(echo "zrange $REDIS_KEY -1 -1 withscores" | redis-cli)
    unix_timestamp=$(echo "$redis_data" | tail -1)
    redis_data_date=$(date -u -d @"$((unix_timestamp / 1000))" '+%Y-%m-%d')
    from_name="Market data notification"
    MAILJET_PAYLOAD_FILE=$(mktemp /tmp/mailjet-email-backup.XXXXXX.json)
    REDIS_DATA_FILE=$(mktemp /tmp/mailjet-redis-data.XXXXXX.txt)
    printf '%s' "$redis_data" > "$REDIS_DATA_FILE"

    python3 - "$MAILJET_PAYLOAD_FILE" \
        "$EMAIL_SENDER" \
        "$from_name" \
        "$EMAIL_RECIPIENT" \
        "$MAILJET_REDIS_TEMPLATE_ID" \
        "$redis_data_date" \
        "$REDIS_BACKUP_FILE_NAME" \
        "$REDIS_BACKUP_FILE_NAME" \
        "$REDIS_DATA_FILE" <<'PY'
import base64
import json
import sys

payload_file = sys.argv[1]
email_sender = sys.argv[2]
from_name = sys.argv[3]
email_recipient = sys.argv[4]
template_id = int(sys.argv[5])
redis_data_date = sys.argv[6]
backup_path = sys.argv[7]
backup_filename = sys.argv[8]
redis_data_path = sys.argv[9]

with open(redis_data_path, "r", encoding="utf-8") as handle:
    redis_data = handle.read()

with open(backup_path, "rb") as handle:
    backup_b64 = base64.b64encode(handle.read()).decode("ascii")

payload = {
    "Messages": [
        {
            "From": {
                "Email": email_sender,
                "Name": from_name,
            },
            "To": [
                {
                    "Email": email_recipient,
                }
            ],
            "TemplateID": template_id,
            "TemplateLanguage": True,
            "Variables": {
                "redis_data": redis_data,
                "redis_data_date": redis_data_date,
            },
            "Attachments": [
                {
                    "ContentType": "application/zip",
                    "Filename": backup_filename,
                    "Base64Content": backup_b64,
                }
            ],
        }
    ]
}

with open(payload_file, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY

    curl --fail --silent --show-error --request POST \
        --url "${MAILJET_API_BASE_URL}/v3.1/send" \
        --header "accept: application/json" \
        --header "Content-Type: application/json" \
        --user "${MAILJET_API_KEY}:${MAILJET_SECRET_KEY}" \
        --data-binary "@${MAILJET_PAYLOAD_FILE}"
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
