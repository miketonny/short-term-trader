#!/usr/bin/env python3
"""Validate mode for Multi-ETF strategy - no network, no trading.

Checks: config, API keys, dependencies, local modules, directories,
function signatures, and circuit breaker state.

Usage: python3 ibkr_strategy.py --validate
"""

import importlib
import inspect
import os
import sys
import json
from datetime import datetime


def run_validate():
    print(f"=== Multi-ETF V1 | Validate | {datetime.now().strftime('%H:%M:%S')} ===")

    # Import constants from the strategy module (already loaded)
    from ibkr_strategy import (
        CONFIG_FILE, SYMBOLS, TWELVE_DATA_KEY, DASHBOARD_DIR,
        MODE, place_and_confirm, run, _circuit,
    )

    errors = []

    def check(ok, msg):
        print(f"  {'✅' if ok else '❌'} {msg}")
        if not ok:
            errors.append(msg)

    # 1. Config file
    print("\n── Config ──")
    check(os.path.exists(CONFIG_FILE), f"Config: {CONFIG_FILE}")
    if os.path.exists(CONFIG_FILE):
        try:
            cfg = json.load(open(CONFIG_FILE))
            syms = cfg.get("symbols", SYMBOLS)
            check(len(syms) > 0, f"Symbols configured: {syms}")
            check(isinstance(cfg.get("position_alloc"), (int, float, type(None))),
                  f"Position alloc: {cfg.get('position_alloc', 'default')}")
        except json.JSONDecodeError as e:
            check(False, f"Config JSON: {e}")

    # 2. API keys
    print("\n── API Keys ──")
    key_ok = bool(TWELVE_DATA_KEY) and TWELVE_DATA_KEY != "YOUR_KEY_HERE"
    suffix = TWELVE_DATA_KEY[-4:] if TWELVE_DATA_KEY and len(TWELVE_DATA_KEY) > 4 else ""
    check(key_ok, f"TwelveData Key: {'***' + suffix if suffix else 'MISSING'}")
    check(len(SYMBOLS) > 0, f"Symbols ({len(SYMBOLS)}): {SYMBOLS}")

    # 3. Critical dependencies
    print("\n── Dependencies ──")
    for mod_name in ["numpy", "ib_insync", "httpx", "requests"]:
        try:
            importlib.import_module(mod_name)
            check(True, f"import {mod_name}")
        except ImportError as e:
            check(False, f"import {mod_name}: {e}")

    # 4. Local modules
    print("\n── Local Modules ──")
    for mod_name in ["circuit_breaker", "tg_notify", "data_cache",
                     "rate_limiter", "notifier", "advisor_client"]:
        try:
            importlib.import_module(mod_name)
            check(True, f"import {mod_name}")
        except ImportError as e:
            check(False, f"import {mod_name}: {e}")

    # 5. Output directories
    print("\n── Directories ──")
    check(os.path.isdir(DASHBOARD_DIR) and os.access(DASHBOARD_DIR, os.W_OK),
          f"Dashboard writable: {DASHBOARD_DIR}")
    cfg_dir = os.path.dirname(CONFIG_FILE)
    check(os.path.isdir(cfg_dir), f"Config dir: {cfg_dir}")

    # 6. Key function signatures
    print("\n── Functions ──")
    sig = inspect.signature(place_and_confirm)
    check("mode" in sig.parameters, "place_and_confirm() has 'mode' param")
    check(inspect.iscoroutinefunction(place_and_confirm),
          "place_and_confirm() is async")
    check(inspect.iscoroutinefunction(run), "run() is async")

    # 7. Circuit breaker
    print("\n── Circuit Breaker ──")
    if _circuit.available():
        check(True, "Circuit: OK (available)")
    elif _circuit.is_blocked:
        check(False, f"Circuit: BLOCKED ({_circuit.failures} failures)")
        print(f"    Last error: {_circuit.last_error}")
    else:
        check(True, f"Circuit: cooling ({int(_circuit.remaining_cooldown)}s remaining)")

    # Summary
    print(f"\n{'='*40}")
    if errors:
        print(f"FAIL ({len(errors)} errors):")
        for e in errors:
            print(f"  -> {e}")
        sys.exit(1)
    else:
        print(f"PASS ({MODE} mode, {len(SYMBOLS)} symbols)")
        sys.exit(0)
