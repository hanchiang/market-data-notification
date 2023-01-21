#!/bin/bash

SENDGRID_API_KEY=$1
EMAIL_RECIPIENT=$2
EMAIL_SENDER=$3
REDIS_KEY=$4

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
if [ -z "REDIS_KEY"  ];
then
    echo "redis key is required"
    exit 1
fi

FROM_NAME="han"
SUBJECT="market-data-notification: tradingview redis"

redis_data=$(echo "zrange $REDIS_KEY 0 -1 withscores" | redis-cli)
bodyHTML="<p><strong>Redis data for tradingview</strong></p><p>$redis_data</p>"

maildata='{"personalizations": [{"to": [{"email": "'${$EMAIL_RECIPIENT}'"}]}],"from": {"email": "'${$EMAIL_SENDER}'",
	"name": "'${FROM_NAME}'"},"subject": "'${SUBJECT}'","content": [{"type": "text/html", "value": "'${bodyHTML}'"}]}'

curl --request POST \
  --url https://api.sendgrid.com/v3/mail/send \
  --header 'Authorization: Bearer '$SENDGRID_API_KEY \
  --header 'Content-Type: application/json' \
  --data "$maildata"
