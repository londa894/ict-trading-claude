import json
from pathlib import Path

import pytest

from app.services.structure.models import (
    LevelStructure,
    MtfStructureResponse,
    StructureAnalysis,
    StructureEvent,
    Swing,
    TimeframeStructureState,
)

CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


@pytest.mark.parametrize(
    "model",
    [Swing, StructureEvent, LevelStructure, StructureAnalysis, TimeframeStructureState, MtfStructureResponse],
)
def test_structure_wire_fields_match_shared_contract(model):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert sorted(f.alias or n for n, f in model.model_fields.items()) == sorted(contract[model.__name__])
