#!/usr/bin/env bash
# Static guardrails: fail if application code introduces broker / order-execution surfaces,
# TradingView as a data feed, or secrets exposed to the browser bundle.
set -euo pipefail
cd "$(dirname "$0")/.."

CODE_DIRS=(services/api/app apps/web/src packages/shared-types/src)
fail=0

check() {
  local label="$1" pattern="$2"
  if grep -RInE --include='*.py' --include='*.ts' --include='*.tsx' "$pattern" "${CODE_DIRS[@]}"; then
    echo "GUARDRAIL VIOLATION: $label" >&2
    fail=1
  fi
}

check "order execution" '(place|submit|send|execute|cancel|modify)_?[Oo]rder'
check "broker SDKs" '(MetaTrader5|oandapyV20|ib_insync|ibapi|alpaca_trade_api|ccxt|fxcmpy|ctrader)'
check "TradingView as data source" '(tradingview|TradingView).*(datafeed|udf|fetch|quote|history)'
check "browser-exposed secrets" 'NEXT_PUBLIC_[A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)'

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "guardrails: OK"
