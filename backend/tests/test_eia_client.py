"""Tests for EIAClient and ingest_eia_data."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.price_snapshot import PriceSnapshot
from app.services.data_ingestion import ingest_eia_data
from app.services.eia_client import EIAClient

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ELECTRICITY_RESPONSE = {
    "response": {
        "data": [
            {
                "period": "2024-01",
                "stateid": "IL",
                "stateDescription": "Illinois",
                "sectorid": "RES",
                "price": "13.52",
                "price-units": "cents per kilowatt-hour",
            },
            {
                "period": "2023-12",
                "stateid": "IL",
                "stateDescription": "Illinois",
                "sectorid": "RES",
                "price": "14.10",
                "price-units": "cents per kilowatt-hour",
            },
        ]
    }
}

_NATURAL_GAS_RESPONSE = {
    "response": {
        "data": [
            {
                "period": "2024-01",
                "duoarea": "SIL",
                "area-name": "Illinois",
                "value": "10.82",
                "units": "$/MCF",
            }
        ]
    }
}


def _make_mock_http_client(json_payload: dict) -> MagicMock:
    """Return a mock httpx.Client whose .get() always returns *json_payload*."""
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = json_payload

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_response
    return mock_client


@pytest.fixture()
def sqlite_session():
    """Provide a fresh in-memory SQLite session for each test.

    SQLite doesn't support JSONB or native UUIDs, but SQLAlchemy maps them
    to JSON (TEXT) and CHAR(32) respectively, which is fine for unit tests.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Test 1 – electricity endpoint returns correctly normalised dicts
# ---------------------------------------------------------------------------


def test_electricity_returns_normalised_dicts():
    mock_client = _make_mock_http_client(_ELECTRICITY_RESPONSE)
    client = EIAClient(http_client=mock_client)

    result = client.get_electricity_retail_prices("IL")

    assert len(result) == 2

    first = result[0]
    assert set(first.keys()) == {"period", "state", "price", "units"}
    assert first["period"] == "2024-01"
    assert first["state"] == "IL"
    assert isinstance(first["price"], float)
    assert first["price"] == pytest.approx(13.52)
    assert first["units"] == "cents per kilowatt-hour"


def test_natural_gas_returns_normalised_dicts():
    mock_client = _make_mock_http_client(_NATURAL_GAS_RESPONSE)
    client = EIAClient(http_client=mock_client)

    result = client.get_natural_gas_prices("IL")

    assert len(result) == 1
    first = result[0]
    assert set(first.keys()) == {"period", "state", "price", "units"}
    assert first["state"] == "IL"  # stripped "S" prefix from "SIL"
    assert first["price"] == pytest.approx(10.82)
    assert first["units"] == "$/MCF"


def test_none_price_coerced_gracefully():
    """Records with missing/null price should have price=None, not crash."""
    payload = {
        "response": {
            "data": [{"period": "2024-01", "stateid": "IL", "price": None, "price-units": ""}]
        }
    }
    mock_client = _make_mock_http_client(payload)
    client = EIAClient(http_client=mock_client)
    result = client.get_electricity_retail_prices("IL")
    assert result[0]["price"] is None


# ---------------------------------------------------------------------------
# Test 2 – failed HTTP call retries exactly 3 times before raising
# ---------------------------------------------------------------------------


def test_retries_three_times_on_http_error():
    error_response = MagicMock()
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500 Internal Server Error",
        request=MagicMock(),
        response=MagicMock(),
    )

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = error_response

    client = EIAClient(http_client=mock_client, max_retries=3)

    with patch("app.services.eia_client.time.sleep") as mock_sleep:
        with pytest.raises(httpx.HTTPStatusError):
            client.get_electricity_retail_prices("IL")

    # get() called once per attempt
    assert mock_client.get.call_count == 3

    # Exponential back-off: sleep(1) then sleep(2)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(1)
    mock_sleep.assert_any_call(2)


def test_retries_succeed_on_third_attempt():
    """Simulate two failures then a success."""
    good_response = MagicMock()
    good_response.raise_for_status.return_value = None
    good_response.json.return_value = {"response": {"data": []}}

    bad_response = MagicMock()
    bad_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=MagicMock()
    )

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = [bad_response, bad_response, good_response]

    client = EIAClient(http_client=mock_client, max_retries=3)

    with patch("app.services.eia_client.time.sleep"):
        result = client.get_electricity_retail_prices("IL")

    assert result == []
    assert mock_client.get.call_count == 3


# ---------------------------------------------------------------------------
# Test 3 – deduplication: calling ingest twice doesn't double-insert
# ---------------------------------------------------------------------------

# Records use the EIAClient output format (state/units) which data_ingestion
# maps to the PriceSnapshot schema (region/unit).
_INGEST_RECORDS = [
    {
        "period": "2024-01",
        "state": "IL",
        "price": 13.52,
        "units": "cents per kilowatt-hour",
        "fuel_type": "electricity",
    },
    {
        "period": "2024-01",
        "state": "IL",
        "price": 10.82,
        "units": "$/MCF",
        "fuel_type": "natural_gas",
    },
]


def test_ingest_inserts_new_records(sqlite_session):
    mock_eia = MagicMock(spec=EIAClient)
    mock_eia.fetch_all_states.return_value = _INGEST_RECORDS

    count = ingest_eia_data(sqlite_session, client=mock_eia)

    assert count == 2
    assert sqlite_session.query(PriceSnapshot).count() == 2


def test_ingest_deduplication_no_double_insert(sqlite_session):
    mock_eia = MagicMock(spec=EIAClient)
    mock_eia.fetch_all_states.return_value = _INGEST_RECORDS

    first_run = ingest_eia_data(sqlite_session, client=mock_eia)
    second_run = ingest_eia_data(sqlite_session, client=mock_eia)

    assert first_run == 2
    assert second_run == 0  # nothing new to insert
    assert sqlite_session.query(PriceSnapshot).count() == 2


def test_ingest_skips_incomplete_records(sqlite_session):
    """Records missing period/state/fuel_type should be silently skipped."""
    incomplete = [{"period": "2024-01", "price": 5.0}]  # no state or fuel_type

    mock_eia = MagicMock(spec=EIAClient)
    mock_eia.fetch_all_states.return_value = incomplete

    count = ingest_eia_data(sqlite_session, client=mock_eia)

    assert count == 0
    assert sqlite_session.query(PriceSnapshot).count() == 0
