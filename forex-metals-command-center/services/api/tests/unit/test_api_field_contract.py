import json
from pathlib import Path

from app.api.routes import SystemStatus
from app.domain.decision import MasterDecision
from app.services.candles.service import CHART_TIMEFRAMES, ChartCandle, ChartSeriesResponse
from app.services.market_state.service import DataReport

CONTRACT = Path(__file__).resolve().parents[4] / "packages" / "shared-types" / "contract" / "api_fields.json"


def aliases(model):
    return sorted(f.alias or name for name, f in model.model_fields.items())


def test_python_wire_fields_match_shared_contract():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert aliases(MasterDecision) == sorted(contract["MasterDecision"])
    assert aliases(DataReport) == sorted(contract["DataReport"])
    assert aliases(SystemStatus) == sorted(contract["SystemStatus"])
    assert aliases(ChartCandle) == sorted(contract["ChartCandle"])
    assert aliases(ChartSeriesResponse) == sorted(contract["ChartSeriesResponse"])
    assert [t.value for t in CHART_TIMEFRAMES] == contract["ChartTimeframes"]
