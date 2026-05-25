#!/bin/bash
# ETF Multi-Strategy run wrapper
export DISPLAY=:99
/opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_strategy.py >> /root/ibkr_dashboard/strategy.log 2>&1
