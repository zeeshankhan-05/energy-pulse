"""Tests for the normalization pipeline (normalization.py).

Test scenario (5 records):
  1. Valid EIA electricity record  — unit "cents/kWh",  price=9.5  (valid)
  2. Valid natural-gas record      — unit "$/therm",    price=5.0  (valid)
  3. Duplicate record              — identical to #1 but pre-inserted in DB
  4. Invalid price=0               — rejected by validate_record
  5. Unknown region "ZZ"          — rejected by validate_record

Expected pipeline summary: 5 received, 2 validated, 2 rejected, 1 duplicate, 2 inserted
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.price_snapshot import PriceSnapshot
from app.services.normalization import (
    UNIT_CONVERSIONS,
    STATE_REGION_MAP,
    normalize_unit,
    normalize_period,
    validate_record,
    deduplicate_records,
    normalize_pipeline,
)


# ---------------------------------------------------------------------------
# Shared SQLite fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_session():
    """Fresh in-memory SQLite session — no PostgreSQL required."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    db = SessionFactory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Helper: mock Redis so tests don't need a live Redis server
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Replace _redis_client() with a mock that stores data in-memory."""
    store: dict[str, list] = {}

    class _FakePipeline:
        def __init__(self):
            self._cmds = []

        def rpush(self, key, value):
            self._cmds.append(("rpush", key, value))
            return self

        def ltrim(self, key, start, end):
            return self

        def execute(self):
            for cmd, key, value in self._cmds:
                store.setdefault(key, []).append(value)

    class _FakeRedis:
        def rpush(self, key, value):
            store.setdefault(key, []).append(value)

        def lrange(self, key, start, end):
            return store.get(key, [])

        def pipeline(self):
            return _FakePipeline()

    fake = _FakeRedis()

    import app.services.normalization as norm_mod
    monkeypatch.setattr(norm_mod, "_redis_client", lambda: fake)

    # Expose the store so tests can inspect it
    fake._store = store
    return fake


# ---------------------------------------------------------------------------
# Unit tests: normalize_unit
# ---------------------------------------------------------------------------

def test_normalize_unit_cents_kwh():
    price, unit = normalize_unit(9.5, "cents/kWh")
    assert unit == "$/kWh"
    assert pytest.approx(price, rel=1e-5) == 0.095


def test_normalize_unit_therm():
    price, unit = normalize_unit(5.0, "$/therm")
    assert unit == "$/MMBtu"
    assert pytest.approx(price, rel=1e-5) == 50.0


def test_normalize_unit_mwh():
    price, unit = normalize_unit(50.0, "$/MWh")
    assert unit == "$/kWh"
    assert pytest.approx(price, rel=1e-5) == 0.05


def test_normalize_unit_passthrough_kwh():
    price, unit = normalize_unit(0.12, "$/kWh")
    assert unit == "$/kWh"
    assert pytest.approx(price, rel=1e-5) == 0.12


def test_normalize_unit_unknown_raises():
    with pytest.raises(ValueError, match="Unknown unit"):
        normalize_unit(5.0, "$/gallon")


def test_normalize_unit_case_insensitive():
    """Lowercase variant should still resolve."""
    price, unit = normalize_unit(9.5, "cents/kwh")
    assert unit == "$/kWh"


# ---------------------------------------------------------------------------
# Unit tests: normalize_period
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_str,expected", [
    ("2024-11",       "2024-11"),
    ("2024-11-01",    "2024-11"),
    ("November 2024", "2024-11"),
    ("Nov 2024",      "2024-11"),
    ("11/2024",       "2024-11"),
    ("Q1 2024",       "2024-01"),
    ("Q2 2024",       "2024-04"),
    ("Q3 2024",       "2024-07"),
    ("Q4 2024",       "2024-10"),
])
def test_normalize_period(input_str, expected):
    assert normalize_period(input_str) == expected


def test_normalize_period_invalid():
    with pytest.raises(ValueError, match="Cannot parse"):
        normalize_period("not-a-date")


# ---------------------------------------------------------------------------
# Unit tests: validate_record
# ---------------------------------------------------------------------------

_VALID_RECORD = {
    "source": "EIA",
    "region": "IL",
    "fuel_type": "electricity",
    "price": 9.5,
    "unit": "cents/kWh",
    "period": "2024-11",
}


def test_validate_record_valid():
    ok, reason = validate_record(_VALID_RECORD)
    assert ok is True
    assert reason == ""


def test_validate_record_missing_field():
    bad = {**_VALID_RECORD}
    del bad["source"]
    ok, reason = validate_record(bad)
    assert ok is False
    assert "source" in reason


def test_validate_record_price_zero():
    bad = {**_VALID_RECORD, "price": 0}
    ok, reason = validate_record(bad)
    assert ok is False
    assert ">" in reason or "0" in reason


def test_pipeline_rejects_price_too_high_after_normalization(sqlite_session):
    """A $/kWh-normalized price >= 10 is caught in the pipeline, not validate_record."""
    # 1500 cents/kWh normalises to 15.0 $/kWh — clearly erroneous data
    bad_record = {**_VALID_RECORD, "price": 1500.0, "unit": "cents/kWh"}
    summary = normalize_pipeline([bad_record], sqlite_session)
    assert summary["rejected"] == 1
    assert "sanity" in summary["rejected_reasons"][0].lower()


def test_validate_record_unknown_region():
    bad = {**_VALID_RECORD, "region": "ZZ"}
    ok, reason = validate_record(bad)
    assert ok is False
    assert "ZZ" in reason


def test_validate_record_non_numeric_price():
    bad = {**_VALID_RECORD, "price": "not-a-number"}
    ok, reason = validate_record(bad)
    assert ok is False
    assert "numeric" in reason.lower()


# ---------------------------------------------------------------------------
# Unit tests: deduplicate_records
# ---------------------------------------------------------------------------

def test_deduplicate_filters_existing(sqlite_session):
    # Pre-insert a record directly
    sqlite_session.add(PriceSnapshot(
        source="EIA", region="IL", fuel_type="electricity",
        price=0.095, unit="$/kWh", period="2024-11",
    ))
    sqlite_session.commit()

    records = [
        {"source": "EIA", "region": "IL", "fuel_type": "electricity",
         "price": 0.095, "unit": "$/kWh", "period": "2024-11"},   # duplicate
        {"source": "EIA", "region": "TX", "fuel_type": "electricity",
         "price": 0.110, "unit": "$/kWh", "period": "2024-11"},   # new
    ]
    result = deduplicate_records(records, sqlite_session)
    assert len(result) == 1
    assert result[0]["region"] == "TX"


def test_deduplicate_empty_input(sqlite_session):
    assert deduplicate_records([], sqlite_session) == []


# ---------------------------------------------------------------------------
# Integration test: full normalize_pipeline
# ---------------------------------------------------------------------------

# The 5 test records
_RECORD_EIA_ELEC = {
    "source": "EIA",
    "region": "IL",
    "fuel_type": "electricity",
    "price": 9.5,          # 9.5 cents/kWh → 0.095 $/kWh after conversion
    "unit": "cents/kWh",
    "period": "2024-10",   # distinct period — this is a NEW record
}

_RECORD_NAT_GAS = {
    "source": "EIA",
    "region": "TX",
    "fuel_type": "natural_gas",
    "price": 5.0,          # 5.0 $/therm → 50.0 $/MMBtu after conversion
    "unit": "$/therm",
    "period": "2024-11",
}

# Record 3: period "2024-11" for IL electricity — this one is pre-inserted in DB
_RECORD_DUPLICATE = {
    "source": "EIA",
    "region": "IL",
    "fuel_type": "electricity",
    "price": 9.5,
    "unit": "cents/kWh",
    "period": "2024-11",   # matches pre-inserted row → duplicate
}

# Record 4: price == 0 → rejected by validate_record
_RECORD_PRICE_ZERO = {
    "source": "EIA",
    "region": "OH",
    "fuel_type": "electricity",
    "price": 0,
    "unit": "cents/kWh",
    "period": "2024-11",
}

# Record 5: unknown region "ZZ" → rejected by validate_record
_RECORD_BAD_REGION = {
    "source": "EIA",
    "region": "ZZ",
    "fuel_type": "electricity",
    "price": 9.5,
    "unit": "cents/kWh",
    "period": "2024-11",
}


def test_normalize_pipeline_summary(sqlite_session):
    """Core integration test: correct counts across the full pipeline.

    5 records fed in:
      1. _RECORD_EIA_ELEC  — valid, period=2024-10, NEW
      2. _RECORD_NAT_GAS   — valid, NEW
      3. _RECORD_DUPLICATE — valid, period=2024-11, already in DB → duplicate
      4. _RECORD_PRICE_ZERO — price=0 → rejected
      5. _RECORD_BAD_REGION — region "ZZ" → rejected

    Expected: 5 received, 2 rejected, 1 duplicate, 2 inserted, validated=2
    """
    # Pre-insert the row that record 3 will duplicate
    sqlite_session.add(PriceSnapshot(
        source=_RECORD_DUPLICATE["source"],
        region=_RECORD_DUPLICATE["region"],
        fuel_type=_RECORD_DUPLICATE["fuel_type"],
        price=0.095,
        unit="$/kWh",
        period=_RECORD_DUPLICATE["period"],   # "2024-11"
    ))
    sqlite_session.commit()

    raw = [
        _RECORD_EIA_ELEC,    # valid — period=2024-10, new record
        _RECORD_NAT_GAS,     # valid — new record
        _RECORD_DUPLICATE,   # period=2024-11, matches pre-inserted row → duplicate
        _RECORD_PRICE_ZERO,  # price=0 → rejected
        _RECORD_BAD_REGION,  # region ZZ → rejected
    ]

    summary = normalize_pipeline(raw, sqlite_session)

    assert summary["total_received"] == 5
    assert summary["rejected"] == 2
    assert summary["duplicates"] == 1
    assert summary["inserted"] == 2
    assert summary["validated"] == 2
    assert len(summary["rejected_reasons"]) == 2


def test_normalize_pipeline_units_normalized(sqlite_session):
    """All rows in price_snapshots must use canonical units ($/kWh or $/MMBtu only)."""
    sqlite_session.add(PriceSnapshot(
        source=_RECORD_DUPLICATE["source"],
        region=_RECORD_DUPLICATE["region"],
        fuel_type=_RECORD_DUPLICATE["fuel_type"],
        price=0.095,
        unit="$/kWh",
        period=_RECORD_DUPLICATE["period"],   # "2024-11"
    ))
    sqlite_session.commit()

    raw = [_RECORD_EIA_ELEC, _RECORD_NAT_GAS, _RECORD_DUPLICATE,
           _RECORD_PRICE_ZERO, _RECORD_BAD_REGION]
    normalize_pipeline(raw, sqlite_session)

    rows = sqlite_session.query(PriceSnapshot).all()
    raw_units_in_db = {row.unit for row in rows}
    allowed_units = {"$/kWh", "$/MMBtu"}
    assert raw_units_in_db <= allowed_units, (
        f"Found non-normalized units in DB: {raw_units_in_db - allowed_units}"
    )


def test_normalize_pipeline_rejected_in_redis(mock_redis, sqlite_session):
    """Rejected records must appear in Redis under 'rejected_records' key."""
    sqlite_session.add(PriceSnapshot(
        source=_RECORD_DUPLICATE["source"],
        region=_RECORD_DUPLICATE["region"],
        fuel_type=_RECORD_DUPLICATE["fuel_type"],
        price=0.095,
        unit="$/kWh",
        period=_RECORD_DUPLICATE["period"],   # "2024-11"
    ))
    sqlite_session.commit()

    raw = [_RECORD_EIA_ELEC, _RECORD_NAT_GAS, _RECORD_DUPLICATE,
           _RECORD_PRICE_ZERO, _RECORD_BAD_REGION]
    normalize_pipeline(raw, sqlite_session)

    redis_store = mock_redis._store
    assert "rejected_records" in redis_store
    rejected = redis_store["rejected_records"]
    assert len(rejected) == 2  # price=0 and region ZZ

    parsed = [json.loads(entry) for entry in rejected]
    reasons = [p["reason"] for p in parsed]
    # One reason should mention price, the other should mention ZZ
    assert any("0" in r or ">" in r for r in reasons), f"Expected price reason in {reasons}"
    assert any("ZZ" in r for r in reasons), f"Expected region reason in {reasons}"


def test_normalize_pipeline_no_double_insert(sqlite_session):
    """Running the pipeline twice on the same data inserts nothing on second run."""
    raw = [_RECORD_NAT_GAS]

    first = normalize_pipeline(raw, sqlite_session)
    assert first["inserted"] == 1

    second = normalize_pipeline(raw, sqlite_session)
    assert second["inserted"] == 0
    assert second["duplicates"] == 1

    assert sqlite_session.query(PriceSnapshot).filter_by(region="TX").count() == 1


def test_normalize_pipeline_empty_input(sqlite_session):
    summary = normalize_pipeline([], sqlite_session)
    assert summary["total_received"] == 0
    assert summary["inserted"] == 0
    assert summary["rejected"] == 0
