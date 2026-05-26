#!/bin/bash
# ETF Live Strategy wrapper
set -a; source /root/short-term-trader/.env; set +a
cp /root/ibkr_dashboard/strategy_config.json /root/ibkr_dashboard/strategy_config.json.paper 2>/dev/null
cp /root/live_ibkr_dashboard/strategy_config.json /root/ibkr_dashboard/strategy_config.json
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py >> /root/live_ibkr_dashboard/strategy.log 2>&1
cp /root/ibkr_dashboard/strategy_config.json.paper /root/ibkr_dashboard/strategy_config.json 2>/dev/null
