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
    local redis_file
    local maildata

    redis_data=$(echo "zrange $REDIS_KEY -1 -1 withscores" | redis-cli)
    unix_timestamp=$(echo "$redis_data" | tail -1)
    redis_data_date=$(date -u -d @"$((unix_timestamp / 1000))" '+%Y-%m-%d')
    from_name="Market data notification"
    redis_file=$(base64 -w0 "$REDIS_BACKUP_FILE_NAME")

    maildata='{
      "Messages": [
        {
          "From": {
            "Email": "'${EMAIL_SENDER}'",
            "Name": "'${from_name}'"
          },
          "To": [{"Email": "'${EMAIL_RECIPIENT}'"}],
          "TemplateID": '${MAILJET_REDIS_TEMPLATE_ID}',
          "TemplateLanguage": true,
          "Variables": {
            "redis_data": "'${redis_data}'",
            "redis_data_date": "'${redis_data_date}'"
          },
          "Attachments": [
            {
              "ContentType": "application/zip",
              "Filename": "'${REDIS_BACKUP_FILE_NAME}'",
              "Base64Content": "'${redis_file}'"
            }
          ]
        }
      ]
    }'

    curl --fail --silent --show-error --request POST \
        --url "${MAILJET_API_BASE_URL}/v3.1/send" \
        --header "accept: application/json" \
        --header "Content-Type: application/json" \
        --user "${MAILJET_API_KEY}:${MAILJET_SECRET_KEY}" \
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
