"""Fetch the JBlanked economic calendar and write the news gate's CalendarFile JSON.

The news gate (services/news) reads CALENDAR_FILE_PATH as a `CalendarFile`: a source, a fetch time,
a coverage window, and a list of events. The engine treats the calendar as UNAVAILABLE (and blocks the
decision) unless it was fetched < calendarMaxAgeHours ago AND its coverage spans roughly
[now - 5.25h, now + 24h]. So this must run at least daily and fetch a window that brackets "now".

Source: https://www.jblanked.com/news/api/{source}/calendar/range/?from=YYYY-MM-DD&to=YYYY-MM-DD
  Auth: header `Authorization: Api-Key <JBLANKED_API_KEY>` (free tier: one call / 5 min).
  Each event: Name, Currency, Event_ID, Category, Impact (High/Medium/Low/None), Date, Actual,
  Forecast, Previous, Outcome, Strength, Quality.

Timezone: the feed's `Date` has no zone. Empirically it is MT5 server time (EET/EEST). We parse it in
`--source-tz` (default Europe/Bucharest, i.e. EET/EEST) and convert to UTC, which self-corrects for the
Oct/Mar DST change. Verify once against a known release (e.g. US CPI at 08:30 New York) after a DST flip.

Stdlib only, so it runs with or without the project venv. Writes atomically. No app import: the app
validates the file on read (extra keys are rejected, so we emit only known fields).

Usage:
  python scripts/fetch_calendar.py                 # uses env JBLANKED_API_KEY + CALENDAR_FILE_PATH
  python scripts/fetch_calendar.py --out cal.json --before-days 2 --after-days 9 --source mql5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

API_BASE = "https://www.jblanked.com/news/api"
IMPACT_TO_IMPORTANCE = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}  # "None"/other -> skipped
CURRENCY_COUNTRY = {
    "USD": "United States",
    "EUR": "Euro Area",
    "GBP": "United Kingdom",
    "JPY": "Japan",
    "AUD": "Australia",
    "NZD": "New Zealand",
    "CAD": "Canada",
    "CHF": "Switzerland",
    "CNY": "China",
}


def _env_fallback(name: str) -> str:
    """Read a var from the process env, else from services/api/.env (so a scheduled run needs no secret
    on its command line). Best-effort: a missing or unreadable .env just yields ''."""
    if os.environ.get(name):
        return os.environ[name]
    env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _tzinfo(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SystemExit(
            f"cannot load timezone {name!r} ({exc}); run with the project venv (it has tzdata) "
            "or pass a valid --source-tz"
        )


def fetch_range(source: str, api_key: str, start: datetime, end: datetime) -> list[dict]:
    url = f"{API_BASE}/{source}/calendar/range/?from={start:%Y-%m-%d}&to={end:%Y-%m-%d}"
    req = urllib.request.Request(url, headers={"Authorization": f"Api-Key {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 - fixed https host
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        hint = " (free tier allows one call per 5 minutes)" if exc.code in (400, 429) else ""
        raise SystemExit(f"JBlanked API returned HTTP {exc.code}{hint}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach the JBlanked API: {exc.reason}")
    if isinstance(payload, dict):  # error bodies come back as an object, not a list
        raise SystemExit(f"JBlanked API error: {payload}")
    if not isinstance(payload, list):
        raise SystemExit(f"unexpected JBlanked response type: {type(payload).__name__}")
    return payload


def to_event(raw: dict, src_tz, seen: set[str]) -> dict | None:
    importance = IMPACT_TO_IMPORTANCE.get(str(raw.get("Impact", "")).upper())
    if importance is None:
        return None  # "None"/holiday rows never gate the decision
    currency = str(raw.get("Currency", "")).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        return None
    try:
        naive = datetime.strptime(str(raw["Date"]), "%Y.%m.%d %H:%M:%S")
    except (KeyError, ValueError):
        return None
    when = naive.replace(tzinfo=src_tz).astimezone(UTC)
    outcome = str(raw.get("Outcome", ""))
    not_loaded = "Not Loaded" in outcome
    event_id = raw.get("Event_ID") or 0
    base_id = f"{currency}:{event_id}:{when:%Y%m%dT%H%M}"
    ident, n = base_id, 1
    while ident in seen:  # Event_ID is 0 for some rows; keep ids unique
        ident, n = f"{base_id}:{n}", n + 1
    seen.add(ident)

    def num(key: str):
        v = raw.get(key)
        return v if isinstance(v, (int, float)) else None

    return {
        "id": ident[:120],
        "country": CURRENCY_COUNTRY.get(currency, currency),
        "currency": currency,
        "name": str(raw.get("Name", "")).strip()[:200] or "Unnamed event",
        "scheduledTime": when.isoformat(),
        "importance": importance,
        "actual": None if not_loaded else num("Actual"),
        "forecast": num("Forecast"),
        "previous": num("Previous"),
    }


def build_calendar(
    raw_events: list[dict],
    source: str,
    src_tz,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    seen: set[str] = set()
    events = [e for e in (to_event(r, src_tz, seen) for r in raw_events) if e is not None]
    if not events:
        raise SystemExit("no usable events returned; nothing written")
    events.sort(key=lambda e: e["scheduledTime"])
    # Coverage is the QUERIED window, NOT the min/max of returned events. A quiet stretch (e.g. Mon-Tue with
    # no releases) leaves events clustered later in the week; claiming only that cluster makes the news gate
    # reject the file for "not covering the recent past". The calendar is complete for the whole window.
    return {
        "source": f"JBlanked {source} calendar",
        "fetchedAt": now.isoformat(),
        "coverageStart": window_start.isoformat(),
        "coverageEnd": window_end.isoformat(),
        "events": events,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch JBlanked calendar -> CalendarFile JSON")
    ap.add_argument("--out", default=_env_fallback("CALENDAR_FILE_PATH"))
    ap.add_argument("--api-key", default=_env_fallback("JBLANKED_API_KEY"))
    ap.add_argument("--source", default="mql5", choices=["mql5", "forex-factory", "fxstreet"])
    ap.add_argument("--source-tz", default=_env_fallback("JBLANKED_SOURCE_TZ") or "Europe/Bucharest")
    ap.add_argument("--before-days", type=int, default=2)  # >= 1 so coverage_start <= now - 5.25h
    ap.add_argument("--after-days", type=int, default=9)  # >= 2 so coverage_end >= now + 24h
    args = ap.parse_args()

    if not args.api_key:
        raise SystemExit("no API key: set JBLANKED_API_KEY or pass --api-key")
    if not args.out:
        raise SystemExit("no output path: set CALENDAR_FILE_PATH or pass --out")

    now = datetime.now(UTC)
    src_tz = _tzinfo(args.source_tz)
    window_start = now - timedelta(days=args.before_days)
    window_end = now + timedelta(days=args.after_days)
    raw = fetch_range(args.source, args.api_key, window_start, window_end)
    calendar = build_calendar(raw, args.source, src_tz, now, window_start, window_end)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=out.parent, delete=False, suffix=".tmp") as fh:
        json.dump(calendar, fh, indent=2)
        tmp = Path(fh.name)
    tmp.replace(out)
    print(
        f"wrote {len(calendar['events'])} events to {out}\n"
        f"  coverage {calendar['coverageStart']} .. {calendar['coverageEnd']}  (fetched {calendar['fetchedAt']})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
