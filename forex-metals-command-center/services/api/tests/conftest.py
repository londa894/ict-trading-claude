"""Test-wide fixtures.

The live .env may set VERDICT_AUTHORITY=FULL to enable directional verdicts in production. Tests must
stay on the committed fail-safe default so the gate/authority tests assert the shipped behaviour. An OS
env var takes precedence over the .env file in pydantic-settings, so pinning it empty here makes
`verdict_authority()` fall back to the strategy spec (FAIL_SAFE_ONLY) regardless of the local .env.
"""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fail_safe_verdict_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_AUTHORITY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
