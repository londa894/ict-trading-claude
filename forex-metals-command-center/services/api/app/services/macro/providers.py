"""Macro data providers behind one interface. No scraping; the file provider reads a server-side file."""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.domain.enums import MacroSeriesId
from app.services.macro.models import MacroFile, MacroObservation, MacroSeries, MacroSnapshot
from app.services.timeframes.core import NEW_YORK


class MacroUnavailableError(RuntimeError):
    pass


class MacroProvider(Protocol):
    name: str
    is_synthetic: bool

    async def snapshot(self, now: datetime) -> MacroSnapshot: ...


class UnconfiguredMacro:
    name = "unconfigured"
    is_synthetic = False

    async def snapshot(self, now: datetime) -> MacroSnapshot:
        raise MacroUnavailableError("no macro data provider is configured (MACRO_PROVIDER)")


class FileMacro:
    """Reads MACRO_FILE_PATH; re-reads when it changes. Errors list field paths only."""

    name = "file"
    is_synthetic = False

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None
        self._key: tuple[int, int] | None = None
        self._cached: MacroFile | None = None
        self._error: str | None = None

    async def snapshot(self, now: datetime) -> MacroSnapshot:
        if self._path is None:
            raise MacroUnavailableError("MACRO_FILE_PATH is not set")
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise MacroUnavailableError("the macro file does not exist") from exc
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._key:
            self._key, self._cached, self._error = key, None, None
            try:
                self._cached = MacroFile.model_validate(json.loads(self._path.read_text(encoding="utf-8")))
            except ValidationError as exc:
                self._error = "; ".join(
                    ".".join(str(p) for p in e["loc"]) + f": {e['msg']}"
                    for e in exc.errors(include_input=False)
                )
            except (OSError, ValueError) as exc:
                self._error = f"unreadable macro file ({type(exc).__name__})"
        if self._cached is None:
            raise MacroUnavailableError(f"invalid macro file: {self._error}")
        f = self._cached
        return MacroSnapshot(self.name, f.source, False, f.fetched_at, {s.id: s for s in f.series})


_FIXTURE = {
    MacroSeriesId.DXY: ("SYNTHETIC dollar index", "index", 104.0, 0.35),
    MacroSeriesId.US2Y: ("SYNTHETIC US 2Y yield", "%", 4.9, 0.05),
    MacroSeriesId.US10Y: ("SYNTHETIC US 10Y yield", "%", 4.5, 0.045),
    MacroSeriesId.US10Y_REAL: ("SYNTHETIC US 10Y real yield", "%", 2.1, 0.04),
    MacroSeriesId.VIX: ("SYNTHETIC volatility index", "index", 15.0, 0.6),
}


class FixtureMacro:
    """Deterministic SYNTHETIC daily series ending at the clock's date. Never scored."""

    name = "fixture"
    is_synthetic = True

    async def snapshot(self, now: datetime) -> MacroSnapshot:
        end = now.astimezone(NEW_YORK).date()
        days = [end - timedelta(days=i) for i in range(89, -1, -1)]
        weekdays = [d for d in days if d.weekday() < 5]
        series: dict[MacroSeriesId, MacroSeries] = {}
        for sid, (name, unit, start, vol) in _FIXTURE.items():
            rng = random.Random(f"macro:{sid.value}")  # noqa: S311 - deterministic test data
            value, observations = start, []
            for i, d in enumerate(weekdays):
                value += rng.gauss(0, vol) + 0.2 * vol * math.sin(i / 9)
                observations.append(MacroObservation(date=d, value=round(value, 4)))
            series[sid] = MacroSeries(id=sid, name=name, unit=unit, observations=observations)
        return MacroSnapshot(self.name, "synthetic fixture series", True, now, series)


def create_macro(kind: str, path: str) -> MacroProvider:
    if kind == "file":
        return FileMacro(path or None)
    if kind == "fixture":
        return FixtureMacro()
    return UnconfiguredMacro()
