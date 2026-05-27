#!/bin/bash
# ETF Live Strategy wrapper — uses --config to avoid race
set -a; source /root/short-term-trader/.env; set +a
export DISPLAY=:98
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py \
    --config /root/live_ibkr_dashboard/strategy_config.json \
    >> /root/live_ibkr_dashboard/strategy.log 2>&1
