#!/bin/bash
cd /root/live_ibkr_dashboard && /opt/trader-venv/bin/python3 /root/short-term-trader/server_live.py >> /root/live_ibkr_dashboard/server.log 2>&1
