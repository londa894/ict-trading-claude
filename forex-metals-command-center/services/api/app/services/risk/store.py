"""Server-side manual account profile file. Read-only for the API; never logged, committed or echoed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from app.services.risk.models import RiskProfileFile


class ProfileLoadStatus(StrEnum):
    MISSING = "MISSING"
    INVALID = "INVALID"
    OK = "OK"


@dataclass(frozen=True)
class ProfileLoad:
    status: ProfileLoadStatus
    file: RiskProfileFile | None = None
    error: str | None = None


def _describe(exc: ValidationError) -> str:
    # Field locations and messages only: input values (balances, P/L) are never echoed.
    return "; ".join(
        ".".join(str(p) for p in e["loc"]) + f": {e['msg']}" for e in exc.errors(include_input=False)
    )


class RiskProfileStore:
    """Re-reads the file when it changes (mtime/size), so edits apply without a restart."""

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None
        self._key: tuple[int, int] | None = None
        self._cached = ProfileLoad(ProfileLoadStatus.MISSING)

    def load(self) -> ProfileLoad:
        if self._path is None:
            return ProfileLoad(ProfileLoadStatus.MISSING, error="RISK_PROFILE_PATH is not set")
        try:
            stat = self._path.stat()
        except OSError:
            self._key = None
            return ProfileLoad(ProfileLoadStatus.MISSING, error="the risk profile file does not exist")
        key = (stat.st_mtime_ns, stat.st_size)
        if key == self._key:
            return self._cached
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            result = ProfileLoad(ProfileLoadStatus.OK, file=RiskProfileFile.model_validate(raw))
        except ValidationError as exc:
            result = ProfileLoad(ProfileLoadStatus.INVALID, error=_describe(exc))
        except (OSError, ValueError) as exc:
            result = ProfileLoad(
                ProfileLoadStatus.INVALID, error=f"unreadable profile file ({type(exc).__name__})"
            )
        self._key, self._cached = key, result
        return result
