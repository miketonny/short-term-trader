#!/bin/bash
# Gateway Watchdog v9 — 软重启 (IBC CommandServer RESTART), 不丢 session 不需要 2FA
# v9 vs v8:
#   - api-ghost / circuit-open: 先尝试 telnet RESTART (软重启，无需 2FA)，失败再硬杀
#   - port-down: 仍需硬杀（java 已死，CommandServer 连不上）
#   - 新增 ibc_soft_restart() 函数

LOCKFILE="/tmp/gateway_watchdog.lock"
CIRCUIT_ETF="/root/ibkr_dashboard/circuit_state.json"
CIRCUIT_FOREX="/root/forex_dashboard/circuit_state.json"
CIRCUIT_LIVE="/root/live_ibkr_dashboard/circuit_state.json"
LOG_TAG="[watchdog]"
MAX_RESTARTS=3
RESTART_COUNT="/tmp/gateway_restart_count"
HEALTHCHECK="/root/short-term-trader/gateway_api_healthcheck.py"
STRATEGY_DATA="/root/live_ibkr_dashboard/data.json"
STRATEGY_LOCK="/tmp/run_live_strategy.lock"
STRATEGY_STALE_MAX=900
PY="/opt/trader-venv/bin/python3"
LIVE_TS="/tmp/live_gateway_restart_ts"
PAPER_TS="/tmp/paper_gateway_restart_ts"
TWOFA_GRACE=1800
LIVE_CMD=7462
PAPER_CMD=7463

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

# ── lock logic (unchanged from v7) ──
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 300 ]; then
        echo "$(ts) $LOG_TAG lock held ${LOCK_AGE}s, force clear stale holder"
        tg "⚠️ watchdog lock held ${LOCK_AGE}s → 强清。"
        fuser -k "$LOCKFILE" 2>/dev/null
        rm -f "$LOCKFILE"
        exec 200>"$LOCKFILE"
        if ! flock -n 200; then
            echo "$(ts) $LOG_TAG lock re-acquire failed, skip"
            exit 0
        fi
    else
        echo "$(ts) $LOG_TAG busy, skip"
        exit 0
    fi
fi
touch "$LOCKFILE"

NOW_S=$(date +%s)

# ── v9: IBC CommandServer 软重启 ──
ibc_soft_restart() {
    local label="$1" port="$2" cmd_port="$3"

    echo "$(ts) $LOG_TAG $label 尝试软重启 (telnet RESTART on :$cmd_port)..."
    (echo "RESTART"; sleep 1) | timeout 5 nc -w 3 127.0.0.1 "$cmd_port" 2>/dev/null
    local cmd_rc=$?

    if [ "$cmd_rc" -ne 0 ]; then
        echo "$(ts) $LOG_TAG $label 软重启失败 — CommandServer :$cmd_port 无响应"
        return 1
    fi

    for i in $(seq 1 45); do
        sleep 2
        if ss -tlnp 2>/dev/null | grep -q ":${port}"; then
            if $PY "$HEALTHCHECK" "$port" 5 >/dev/null 2>&1; then
                echo "$(ts) $LOG_TAG $label 软重启成功 ($((i*2))s)"
                return 0
            fi
        fi
    done

    echo "$(ts) $LOG_TAG $label 软重启超时 (90s)"
    return 1
}

# ── 滑动窗口限频 ──
[ -f "$RESTART_COUNT" ] || touch "$RESTART_COUNT"
awk -v cutoff=$((NOW_S - 3600)) '$1 >= cutoff' "$RESTART_COUNT" > "${RESTART_COUNT}.tmp" && mv "${RESTART_COUNT}.tmp" "$RESTART_COUNT"
RECENT=$(wc -l < "$RESTART_COUNT")
if [ "$RECENT" -ge "$MAX_RESTARTS" ]; then
    echo "$(ts) $LOG_TAG STOP: $RECENT restarts in 1h"
    LAST_ALERT="/tmp/gateway_alert_stop"
    if [ ! -f "$LAST_ALERT" ] || [ $((NOW_S - $(stat -c %Y "$LAST_ALERT" 2>/dev/null || echo 0))) -gt 3600 ]; then
        tg "🚨 watchdog: 1h 内 ${RECENT} 次重启 → 暂停干预。需人工排查。"
        touch "$LAST_ALERT"
    fi
    exit 1
fi

RESTART_PAPER=false
RESTART_LIVE=false
TRIGGER_REASON_PAPER=""
TRIGGER_REASON_LIVE=""

# === 检测信号 1：端口不在 ===
if ! ss -tlnp 2>/dev/null | grep -q ':4002'; then
    RESTART_PAPER=true; TRIGGER_REASON_PAPER="port-down"
