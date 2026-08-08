#!/bin/bash
# Live Gateway runner v3
# Changes vs v2:
#   - touch /tmp/live_gateway_restart_ts before each ibcstart attempt + backoff + pause
#     so watchdog v8 can detect "runner actively managing" and skip kill
LOCKFILE="/tmp/live_gateway_runner.lock"
RESTART_TS="/tmp/live_gateway_restart_ts"

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
    touch "$RESTART_TS"   # v3: 告知 watchdog 新一轮 2FA 等待开始
    /ibgateway/ibc/scripts/ibcstart.sh 1045 -g \
        --tws-path=/root/Jts --tws-settings-path=/root/Jts/live \
        --ibc-path=/ibgateway/ibc \
        --ibc-ini=/ibgateway/ibc/config_live.ini --mode=live
    EXIT=$?
    echo "$(date): live gateway exited code=$EXIT"

    ATTEMPTS=$((ATTEMPTS+1))
    if [ $ATTEMPTS -ge $MAX_FAIL ]; then
        tg "🚨 live Gateway 连续 ${MAX_FAIL} 次失败 → 暂停 1h，避免凌晨 IBKey 推送轰炸"
        # 暂停期间每 120s 更新一次时间戳，防止 watchdog 误判为 stuck
        for _ in $(seq 1 30); do
            touch "$RESTART_TS"
            sleep 120
        done
        ATTEMPTS=0
    else
        SLEEP=${BACKOFFS[$((ATTEMPTS-1))]}
        echo "$(date): backoff ${SLEEP}s"
        touch "$RESTART_TS"   # v3: 告知 watchdog "我还活着，正在 backoff"
        sleep $SLEEP
    fi
done
