"""Async client for the forex-advisor service - shadow-only, never blocks trading."""

import json
import os
import uuid
from datetime import datetime, timezone

import httpx

ADVISOR_URL = "http://127.0.0.1:8001"
DEFAULT_LOG = "/root/forex_dashboard/advisor_log.json"


async def call_advisor(endpoint: str, ctx: dict, timeout: float = 15.0) -> dict | None:
    """POST to advisor service. Returns parsed JSON or None on any error."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{ADVISOR_URL}{endpoint}", json=ctx)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


def _write_jsonl(line_data: dict, path: str) -> None:
    """Append one NDJSON line to the given path."""
    try:
        line = json.dumps(line_data, ensure_ascii=False, default=str)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_advisor_call(kind: str, symbol: str, rule_action: str, ctx: dict,
                     advice: dict | None, log_path: str | None = None,
                     shadow: dict | None = None) -> None:
    """Log advisor interaction. Uses DEFAULT_LOG unless log_path is specified."""
    entry = {
        "signal_id": uuid.uuid4().hex[:12],
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind, "symbol": symbol, "rule_action": rule_action,
        "context": ctx, "decision": advice,
        "shadow": shadow,
    }
    _write_jsonl(entry, log_path or DEFAULT_LOG)


def build_entry_context(symbol, ts, price, rsi, sma, bb_upper, bb_lower,
                        adx, macd_signal, macd_line, nlv_usd, qty, mode, checks, session_trades):
    """Build dict for /evaluate_entry."""
    return {
        "symbol": symbol, "ts": ts, "price": price,
        "rsi": rsi, "sma_20": sma, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "adx": adx, "macd_signal": macd_signal, "macd_line": macd_line,
        "nlv_usd": nlv_usd, "proposed_qty": qty,
        "signal_mode": mode, "signal_checks": checks,
        "session_history": session_trades,
    }


def build_position_context(symbol, ts, price, avg_cost, qty, entry_time,
                           mode, trailing_stop, pnl_usd, rsi, sma, adx, nlv_usd, session_trades):
    """Build dict for /evaluate_position."""
    return {
        "symbol": symbol, "ts": ts, "price": price,
        "avg_cost": avg_cost, "qty": qty, "entry_time": entry_time,
        "pos_mode": "long", "trailing_stop": trailing_stop,
        "pnl_usd": pnl_usd, "rsi": rsi, "sma_20": sma, "adx": adx,
        "nlv_usd": nlv_usd, "session_history": session_trades,
    }
