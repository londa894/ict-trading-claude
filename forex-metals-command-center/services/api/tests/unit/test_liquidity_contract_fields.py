import json
from pathlib import Path

import pytest

from app.services.liquidity.models import (
    DolSelection,
    DolTarget,
    LiquidityAnalysis,
    LiquidityEvent,
    LiquidityPool,
)

CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


@pytest.mark.parametrize("model", [LiquidityPool, LiquidityEvent, DolTarget, DolSelection, LiquidityAnalysis])
def test_liquidity_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
