#!/usr/bin/env python3
"""
熔断器 — 替换 fail_count.json，统一 ETF/外汇/Gateway 故障处理。

状态机: CLOSED → (失败N次) → OPEN → (冷却结束) → HALF_OPEN → (成功) → CLOSED
                                               └→ (失败) → OPEN

用法:
    cb = CircuitBreaker("gateway", threshold=3, cooldown=300)
    if not cb.available():
        return  # 熔断中，跳过
    try:
        do_thing()
        cb.success()
    except Exception:
        cb.failure()
        if cb.should_notify():
            send_alert()

状态持久化到 JSON，crash 后恢复。
通知限频: should_notify() 默认 30min 内只发一次。
"""
import json, os, time
from enum import Enum


class State(Enum):
    CLOSED = "closed"        # 正常
    OPEN = "open"            # 熔断
    HALF_OPEN = "half_open"  # 试探


class CircuitBreaker:
    def __init__(self, name, threshold=3, cooldown=300, persist_path=None, notify_interval=1800):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self.persist_path = persist_path
        self.notify_interval = notify_interval  # TG 通知最小间隔(秒)，默认30分钟
        self._state = State.CLOSED
        self._failures = 0
        self._last_failure = 0.0
        self._last_error = None
        self._half_tries = 0
        self._last_notify = 0.0
        self._load()

    # ── 持久化 ──
    def _load(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path) as f:
                d = json.load(f)
            self._state = State(d.get("state", "closed"))
            self._failures = d.get("failures", 0)
            self._last_failure = d.get("last_failure", 0.0)
            self._last_error = d.get("last_error")
            self._last_notify = d.get("last_notify", 0.0)
        except Exception:
            pass

    def _save(self):
        if not self.persist_path:
            return
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        with open(self.persist_path, "w") as f:
            json.dump({
                "state": self._state.value,
                "failures": self._failures,
                "last_failure": self._last_failure,
                "last_error": self._last_error,
                "last_notify": self._last_notify,
            }, f)

    # ── 核心方法 ──
    def available(self):
        """是否可以尝试请求"""
        if self._state == State.CLOSED:
            return True

        if self._state == State.OPEN:
            if time.time() - self._last_failure >= self.cooldown:
                self._state = State.HALF_OPEN
                self._half_tries = 0
                self._save()
                return True
            return False

        if self._state == State.HALF_OPEN:
            return self._half_tries < 1  # 只给一次试探机会

        return True

    def success(self):
        """记录成功"""
        self._state = State.CLOSED
        self._failures = 0
        self._half_tries = 0
        self._last_error = None
        self._save()

    def failure(self, error=None):
        """记录失败"""
        self._failures += 1
        self._last_failure = time.time()
        self._last_error = str(error)[:200] if error else None

        old_state = self._state
        if self._state == State.HALF_OPEN:
            self._state = State.OPEN
        elif self._failures >= self.threshold:
            self._state = State.OPEN

        # 首次进入 OPEN 时记录通知时间
        if old_state != State.OPEN and self._state == State.OPEN:
            self._last_notify = time.time()

        self._save()

    def should_notify(self):
        """是否应该发送通知（限频：notify_interval 内只发一次）"""
        if self._state != State.OPEN:
            return False
        return (time.time() - self._last_notify) >= self.notify_interval

    def mark_notified(self):
        """标记已通知，重置计时器"""
        self._last_notify = time.time()
        self._save()

    def reset(self):
        """手动重置"""
        self._state = State.CLOSED
        self._failures = 0
        self._half_tries = 0
        self._last_error = None
        self._last_notify = 0.0
        self._save()

    # ── 状态查询 ──
    @property
    def is_blocked(self):
        return self._state == State.OPEN

    @property
    def state(self):
        return self._state.value

    @property
    def failures(self):
        return self._failures

    @property
    def last_error(self):
        return self._last_error

    @property
    def remaining_cooldown(self):
        if self._state != State.OPEN:
            return 0
        return max(0, self.cooldown - (time.time() - self._last_failure))
