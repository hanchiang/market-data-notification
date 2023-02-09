#!/bin/bash

SENDGRID_API_KEY=$1
EMAIL_RECIPIENT=$2
EMAIL_SENDER=$3
REDIS_KEY=$4
SENDGRID_TEMPLATE_ID=$5

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

FROM_NAME="han"
SUBJECT="market-data-notification: tradingview redis"

redis_data=$(echo "zrange $REDIS_KEY -1 -1 withscores" | redis-cli)
redis_data=$(echo $redis_data | sed -e "s/\"/'/g" )

# TODO: write in python
maildata='{"personalizations": [{"to": [{"email": "'${EMAIL_RECIPIENT}'"}], "dynamic_template_data": { "redis_data": "'${redis_data}'" } }],"from": {"email": "'${EMAIL_SENDER}'",
	"name": "'${FROM_NAME}'"},"subject": "'${SUBJECT}'", "template_id": "'${SENDGRID_TEMPLATE_ID}'"}'

curl --request POST \
  --url https://api.sendgrid.com/v3/mail/send \
  --header 'Authorization: Bearer '$SENDGRID_API_KEY \
  --header 'Content-Type: application/json' \
  --data "$maildata"
