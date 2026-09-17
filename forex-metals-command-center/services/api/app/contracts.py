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
