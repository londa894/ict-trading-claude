from app.contracts import load_spec, strategy_version
from app.domain.enums import CONTRACT_ENUMS, PositionSizeStatus
from app.domain.instrument import instrument_registry


def test_python_enums_match_strategy_spec_exactly():
    spec = load_spec("enums")
    spec_names = {k for k in spec if not k.startswith("$")}
    assert spec_names == set(CONTRACT_ENUMS), "enum sets diverged between spec and Python"
    for name, enum_cls in CONTRACT_ENUMS.items():
        assert [m.value for m in enum_cls] == spec[name], f"{name} values/order diverged"


def test_strategy_version_is_fail_safe():
    sv = load_spec("strategy_version")
    assert strategy_version() == "0.20.0-phase20"
    assert sv["phase"] == 20
    assert sv["verdictAuthority"] == "FAIL_SAFE_ONLY"


def test_xauusd_is_primary_and_deeply_validated():
    reg = instrument_registry()
    primary = min(reg.values(), key=lambda i: i.priority)
    assert primary.symbol == "XAUUSD"
    assert primary.deeply_validated is True
    assert {"XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"} <= set(reg)


def test_contract_specs_are_never_guessed():
    for inst in instrument_registry().values():
        assert inst.spec is None
        assert inst.position_size_status is PositionSizeStatus.POSITION_SIZE_UNVERIFIED
