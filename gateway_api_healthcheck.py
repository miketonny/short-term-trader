#!/usr/bin/env python3
"""
Gateway API 层健康检查 — 不仅看端口在不在，还看 API 是否真的能响应数据请求。
专治 ghost gateway（port up, api dead）。

用法: gateway_api_healthcheck.py <port> [timeout_s]
退出码: 0 = 健康, 1 = 不健康（应该重启 Gateway）
"""
import asyncio, sys

# Python 3.14 asyncio.get_event_loop 兼容
asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4001
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
CLIENT_ID = 99  # 专用 healthcheck client ID，避开策略用的 10/20/30

ib = IB()
try:
    ib.connect('127.0.0.1', PORT, clientId=CLIENT_ID, timeout=TIMEOUT, readonly=True)
    # reqCurrentTime 是最轻的 API 调用 — 若 Gateway 到 IBKR 的上行通道断了会 timeout
    t = ib.reqCurrentTime()
    if t is None:
        print(f"HEALTHCHECK_FAIL port={PORT}: reqCurrentTime returned None")
        sys.exit(1)
    print(f"HEALTHCHECK_OK port={PORT} serverTime={t}")
    sys.exit(0)
except Exception as e:
    print(f"HEALTHCHECK_FAIL port={PORT}: {type(e).__name__}: {e}")
    sys.exit(1)
finally:
    try:
        ib.disconnect()
    except Exception:
        pass
