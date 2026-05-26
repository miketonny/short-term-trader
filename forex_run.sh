#!/bin/bash
# Forex Strategy wrapper
set -a; source /root/short-term-trader/.env; set +a
export DISPLAY=:99
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_forex_strategy.py >> /root/forex_dashboard/strategy.log 2>&1
