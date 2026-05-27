#!/bin/bash
# Gateway Watchdog v4 — 监控 4001(live) + 4002(paper)
# 改动 vs v3:
#   1. live 用指数退避 (10s→30s→60s→300s 上限) + 5 次失败暂停 1h + Telegram
#   2. MAX_RESTARTS 改为滑动窗口（1h 内 ≥3 才暂停）
#   3. recovery / 限频触发 / 锁清理 都推 Telegram

LOCKFILE="/tmp/gateway_watchdog.lock"
CIRCUIT_ETF="/root/ibkr_dashboard/circuit_state.json"
CIRCUIT_FOREX="/root/forex_dashboard/circuit_state.json"
CIRCUIT_LIVE="/root/live_ibkr_dashboard/circuit_state.json"
LOG_TAG="[watchdog]"
MAX_RESTARTS=3
RESTART_COUNT="/tmp/gateway_restart_count"
LIVE_BACKOFF_STATE="/tmp/live_gw_backoff"

# Telegram helper — 读 .env，失败静默
tg() {
    local msg="$1"
    [ -f /root/short-term-trader/.env ] || return 0
    set -a; source /root/short-term-trader/.env; set +a
    [ -z "$TG_TOKEN" ] && return 0
    curl -s -o /dev/null -m 5 \
        "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT_ID:-6849175810}" \
        -d "text=${msg}" || true
}

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 锁超时 5 分钟强清 + 通知
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 300 ]; then
        echo "$(ts) $LOG_TAG lock expired(${LOCK_AGE}s), force clear"
        tg "⚠️ watchdog lock expired ${LOCK_AGE}s → 强清。可能有 zombie。"
        fuser -k "$LOCKFILE" 2>/dev/null
        rm -f "$LOCKFILE"
    fi
fi

exec 200>"$LOCKFILE"
flock -n 200 || { echo "$(ts) $LOG_TAG busy, skip"; exit 0; }

# ===== 滑动窗口限频：保留过去 1h 内的所有 restart 时间戳 =====
NOW_S=$(date +%s)
# 文件每行 = 一次重启的 unix 时间
[ -f "$RESTART_COUNT" ] || touch "$RESTART_COUNT"
# 保留 1h 内的
awk -v cutoff=$((NOW_S - 3600)) '$1 >= cutoff' "$RESTART_COUNT" > "${RESTART_COUNT}.tmp" && mv "${RESTART_COUNT}.tmp" "$RESTART_COUNT"
RECENT=$(wc -l < "$RESTART_COUNT")
if [ "$RECENT" -ge "$MAX_RESTARTS" ]; then
    echo "$(ts) $LOG_TAG STOP: $RECENT restarts in 1h"
    # 只在跨过阈值的第一次告警
    LAST_ALERT="/tmp/gateway_alert_stop"
    if [ ! -f "$LAST_ALERT" ] || [ $((NOW_S - $(stat -c %Y "$LAST_ALERT" 2>/dev/null || echo 0))) -gt 3600 ]; then
        tg "🚨 watchdog: 1h 内 ${RECENT} 次重启 → 暂停干预。需人工排查。"
        touch "$LAST_ALERT"
    fi
    exit 1
fi

RESTART_PAPER=false
RESTART_LIVE=false
ss -tlnp 2>/dev/null | grep -q ':4002' || RESTART_PAPER=true
ss -tlnp 2>/dev/null | grep -q ':4001' || RESTART_LIVE=true

if ! $RESTART_PAPER && ! $RESTART_LIVE; then
    exit 0
fi

echo "$(ts) $LOG_TAG mem: $(free -h | grep Mem | awk '{print $3"/"$2" avail:"$7}')"
RESTART_OK=false

# ── 重启 paper (4002) ──
if [ "$RESTART_PAPER" = true ]; then
    echo "$(ts) $LOG_TAG WARN: port 4002(paper) down → restart"
    GWPID=$(pgrep -f 'config\.ini[^_]' 2>/dev/null)
    [ -n "$GWPID" ] && { kill -9 $GWPID 2>/dev/null; sleep 2; }
    pgrep -f 'Xvfb :99' > /dev/null || { Xvfb :99 -screen 0 1024x768x16 & sleep 1; }
    cd /ibgateway/ibc && nohup bash gatewaystart.sh -inline > /tmp/paper_restart.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ':4002'; then
            echo "$(ts) $LOG_TAG OK paper 4002 ready ($((i*2))s)"
            RESTART_OK=true
            tg "✅ paper Gateway restart OK ($((i*2))s)"
            break
        fi
    done
    if ! $RESTART_OK; then
        tg "❌ paper Gateway 60s 内未起来"
    fi
fi

# ── 重启 live (4001) — 指数退避，不再 while true tight loop ──
if [ "$RESTART_LIVE" = true ]; then
    echo "$(ts) $LOG_TAG WARN: port 4001(live) down → restart"
    # v5: kill runner parent bash first to stop the while-true respawn loop,
    # then kill any remaining ibcstart/java children; sleep 5 to let IBKR
    # release the server-side session before the new login attempt.
    pkill -TERM -f 'live_gateway_runner.sh' 2>/dev/null
    pkill -TERM -f 'config_live.ini' 2>/dev/null
    sleep 2
    pkill -KILL -f 'live_gateway_runner.sh' 2>/dev/null
    pkill -KILL -f 'config_live.ini' 2>/dev/null
    sleep 5
    pgrep -f 'Xvfb :98' > /dev/null || { Xvfb :98 -screen 0 1024x768x16 & sleep 1; }
    export DISPLAY=:98
    # 启动一次性 live runner（自身带指数退避；不再每 10s 死循环）
    nohup bash /root/short-term-trader/live_gateway_runner.sh > /tmp/live_restart.log 2>&1 &
    for i in $(seq 1 30); do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ':4001'; then
            echo "$(ts) $LOG_TAG OK live 4001 ready ($((i*2))s)"
            RESTART_OK=true
            tg "✅ live Gateway restart OK ($((i*2))s)"
            break
        fi
    done
    if ! $RESTART_OK; then
        tg "❌ live Gateway 60s 内未起来（runner 自身会继续退避重试）"
    fi
fi

# 真正成功才记录
[ "$RESTART_OK" = true ] && echo "$NOW_S" >> "$RESTART_COUNT"

# 重置熔断器
for CF in "$CIRCUIT_ETF" "$CIRCUIT_FOREX" "$CIRCUIT_LIVE"; do
    [ -f "$CF" ] || continue
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
done

echo "$(ts) $LOG_TAG recovery done"
exit 0
