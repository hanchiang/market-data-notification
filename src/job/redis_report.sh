#!/bin/bash

SENDGRID_API_KEY=$1
EMAIL_RECIPIENT=$2
EMAIL_SENDER=$3
REDIS_KEY=$4
SENDGRID_TEMPLATE_ID=$5
STOCKS_TELEGRAM_BOT_TOKEN=$6
STOCKS_TELEGRAM_CHANNEL_ID=$7

if [ -z "$SENDGRID_API_KEY"  ];
then
    echo "send grid api is required"
    exit 1
fi
if [ -z "$EMAIL_RECIPIENT"  ];
then
    echo "email recipient is required"
    exit 1
fi
if [ -z "$EMAIL_SENDER"  ];
then
    echo "email sender is required"
    exit 1
fi
if [ -z "$REDIS_KEY"  ];
then
    echo "redis key is required"
    exit 1
fi
if [ -z "$TEMPLATE_ID"  ];
then
    echo "template id is required"
    exit 1
fi
if [ -z "$STOCKS_TELEGRAM_BOT_TOKEN"  ];
then
    echo "telegram bot token is required"
    exit 1
fi
if [ -z "$STOCKS_TELEGRAM_CHANNEL_ID"  ];
then
    echo "telegram channel id is required"
    exit 1
fi

REDIS_DATA_PATH="/var/lib/redis"
REDIS_BACKUP_FILE_NAME="redis_backup_$(date "+%Y-%m-%dT%H%M%S%z").zip"

LETSENCRYPT_DATA_PATH="/etc/letsencrypt"
LETSENCRYPT_FILE_NAME="redis_backup_$(date "+%Y-%m-%dT%H%M%S%z").zip"

function backup_redis() {
  sudo sh -c "cd $REDIS_DATA_PATH && zip -r $REDIS_BACKUP_FILE_NAME ."
  sudo mv "$REDIS_DATA_PATH/$REDIS_BACKUP_FILE_NAME" .
}

function backup_letsencrypt() {
  sudo sh -c "cd $LETSENCRYPT_DATA_PATH && zip --symlinks -r $LETSENCRYPT_FILE_NAME ."
  sudo mv "$LETSENCRYPT_DATA_PATH/$LETSENCRYPT_FILE_NAME" .
}

function cleanup() {
  sudo rm -rf $REDIS_BACKUP_FILE_NAME
  sudo rm -rf $LETSENCRYPT_FILE_NAME
}

function send_mail() {
  redis_data=$(echo "zrange $REDIS_KEY -1 -1 withscores" | redis-cli)
  redis_data=$(echo $redis_data | sed -e "s/\"/'/g")
  unix_timestamp=$(echo $redis_data | sed -r "s/.* ([0-9]+)/\1/g")
  redis_data_date=$(date -d @${unix_timestamp} +%Y-%m-%d)

  FROM_NAME="han@market-data-notification"

  redis_file=$(cat $REDIS_BACKUP_FILE_NAME | base64 -w0)
  letsencrypt_file=$(cat $LETSENCRYPT_FILE_NAME | base64 -w0)

  # https://docs.sendgrid.com/api-reference/mail-send/mail-send
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
      "name": "'${FROM_NAME}'"
    },
    "template_id": "'${SENDGRID_TEMPLATE_ID}'",
    "attachments": [
      {
        "content": "'${redis_file}'",
        "filename": "'${REDIS_BACKUP_FILE_NAME}'",
        "type": "application/zip",
        "disposition": "attachment"
      },
      {
        "content": "'${letsencrypt_file}'",
        "filename": "'${LETSENCRYPT_FILE_NAME}'",
        "type": "application/zip",
        "disposition": "attachment"
      }
    ]
  }'

  curl --request POST \
    --url https://api.sendgrid.com/v3/mail/send \
    --header 'Authorization: Bearer '$SENDGRID_API_KEY \
    --header 'Content-Type: application/json' \
    --data "$maildata"
}

function notify_telegram() {
  now=$(date +%Y-%m-%dT%H:%M:%S)

  text=$(echo "\[Redis report\] Market data notification redis report backed up to email at $now." | sed 's~[[:blank:]]~%20~g')
  curl "https://api.telegram.org/bot${STOCKS_TELEGRAM_BOT_TOKEN}/sendMessage?chat_id=${STOCKS_TELEGRAM_CHANNEL_ID}&text=$text"
}

backup_redis
backup_letsencrypt
send_mail
notify_telegram
cleanup