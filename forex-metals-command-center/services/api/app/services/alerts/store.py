"""In-memory alert feed: sequence numbers, dedupe keys, per-category cooldowns and a bounded buffer."""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timedelta

from app.contracts import strategy_version
from app.services.alerts.models import Alert, AlertCandidate, AlertConfig


class AlertStore:
    def __init__(self, cfg: AlertConfig) -> None:
        self._cfg = cfg
        self._seq = 0
        self._buffer: deque[Alert] = deque(maxlen=cfg.buffer_size)
        self._last_by_key: dict[str, datetime] = {}
        self.suppressed: Counter[str] = Counter()

    @property
    def last_seq(self) -> int:
        return self._seq

    def add(self, c: AlertCandidate, now: datetime) -> Alert | None:
        """Returns the stored alert, or None when the same dedupe key fired within its category cooldown."""
        category, priority = self._cfg.types[c.type]
        key = f"{c.symbol}:{c.type.value}:{c.reference}"
        last = self._last_by_key.get(key)
        if last is not None and now - last < timedelta(seconds=self._cfg.category_cooldown[category]):
            self.suppressed[c.type.value] += 1
            return None
        self._last_by_key[key] = now
        self._seq += 1
        alert = Alert(
            id=f"ALERT:{self._seq}",
            seq=self._seq,
            dedupe_key=key,
            symbol=c.symbol,
            type=c.type,
            category=category,
            priority=priority,
            title=c.title,
            message=c.message,
            direction=c.direction,
            price=c.price,
            occurred_at=c.occurred_at,
            created_at=now,
            strategy_version=strategy_version(),
        )
        self._buffer.append(alert)
        self._prune(now)
        return alert

    def list(self) -> list[Alert]:
        return list(reversed(self._buffer))

    def _prune(self, now: datetime) -> None:
        horizon = timedelta(seconds=max(self._cfg.category_cooldown.values()))
        if len(self._last_by_key) > 4 * self._cfg.buffer_size:
            self._last_by_key = {k: t for k, t in self._last_by_key.items() if now - t < horizon}
