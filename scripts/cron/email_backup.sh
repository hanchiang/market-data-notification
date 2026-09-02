#!/bin/bash

set -euo pipefail

# The crontab fires this at both 20:05 and 21:05 UTC because only one of them is
# 16:05 New York local, and which one flips with US daylight saving. On a
# continuously-up host nothing else discards the wrong half: unlike the
# notification jobs, this script has no delay tolerance.
# BACKUP_LOCAL_HOUR and the crontab hours are one setting split across two files --
# the cron line is installed by .github/workflows/deploy-email-backup-cron.yml.
# Change one without the other and the failure is seasonal, not immediate: an hour
# that matches in EST stops matching in EDT, so backups can run correctly for
# months and then stop at a DST flip with no edit that day to blame.
# The override is an exact match on the string "true" -- TRUE, True, 1 and yes all
# leave the gate armed.
# To run off-schedule by hand, prefix the invocation:
#   EMAIL_BACKUP_IGNORE_SCHEDULE=true EMAIL_BACKUP_ENV_FILE=... /bin/bash email_backup.sh
# Setting it inside the env file does not work -- that file is sourced below, after
# this gate has already run.
BACKUP_LOCAL_HOUR=16
BACKUP_TZ="America/New_York"

if [ "${EMAIL_BACKUP_IGNORE_SCHEDULE:-false}" != "true" ]; then
    # Check the offset the zone actually produces, not that a file exists: glibc
    # honours TZDIR, so absent tzdata, a hostile TZDIR or a typo yield a silent UTC
    # with exit status 0. Assert the offset is one New York really uses rather than
    # merely not +0000, so a zone carrying stale DST rules -- which still parses and
    # still returns -0400/-0500, just on the wrong dates -- is caught too.
    #
    # Fail OPEN, not closed. Skipping both runs would turn duplicate backups into no
    # backups at all, silently and indefinitely. A duplicate is recoverable; a
    # missing backup is not. Do not read this as "a duplicate gets noticed" -- seven
    # days of them went unnoticed in 2026-08, which is why this gate exists.
    tz_offset=$(TZ="$BACKUP_TZ" date +%z)
    if [ "$tz_offset" != "-0400" ] && [ "$tz_offset" != "-0500" ]; then
        echo "WARNING: $BACKUP_TZ resolved to $tz_offset, not -0400/-0500; running ungated, expect a duplicate" >&2
    else
        # 10# forces base 10 -- an unpadded constant compared against a zero-padded
        # %H would never match, and 08/09 are invalid octal, which aborts under set -e.
        current_local_hour=$(TZ="$BACKUP_TZ" date +%H)
        if [ "$((10#$current_local_hour))" -ne "$BACKUP_LOCAL_HOUR" ]; then
            # Full date, not just the time: the log never rotates, so a bare clock
            # time gives an operator no day boundary to count runs within.
            echo "Skipping: $(date -u +%FT%H:%MZ) is local hour $current_local_hour, not $BACKUP_LOCAL_HOUR ($BACKUP_TZ)"
            exit 0
        fi
    fi
fi

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
    # TradingView scores are stored as Unix seconds today, but tolerate older millisecond-style values.
    if (( redis_score > 9999999999 )); then
        redis_score=$((redis_score / 1000))
    fi
    backup_date=$(date -u -d @"$redis_score" '+%Y-%m-%d')
    email_body="Full TradingView redis payload is attached in ${REDIS_BACKUP_FILE_NAME}."
    EMAIL_PAYLOAD_FILE=$(mktemp /tmp/resend-email-backup.XXXXXX.json)

    # Build the attachment payload inside Python so the base64 ZIP never flows through argv.
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
