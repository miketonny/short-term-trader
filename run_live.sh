#!/bin/bash
export DISPLAY=:99
# Copy live config to default location, run, restore
cp /root/ibkr_dashboard/strategy_config.json /root/ibkr_dashboard/strategy_config.json.paper 2>/dev/null
cp /root/live_ibkr_dashboard/strategy_config.json /root/ibkr_dashboard/strategy_config.json
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py >> /root/live_ibkr_dashboard/strategy.log 2>&1
cp /root/ibkr_dashboard/strategy_config.json.paper /root/ibkr_dashboard/strategy_config.json 2>/dev/null
