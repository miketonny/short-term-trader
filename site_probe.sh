#!/bin/bash
# site_probe.sh — nginx 探活,每 5 分钟 cron 跑一次;挂了发 TG 告警,30 分钟限一次
ST=/root/.site_probe_last
code=$(curl -s -o /dev/null -m 10 -w "%{http_code}" http://127.0.0.1/ 2>/dev/null)
case "$code" in
  2*|3*) rm -f "$ST"; exit 0;;
esac
now=$(date +%s)
last=0; [ -f "$ST" ] && last=$(cat "$ST" 2>/dev/null || echo 0)
if [ $((now - last)) -ge 1800 ]; then
  echo "$now" > "$ST"
  cd /root/short-term-trader && /opt/trader-venv/bin/python3 -c \
    "from notifier import notify_error; notify_error(\"site-probe\", \"🚨 nginx 挂了:curl 127.0.0.1 → $code,网站全挂!查 systemctl status nginx / /var/log/nginx/error.log\")"
fi