fi
if ! ss -tlnp 2>/dev/null | grep -q ':4001'; then
    RESTART_LIVE=true; TRIGGER_REASON_LIVE="port-down"
fi

# === 检测信号 2：live API ghost 检查 ===
if ! $RESTART_LIVE; then
    if ! $PY "$HEALTHCHECK" 4001 8 >/dev/null 2>&1; then
        RESTART_LIVE=true; TRIGGER_REASON_LIVE="api-ghost"
        echo "$(ts) $LOG_TAG live 4001 端口在但 API 不响应"
    fi
fi

# === 检测信号 3：circuit_state.json 处于 open ===
check_circuit() {
    local f="$1"
    [ -f "$f" ] || return 1
    $PY -c "import json,sys; sys.exit(0 if json.load(open('$f')).get('state')=='open' else 1)" 2>/dev/null
}
if ! $RESTART_PAPER && check_circuit "$CIRCUIT_FOREX"; then
    RESTART_PAPER=true; TRIGGER_REASON_PAPER="forex-circuit-open"
fi
if ! $RESTART_LIVE; then
    if check_circuit "$CIRCUIT_LIVE" || check_circuit "$CIRCUIT_ETF"; then
        RESTART_LIVE=true; TRIGGER_REASON_LIVE="etf-circuit-open"
    fi
fi

# === 检测信号 4：策略僵死（data.json 超过 15 分钟没更新）===
RESTART_STRATEGY=false
TRIGGER_REASON_STRATEGY=""
DATA_AGE=$(( NOW_S - $(stat -c %Y "$STRATEGY_DATA" 2>/dev/null || echo 0) ))
if [ "$DATA_AGE" -gt "$STRATEGY_STALE_MAX" ]; then
    RESTART_STRATEGY=true
    TRIGGER_REASON_STRATEGY="data-stale-${DATA_AGE}s"
    echo "$(ts) $LOG_TAG strategy data.json ${DATA_AGE}s stale (max ${STRATEGY_STALE_MAX}s)"
    if fuser "$STRATEGY_LOCK" 2>/dev/null | grep -q .; then
        STRAT_PID=$(fuser "$STRATEGY_LOCK" 2>/dev/null | head -1)
        echo "$(ts) $LOG_TAG strategy lock held by pid $STRAT_PID but data stale, force kill"
        kill "$STRAT_PID" 2>/dev/null
        sleep 1
        kill -KILL "$STRAT_PID" 2>/dev/null
    fi
    fuser -k "$STRATEGY_LOCK" 2>/dev/null
    rm -f "$STRATEGY_LOCK"
    /root/short-term-trader/run_live.sh &
    tg "⚠️ ETF strategy stale (data ${DATA_AGE}s old) -> killed + restarting"
fi

# ── port-down 且 runner 已在处理 → 跳过 ──
runner_recently_started() {
    local tsfile="$1" runner_pattern="$2"
    [ -f "$tsfile" ] || return 1
    local age=$(( NOW_S - $(stat -c %Y "$tsfile" 2>/dev/null || echo 0) ))
    [ "$age" -lt "$TWOFA_GRACE" ] || return 1
    pgrep -f "$runner_pattern" >/dev/null 2>&1 || return 1
    return 0
}

if $RESTART_LIVE && [ "$TRIGGER_REASON_LIVE" = "port-down" ]; then
    if runner_recently_started "$LIVE_TS" "live_gateway_runner.sh"; then
        TS_AGE=$(( NOW_S - $(stat -c %Y "$LIVE_TS") ))
        echo "$(ts) $LOG_TAG live runner already restarting (ts ${TS_AGE}s ago), skip kill"
        RESTART_LIVE=false
    fi
fi

if $RESTART_PAPER && [ "$TRIGGER_REASON_PAPER" = "port-down" ]; then
    if runner_recently_started "$PAPER_TS" "gatewaystart.sh"; then
        TS_AGE=$(( NOW_S - $(stat -c %Y "$PAPER_TS") ))
        echo "$(ts) $LOG_TAG paper gateway already starting (ts ${TS_AGE}s ago), skip kill"
        RESTART_PAPER=false
    fi
fi

if ! $RESTART_PAPER && ! $RESTART_LIVE; then
    exit 0
fi

echo "$(ts) $LOG_TAG mem: $(free -h | grep Mem | awk '{print $3"/"$2" avail:"$7}')"
RESTART_OK=false

