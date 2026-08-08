#!/bin/bash
# ETF Live Strategy — flock-guarded, cron every 5min + daily 7am
# Only starts if not already running. No kill → no 2FA trigger.
set -e
LOCKFILE="/tmp/run_live_strategy.lock"
LOGFILE="/root/live_ibkr_dashboard/strategy.log"

exec 9>"$LOCKFILE"
if ! flock -n 9; then
    # Already running, skip
    exit 0
fi

set -a; source /root/short-term-trader/.env; set +a
export DISPLAY=:98

echo "=== Multi-ETF V1 | $(date +%H:%M:%S) ===" >> "$LOGFILE"
echo "  策略启动" >> "$LOGFILE"

/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py $* \
    --config /root/live_ibkr_dashboard/strategy_config.json \
    >> "$LOGFILE" 2>&1

echo "  [$(date +%H:%M:%S)] 策略进程退出" >> "$LOGFILE"
