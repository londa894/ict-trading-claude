"""Roll the manual risk profile to the current trading day so the RISK gate never stale-locks.

The risk engine (services/risk) locks ACCOUNT_STATE_STALE when the profile's `state.tradingDay` is not the
current trading day, which rolls at the New York 17:00 close (`trading_day_of`). Run on a schedule (every few
hours) so the profile stays current. On a genuine day change it also zeroes the *today* counters — a normal
daily rollover — while leaving weekly totals, consecutive losses, and open positions untouched.

Runs with the project venv (imports the app for the exact trading-day rule). Reads RISK_PROFILE_PATH from the
process env or services/api/.env. Writes atomically. Idempotent: does nothing when already on today's date.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # services/api, so `app` imports when run by path

from app.config import get_settings  # noqa: E402
from app.services.sessions.clock import trading_day_of  # noqa: E402


def _profile_path() -> Path:
    path = os.environ.get("RISK_PROFILE_PATH") or get_settings().risk_profile_path
    if not path:
        raise SystemExit("RISK_PROFILE_PATH is not set (env or services/api/.env)")
    return Path(path)


def main() -> None:
    path = _profile_path()
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read risk profile at {path}: {exc}")

    today = trading_day_of(datetime.now(UTC)).isoformat()
    state = profile.setdefault("state", {})
    current = state.get("tradingDay")
    if current == today:
        print(f"risk profile already on trading day {today}; no change", file=sys.stderr)
        return

    state["tradingDay"] = today
    # New trading day: reset today's counters (a real rollover). Weekly totals, consecutive losses and open
    # positions are intentionally preserved.
    state["realizedPnlToday"] = 0
    state["tradesToday"] = 0

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as fh:
        json.dump(profile, fh, indent=2)
        tmp = Path(fh.name)
    tmp.replace(path)
    print(f"risk profile rolled {current} -> {today} (today counters reset)", file=sys.stderr)


if __name__ == "__main__":
    main()
