#!/bin/bash
# Live Gateway runner v2
# Changes vs v1:
#   - flock single-instance lock to prevent the 2026-05-27 incident
#     (watchdog killed the java child but not this parent bash, then launched
#      a second runner; result: 2 java instances logging into same IB account,
#      IBKey push routing got confused, user could not approve).
LOCKFILE="/tmp/live_gateway_runner.lock"
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    echo "$(date): another live_gateway_runner.sh already holds $LOCKFILE - exit"
    exit 0
fi

export DISPLAY=:98
ATTEMPTS=0
BACKOFFS=(10 30 60 300 300)
MAX_FAIL=5

tg() {
    [ -f /root/short-term-trader/.env ] || return 0
    set -a; source /root/short-term-trader/.env; set +a
    [ -z "$TG_TOKEN" ] && return 0
    curl -s -o /dev/null -m 5 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT_ID:-6849175810}" -d "text=$1" || true
}

while true; do
    echo "$(date): starting live gateway (attempt $((ATTEMPTS+1)))..."
    /ibgateway/ibc/scripts/ibcstart.sh 1045 -g \
        --tws-path=/root/Jts --tws-settings-path=/root/Jts/live \
        --ibc-path=/ibgateway/ibc \
        --ibc-ini=/ibgateway/ibc/config_live.ini --mode=live
    EXIT=$?
    echo "$(date): live gateway exited code=$EXIT"

    ATTEMPTS=$((ATTEMPTS+1))
    if [ $ATTEMPTS -ge $MAX_FAIL ]; then
        tg "🚨 live Gateway 连续 ${MAX_FAIL} 次失败 → 暂停 1h，避免凌晨 IBKey 推送轰炸"
        sleep 3600
        ATTEMPTS=0
    else
        SLEEP=${BACKOFFS[$((ATTEMPTS-1))]}
        echo "$(date): backoff ${SLEEP}s"
        sleep $SLEEP
    fi
done
