#!/usr/bin/env python3
"""
数据缓存 — TTL + LRU 淘汰 + 磁盘持久化，减少 Twelve Data API 调用。

v2: 新增磁盘持久化。每个 cron 进程启动时从磁盘恢复缓存，
    避免日线数据每次运行都重新请求 API。
    日线 TTL 从 1h 提高到 24h（日线每天只变一次）。

缓存粒度: 按 (symbol, interval, outputsize) 分键。

用法:
    cache = DataCache(cache_dir="/root/live_ibkr_dashboard/cache")
    data = cache.get("SPY:15min:100")
    if data is None:
        data = fetch_candles("SPY")
        cache.put("SPY:15min:100", data, ttl=180)
"""
import json, os, time, threading
from collections import OrderedDict


class DataCache:
    def __init__(self, default_ttl=120, max_size=64, cache_dir=None):
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._store: OrderedDict[str, tuple] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._cache_dir = cache_dir

        # 从磁盘恢复缓存
        if self._cache_dir:
            os.makedirs(self._cache_dir, exist_ok=True)
            self._load_from_disk()

    def _cache_path(self, key):
        """返回缓存文件路径（key 做安全文件名处理）"""
        safe = key.replace("/", "_").replace(":", "_").replace(" ", "_")
        return os.path.join(self._cache_dir, f"{safe}.json")

    def _load_from_disk(self):
        """从磁盘恢复未过期的缓存条目"""
        if not self._cache_dir:
            return
        try:
            now = time.time()
            for fname in os.listdir(self._cache_dir):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(self._cache_dir, fname)
                try:
                    with open(fpath) as f:
                        entry = json.load(f)
                    if entry.get("expires", 0) > now:
                        import numpy as np
                        data = {}
                        for k, v in entry.get("data", {}).items():
                            if isinstance(v, list):
                                data[k] = np.array(v)
                            else:
                                data[k] = v
                        self._store[entry["key"]] = (data, entry["expires"])
                except (json.JSONDecodeError, KeyError, OSError):
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
        except OSError:
            pass

    def _save_to_disk(self, key, data, expires):
        """将缓存条目写入磁盘"""
        if not self._cache_dir:
            return
        try:
            serializable = {}
            for k, v in data.items():
                if hasattr(v, "tolist"):
                    serializable[k] = v.tolist()
                else:
                    serializable[k] = v
            entry = {"key": key, "data": serializable, "expires": expires}
            fpath = self._cache_path(key)
            with open(fpath, "w") as f:
                json.dump(entry, f)
        except (OSError, TypeError):
            pass

    def get(self, key):
        with self._lock:
            if key in self._store:
                data, expires = self._store[key]
                if time.time() > expires:
                    del self._store[key]
                    self._misses += 1
                    if self._cache_dir:
                        try:
                            os.remove(self._cache_path(key))
                        except OSError:
                            pass
                    return None
                self._store.move_to_end(key)
                self._hits += 1
                return data

            # 内存未命中，尝试磁盘
            if self._cache_dir:
                fpath = self._cache_path(key)
                if os.path.exists(fpath):
                    try:
                        with open(fpath) as f:
                            entry = json.load(f)
                        if entry.get("expires", 0) > time.time():
                            import numpy as np
                            data = {}
                            for k, v in entry.get("data", {}).items():
                                if isinstance(v, list):
                                    data[k] = np.array(v)
                                else:
                                    data[k] = v
                            self._store[key] = (data, entry["expires"])
                            self._hits += 1
                            return data
                        else:
                            os.remove(fpath)
                    except (json.JSONDecodeError, OSError):
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass

            self._misses += 1
            return None

    def put(self, key, data, ttl=None):
        ttl = ttl if ttl is not None else self._default_ttl
        expires = time.time() + ttl
        with self._lock:
            if len(self._store) >= self._max_size:
                oldest = self._store.popitem(last=False)
                if self._cache_dir:
                    try:
                        os.remove(self._cache_path(oldest[0]))
                    except OSError:
                        pass
            self._store[key] = (data, expires)
            self._save_to_disk(key, data, expires)

    def stats(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}

    def clear(self):
        with self._lock:
            if self._cache_dir:
                for key in list(self._store.keys()):
                    try:
                        os.remove(self._cache_path(key))
                    except OSError:
                        pass
            self._store.clear()

    @staticmethod
    def ttl_for_interval(interval):
        """推荐 TTL (秒) — v2 日线提高到 24h"""
        return {
            "1min": 30, "5min": 60, "15min": 180, "30min": 300,
            "1h": 600, "1day": 86400
        }.get(interval, 120)


# 全局单例
_global_cache = None

def get_cache(cache_dir=None):
    global _global_cache
    if _global_cache is None:
        _global_cache = DataCache(cache_dir=cache_dir)
    return _global_cache
