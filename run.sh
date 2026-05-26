#!/bin/bash
# ETF Multi-Strategy run wrapper (paper)
set -a; source /root/short-term-trader/.env; set +a
export DISPLAY=:99
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py >> /root/ibkr_dashboard/strategy.log 2>&1
