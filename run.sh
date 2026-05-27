#!/bin/bash
# ETF Multi-Strategy run wrapper (paper) — uses --config to avoid race
set -a; source /root/short-term-trader/.env; set +a
export DISPLAY=:99
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py \
    --config /root/ibkr_dashboard/strategy_config.json \
    >> /root/ibkr_dashboard/strategy.log 2>&1
