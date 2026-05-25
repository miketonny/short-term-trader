#!/bin/bash
    export DISPLAY=:99
    /opt/trader-venv/bin/python3 /root/short-term-trader/ibkr_forex_strategy.py >> /root/forex_dashboard/strategy.log 2>&1
