#!/usr/bin/env python3
"""
请求频率限制 — 最小间隔 + 随机抖动，防 Twelve Data rate limit。

用法:
    limiter = RateLimiter(min_interval=1.0, jitter=0.5)
    limiter.wait()  # 在请求前调用，自动休眠到安全时间
"""
import time, random


class RateLimiter:
    def __init__(self, min_interval=1.0, jitter_min=0.3, jitter_max=0.8):
        self._min_interval = min_interval
        self._jitter_min = jitter_min
        self._jitter_max = jitter_max
        self._last = 0.0

    def wait(self):
        """等待直到可以安全发起下一次请求"""
        now = time.time()
        elapsed = now - self._last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        jitter = random.uniform(self._jitter_min, self._jitter_max)
        time.sleep(jitter)
        self._last = time.time()

    def reset(self):
        self._last = 0.0


# 全局单例 — Twelve Data 友好间隔
_twelve_data = RateLimiter(min_interval=1.2, jitter_min=0.3, jitter_max=0.8)

def get_twelve_data_limiter():
    return _twelve_data

# User-Agent 轮换
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

def random_ua():
    return random.choice(USER_AGENTS)
