#!/bin/bash
# Live Gateway — display :98
export DISPLAY=:98

if ! pgrep -f 'Xvfb :98' > /dev/null; then
    Xvfb :98 -screen 0 1024x768x16 &
    sleep 1
fi

if ss -tlnp 2>/dev/null | grep -q ':4001'; then
    echo "$(date) Live Gateway 4001 already running"
    exit 0
fi

OLD_PID=$(pgrep -f 'config_live.ini' 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    kill $OLD_PID 2>/dev/null
    sleep 2
fi

cd /ibgateway/ibc
nohup bash -c '
export DISPLAY=:98
while true; do
    echo "$(date): Starting Live Gateway..."
    /ibgateway/ibc/scripts/ibcstart.sh 1045 -g         --tws-path=/root/Jts         --ibc-path=/ibgateway/ibc         --ibc-ini=/ibgateway/ibc/config_live.ini         --mode=live
    echo "$(date): Live Gateway exited, retry in 10s..."
    sleep 10
done
' > /tmp/live_gateway.log 2>&1 &

echo "$(date) Live Gateway triggered"