# ── 重启 paper ──
if [ "$RESTART_PAPER" = true ]; then
    echo "$(ts) $LOG_TAG WARN: paper 4002 触发 ($TRIGGER_REASON_PAPER) → restart"
    SOFT_OK=false
    if [ "$TRIGGER_REASON_PAPER" != "port-down" ]; then
        ibc_soft_restart "paper" 4002 "$PAPER_CMD" && SOFT_OK=true && RESTART_OK=true
        $SOFT_OK && tg "✅ paper Gateway 软重启 OK"
    fi
    if ! $SOFT_OK; then
        tg "⚠️ paper Gateway 硬重启中 (原因: $TRIGGER_REASON_PAPER)"
        pkill -TERM -f 'gatewaystart.sh' 2>/dev/null
        pkill -TERM -f 'config\.ini[^_]' 2>/dev/null
        sleep 2
        pkill -KILL -f 'gatewaystart.sh' 2>/dev/null
        pkill -KILL -f 'config\.ini[^_]' 2>/dev/null
        sleep 5
        touch "$PAPER_TS"
        pgrep -f 'Xvfb :99' > /dev/null || { Xvfb :99 -screen 0 1024x768x24 & sleep 1; }
        nohup flock -n /tmp/paper_gateway_runner.lock -c "cd /ibgateway/ibc && bash gatewaystart.sh -inline" > /tmp/paper_restart.log 2>&1 &
        PAPER_OK=false
        for i in $(seq 1 30); do
            sleep 2
            if ss -tlnp 2>/dev/null | grep -q ':4002'; then
                echo "$(ts) $LOG_TAG OK paper 4002 ready ($((i*2))s)"
                PAPER_OK=true; RESTART_OK=true
                tg "✅ paper Gateway restart OK ($((i*2))s)"
                break
            fi
        done
        $PAPER_OK || tg "❌ paper Gateway 60s 内未起来"
    fi
fi

# ── 重启 live ──
if [ "$RESTART_LIVE" = true ]; then
    echo "$(ts) $LOG_TAG WARN: live 4001 触发 ($TRIGGER_REASON_LIVE) → restart"
    SOFT_OK=false
    # v9: api-ghost / circuit-open → 先软重启，无需 2FA！
    if [ "$TRIGGER_REASON_LIVE" != "port-down" ]; then
        ibc_soft_restart "live" 4001 "$LIVE_CMD" && SOFT_OK=true && RESTART_OK=true
        $SOFT_OK && tg "✅ live Gateway 软重启 OK (无需 2FA)"
    fi
    if ! $SOFT_OK; then
        tg "⚠️ live Gateway 硬重启中 (原因: $TRIGGER_REASON_LIVE)。若 180s 内未 OK 说明在等 2FA 批准。"
        pkill -TERM -f 'live_gateway_runner.sh' 2>/dev/null
        pkill -TERM -f 'config_live.ini' 2>/dev/null
        sleep 2
        pkill -KILL -f 'live_gateway_runner.sh' 2>/dev/null
        pkill -KILL -f 'config_live.ini' 2>/dev/null
        sleep 5
        touch "$LIVE_TS"
        pgrep -f 'Xvfb :98' > /dev/null || { Xvfb :98 -screen 0 1024x768x16 & sleep 1; }
        export DISPLAY=:98
        nohup bash /root/short-term-trader/live_gateway_runner.sh > /tmp/live_restart.log 2>&1 &
        LIVE_OK=false
        for i in $(seq 1 90); do
            sleep 2
            if ss -tlnp 2>/dev/null | grep -q ':4001'; then
                if $PY "$HEALTHCHECK" 4001 5 >/dev/null 2>&1; then
                    echo "$(ts) $LOG_TAG OK live 4001 ready+healthy ($((i*2))s)"
                    LIVE_OK=true; RESTART_OK=true
                    tg "✅ live Gateway restart OK ($((i*2))s)"
                    break
                fi
            fi
        done
        $LIVE_OK || tg "❌ live Gateway 180s 内未 healthy — 检查手机 2FA 或 /tmp/live_restart.log"
    fi
fi

[ "$RESTART_OK" = true ] && echo "$NOW_S" >> "$RESTART_COUNT"

for CF in "$CIRCUIT_ETF" "$CIRCUIT_FOREX" "$CIRCUIT_LIVE"; do
    [ -f "$CF" ] || continue
    $PY -c "
import json
try:
    with open('$CF') as f: state = json.load(f)
    old = state.get('state','unknown')
    if old != 'closed':
        with open('$CF','w') as f:
            json.dump({'state':'closed','failures':0,'last_failure':None,'last_error':None,'last_notify':0},f)
        print(f'circuit $CF: {old} -> closed')
except Exception as e:
    print(f'circuit reset fail $CF: {e}')
" 2>&1
done

if [ "$RESTART_OK" = true ]; then
    echo "$(ts) $LOG_TAG recovery done ✓"
else
    echo "$(ts) $LOG_TAG recovery attempt finished (gateway not yet healthy, runner may still be trying)"
fi
exit 0
