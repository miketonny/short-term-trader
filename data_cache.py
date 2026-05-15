#!/usr/bin/env python3
"""
数据缓存 — TTL + LRU 淘汰，减少 Twelve Data API 调用。

缓存粒度: 按 (symbol, interval, outputsize) 分键。
TTL 按周期设: 5min K线缓60秒（等新 bar），15min 缓180秒。

用法:
    cache = DataCache()
    data = cache.get("SPY:15min:100")
    if data is None:
        data = fetch_candles("SPY")
        cache.put("SPY:15min:100", data, ttl=180)
"""
import time, threading
from collections import OrderedDict


class DataCache:
    def __init__(self, default_ttl=120, max_size=64):
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._store: OrderedDict[str, tuple] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key):
        with self._lock:
            if key not in self._store:
                self._misses += 1
                return None

            data, expires = self._store[key]
            if time.time() > expires:
                del self._store[key]
                self._misses += 1
                return None

            self._store.move_to_end(key)
            self._hits += 1
            return data

    def put(self, key, data, ttl=None):
        ttl = ttl if ttl is not None else self._default_ttl
        expires = time.time() + ttl
        with self._lock:
            if len(self._store) >= self._max_size:
                self._store.popitem(last=False)
            self._store[key] = (data, expires)

    def stats(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}

    def clear(self):
        with self._lock:
            self._store.clear()

    @staticmethod
    def ttl_for_interval(interval):
        """推荐 TTL (秒)"""
        return {"1min": 30, "5min": 60, "15min": 180, "30min": 300, "1h": 600}.get(interval, 120)


# 全局单例
_global_cache = DataCache()

def get_cache():
    return _global_cache
