"""Loader for the language-neutral strategy spec in packages/strategy-spec."""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any, cast


def spec_dir() -> Path:
    override = os.environ.get("STRATEGY_SPEC_DIR")
    if override:
        return Path(override)
    # services/api/app/contracts.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3] / "packages" / "strategy-spec"


@cache
def load_spec(name: str) -> dict[str, Any]:
    path = spec_dir() / f"{name}.json"
    with path.open(encoding="utf-8") as fh:
        return cast(dict[str, Any], json.load(fh))


def strategy_version() -> str:
    return str(load_spec("strategy_version")["strategyVersion"])


_ALLOWED_AUTHORITY = frozenset({"FAIL_SAFE_ONLY", "FULL"})


def verdict_authority() -> str:
    """Effective verdict authority.

    Returns the VERDICT_AUTHORITY env override when it is set to a valid value (FAIL_SAFE_ONLY or FULL),
    otherwise the committed strategy spec. This lets an operator enable directional verdicts (FULL) per
    deployment via .env without editing the versioned spec; the safe default stays FAIL_SAFE_ONLY, so
    tests and any environment without the override keep fail-safe behaviour.
    """
    from app.config import get_settings

    override = (get_settings().verdict_authority or "").strip().upper()
    if override in _ALLOWED_AUTHORITY:
        return override
    return str(load_spec("strategy_version")["verdictAuthority"])
