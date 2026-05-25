#!/bin/bash
# Gateway Watchdog v3 — 监控 4001(live) + 4002(paper)，独立重启
LOCKFILE="/tmp/gateway_watchdog.lock"
CIRCUIT_ETF="/root/ibkr_dashboard/circuit_state.json"
CIRCUIT_FOREX="/root/forex_dashboard/circuit_state.json"
CIRCUIT_LIVE="/root/live_ibkr_dashboard/circuit_state.json"
LOG_TAG="[watchdog]"
MAX_RESTARTS=3
RESTART_COUNT="/tmp/gateway_restart_count"

# 锁超时5分钟强制清理
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 300 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG lock expired(${LOCK_AGE}s), force clear"
        fuser -k "$LOCKFILE" 2>/dev/null
        rm -f "$LOCKFILE"
    fi
fi

exec 200>"$LOCKFILE"
flock -n 200 || { echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG busy, skip"; exit 0; }

# 限频
COUNT=$(cat "$RESTART_COUNT" 2>/dev/null || echo 0)
AGE=9999
if [ -f "$RESTART_COUNT" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_COUNT" 2>/dev/null || echo 0) ))
fi
if [ "$AGE" -gt 3600 ]; then COUNT=0; fi
if [ "$COUNT" -ge "$MAX_RESTARTS" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG STOP: $COUNT restarts in 1h"
    exit 1
fi

NEED_RESTART=false
RESTART_PAPER=false
RESTART_LIVE=false

# 检查 4002 (paper)
if ! ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG WARN: port 4002(paper) down"
    RESTART_PAPER=true; NEED_RESTART=true
fi

# 检查 4001 (live)
if ! ss -tlnp 2>/dev/null | grep -q ':4001'; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG WARN: port 4001(live) down"
    RESTART_LIVE=true; NEED_RESTART=true
fi

if [ "$NEED_RESTART" = false ]; then
    echo "0" > "$RESTART_COUNT" 2>/dev/null
    exit 0
fi

echo "$((COUNT+1))" > "$RESTART_COUNT"
echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG mem: $(free -h | grep Mem | awk '{print $3"/"$2" avail:"$7}')"

# --- 重启 paper (4002) ---
if [ "$RESTART_PAPER" = true ]; then
    GWPID=$(pgrep -f 'IbcGateway' 2>/dev/null | head -1)
    # 只杀 paper (不是 live)
    PCOUNT=$(pgrep -f 'IbcGateway' 2>/dev/null | wc -l)
    if [ "$PCOUNT" -eq 1 ] && [ -n "$GWPID" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG kill paper gateway PID: $GWPID"
        kill -9 $GWPID 2>/dev/null; sleep 2
    fi
    if ! pgrep -f 'Xvfb :99' > /dev/null; then
        Xvfb :99 -screen 0 1024x768x16 &; sleep 1
    fi
    cd /ibgateway/ibc && nohup bash gatewaystart.sh -inline > /tmp/paper_restart.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ':4002'; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG OK paper 4002 ready ($((i*2))s)"; break
        fi
    done
fi

# --- 重启 live (4001) ---
if [ "$RESTART_LIVE" = true ]; then
    GWPID=$(pgrep -f 'config_live.ini' 2>/dev/null)
    if [ -n "$GWPID" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG kill live gateway PID: $GWPID"
        kill -9 $GWPID 2>/dev/null; sleep 2
    fi
    if ! pgrep -f 'Xvfb :98' > /dev/null; then
        Xvfb :98 -screen 0 1024x768x16 &; sleep 1
    fi
    export DISPLAY=:98
    cd /ibgateway/ibc && nohup bash -c '
        export DISPLAY=:98
        while true; do
            echo "$(date): Starting Live Gateway..."
            /ibgateway/ibc/scripts/ibcstart.sh 1045 -g                 --tws-path=/root/Jts --ibc-path=/ibgateway/ibc                 --ibc-ini=/ibgateway/ibc/config_live.ini --mode=live
            echo "$(date): Live Gateway exited, retry 10s..."
            sleep 10
        done
    ' > /tmp/live_restart.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ':4001'; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG OK live 4001 ready ($((i*2))s)"; break
        fi
    done
fi

# 重置熔断器
for CF in "$CIRCUIT_ETF" "$CIRCUIT_FOREX" "$CIRCUIT_LIVE"; do
    if [ -f "$CF" ]; then
        python3 -c "
import json
try:
    with open('$CF') as f: state = json.load(f)
    old = state.get('state','unknown')
    if old != 'closed':
        with open('$CF','w') as f:
            json.dump({'state':'closed','failures':0,'last_failure':None,'last_error':None},f)
        print(f'circuit {old} -> closed')
except Exception as e:
    print(f'circuit reset fail: {e}')
" 2>&1
    fi
done

echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG recovery done"
exit 0
