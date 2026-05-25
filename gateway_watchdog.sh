#!/bin/bash
# Gateway Watchdog v2 — 每5分钟检测4002端口，挂了就重启+重置熔断
# 部署到服务器 crontab: */5 * * * * /root/short-term-trader/gateway_watchdog.sh >> /root/ibkr_dashboard/watchdog.log 2>&1

PORT=4002
LOCKFILE="/tmp/gateway_watchdog.lock"
CIRCUIT_FILE_ETF="/root/ibkr_dashboard/circuit_state.json"
CIRCUIT_FILE_FOREX="/root/forex_dashboard/circuit_state.json"
LOG_TAG="[watchdog]"
MAX_RESTARTS_PER_HOUR=3
RESTART_COUNT_FILE="/tmp/gateway_restart_count"

# ── 锁超时检测：如果锁文件超过5分钟，强制清理 ──
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 300 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG ⚠️ 锁过期(${LOCK_AGE}s)，强制清理"
        fuser -k "$LOCKFILE" 2>/dev/null
        rm -f "$LOCKFILE"
    fi
fi

# ── 防并发 ──
exec 200>"$LOCKFILE"
flock -n 200 || { echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 上次检测未结束，跳过"; exit 0; }

# ── 检查端口 ──
if ss -tlnp | grep -q ":$PORT"; then
    echo "0" > "$RESTART_COUNT_FILE" 2>/dev/null
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG ⚠️ 端口 $PORT 无监听！Gateway 可能已崩溃"

# ── 限频：1小时内最多重启3次 ──
COUNT=$(cat "$RESTART_COUNT_FILE" 2>/dev/null || echo 0)
AGE=9999
if [ -f "$RESTART_COUNT_FILE" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_COUNT_FILE" 2>/dev/null || echo 0) ))
fi
if [ "$AGE" -gt 3600 ]; then COUNT=0; fi
if [ "$COUNT" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 🛑 1小时内已重启${COUNT}次，暂停自动恢复，需人工介入"
    exit 1
fi
echo "$((COUNT+1))" > "$RESTART_COUNT_FILE"

# ── 记录崩溃前内存 ──
echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 崩溃时内存: $(free -h | grep Mem | awk '{print $3"/"$2" avail:"$7}')"

# ── 杀掉残留Gateway Java（精确匹配，不杀全局java）──
GATEWAY_PID=$(pgrep -f 'IbcGateway' 2>/dev/null)
if [ -n "$GATEWAY_PID" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 杀掉残留Gateway PID: $GATEWAY_PID"
    kill -9 $GATEWAY_PID 2>/dev/null
    sleep 2
fi

# ── 杀掉卡住的 gatewaystart.sh ──
OLD_STARTER=$(pgrep -f 'gatewaystart.sh' 2>/dev/null)
if [ -n "$OLD_STARTER" ]; then
    kill $OLD_STARTER 2>/dev/null
    sleep 1
fi

# ── 确保 Xvfb 在运行（不杀现有Xvfb，避免影响其他服务）──
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :99 -screen 0 1024x768x24 &>/dev/null &
    sleep 1
fi
export DISPLAY=:99

# ── 启动 Gateway（后台，gatewaystart.sh 的 while 循环会保活）──
cd /ibgateway/ibc && nohup bash gatewaystart.sh -inline &>/tmp/gateway_watchdog_start.log &
STARTER_PID=$!

# ── 等 Gateway 启动完成（最多等90秒）──
STARTED=false
for i in $(seq 1 45); do
    sleep 2
    if ss -tlnp | grep -q ":$PORT"; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG ✅ Gateway 重启成功，4002端口已就绪 (耗时${i}x2秒)"
        STARTED=true
        break
    fi
done

if [ "$STARTED" = false ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG ❌ Gateway 启动超时（90秒）"
    kill $STARTER_PID 2>/dev/null
    pgrep -f 'IbcGateway' | xargs kill 2>/dev/null
    exit 2
fi

# ── 重置两个熔断器（ETF + Forex）──
for CF in "$CIRCUIT_FILE_ETF" "$CIRCUIT_FILE_FOREX"; do
    if [ -f "$CF" ]; then
        python3 -c "
import json
try:
    with open('$CF') as f:
        state = json.load(f)
    old_state = state.get('state', 'unknown')
    with open('$CF', 'w') as f:
        json.dump({'state': 'closed', 'failures': 0, 'last_failure': None, 'last_error': None}, f)
    print(f'熔断器 {old_state} -> closed')
except Exception as e:
    print(f'熔断器重置失败: {e}')
" 2>&1
    fi
done

# ── 手动跑策略恢复数据 ──
echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 触发策略恢复..."
timeout 60 /opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_forex_strategy.py 2>&1 | tail -3
timeout 60 /opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py 2>&1 | tail -3

echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_TAG 恢复完成"
exit 0
