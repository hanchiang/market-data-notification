#!/bin/bash

set -euo pipefail

EMAIL_BACKUP_ENV_FILE=${EMAIL_BACKUP_ENV_FILE:-}
REDIS_DATA_PATH="/var/lib/redis"
REDIS_BACKUP_FILE_NAME="redis_backup_$(date "+%Y-%m-%dT%H:%M:%S%:z").zip"

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
    sudo rm -f "$REDIS_BACKUP_FILE_NAME"
}

if [ -z "$EMAIL_BACKUP_ENV_FILE" ] || [ ! -r "$EMAIL_BACKUP_ENV_FILE" ]; then
    usage
fi

# Keep secrets off the cron command line by loading them from a protected env file.
# shellcheck disable=SC1090
source "$EMAIL_BACKUP_ENV_FILE"

require_var SENDGRID_API_KEY
require_var EMAIL_RECIPIENT
require_var EMAIL_SENDER
require_var REDIS_KEY
require_var SENDGRID_REDIS_TEMPLATE_ID
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
    local redis_file
    local maildata

    redis_data=$(echo "zrange $REDIS_KEY -1 -1 withscores" | redis-cli)
    redis_data=$(echo "$redis_data" | sed -e "s/\"/'/g")
    unix_timestamp=$(echo "$redis_data" | sed -r "s/.* ([0-9]+)/\1/g")
    redis_data_date=$(date -d @"$unix_timestamp" +%Y-%m-%d)
    from_name="han@market-data-notification"
    redis_file=$(base64 -w0 "$REDIS_BACKUP_FILE_NAME")

    maildata='{"personalizations":
      [
        {
          "to": [{"email": "'${EMAIL_RECIPIENT}'"}],
          "dynamic_template_data": {
            "redis_data": "'${redis_data}'",
            "redis_data_date": "'${redis_data_date}'"
          }
        }
      ],
      "from": {
        "email": "'${EMAIL_SENDER}'",
        "name": "'${from_name}'"
      },
      "template_id": "'${SENDGRID_REDIS_TEMPLATE_ID}'",
      "attachments": [
        {
          "content": "'${redis_file}'",
          "filename": "'${REDIS_BACKUP_FILE_NAME}'",
          "type": "application/zip",
          "disposition": "attachment"
        }
      ]
    }'

    curl --fail --silent --show-error --request POST \
        --url https://api.sendgrid.com/v3/mail/send \
        --header "Authorization: Bearer ${SENDGRID_API_KEY}" \
        --header "Content-Type: application/json" \
        --data "$maildata"
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
