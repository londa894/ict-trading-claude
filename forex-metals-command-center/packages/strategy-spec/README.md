# strategy-spec

Language-neutral, versioned configuration shared by the Python API and the TypeScript web app.

| File | Purpose |
|---|---|
| `enums.json` | Canonical enum values. Both languages are contract-tested against it. |
| `strategy_version.json` | Current strategy version and verdict authority (`FAIL_SAFE_ONLY` until Phase 8). |
| `data_quality.json` | Staleness, bad-tick and provider-disagreement thresholds. |
| `market_hours.json` | Weekly FX/metals trading hours in New York time (DST handled in code). |
| `instruments.json` | Instrument identity. Contract specs are `null` on purpose: never guess them. |

Any threshold change must bump `strategyVersion`.
