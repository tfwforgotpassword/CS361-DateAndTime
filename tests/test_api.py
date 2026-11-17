import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from zoneinfo import ZoneInfo

from app.logic import compute_countdown
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


# ---------- Health ----------

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "date-time-tracker"
    assert "X-Process-Time-Ms" in r.headers


# ---------- Current time ----------

def test_current_time_defaults_to_utc():
    r = client.get("/api/v1/datetime/current")
    assert r.status_code == 200
    body = r.json()
    assert body["timezone"] == "UTC"
    assert body["utc_offset"] == "+00:00"
    # Formats promised in the communication contract.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", body["date"])
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", body["time"])


def test_current_time_with_timezone():
    r = client.get("/api/v1/datetime/current",
                   params={"timezone": "America/Los_Angeles"})
    assert r.status_code == 200
    body = r.json()
    assert body["timezone"] == "America/Los_Angeles"
    # Pacific Standard Time or Pacific Daylight Time, depending on the date.
    assert body["utc_offset"] in ("-08:00", "-07:00")
    assert body["iso_datetime"] == f"{body['date']}T{body['time']}{body['utc_offset']}"


def test_current_time_is_accurate():
    # Accuracy requirement: within 1 second of the system clock
    # (2 seconds allowed here because the time is rounded to whole seconds).
    r = client.get("/api/v1/datetime/current", params={"timezone": "Asia/Tokyo"})
    returned = datetime.fromisoformat(r.json()["iso_datetime"])
    assert abs((datetime.now(timezone.utc) - returned).total_seconds()) < 2


def test_current_time_invalid_timezone_returns_400():
    r = client.get("/api/v1/datetime/current", params={"timezone": "Mars/Olympus_Mons"})
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "INVALID_INPUT"
    assert body["field"] == "timezone"


# ---------- Countdown ----------

def test_countdown_breakdown_exact():
    # Logic test with a fixed "now", so the expected answer is exact.
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 16, 17, 0, 0, tzinfo=la)
    target = datetime(2026, 12, 25, tzinfo=la)
    result = compute_countdown(target, now)
    assert result["remaining"] == {"days": 38, "hours": 7, "minutes": 0, "seconds": 0}
    assert result["has_passed"] is False


def test_countdown_across_dst_change():
    # Clocks fall back on 2026-11-01: midnight to 3 AM is 4 real hours.
    la = ZoneInfo("America/Los_Angeles")
    result = compute_countdown(datetime(2026, 11, 1, 3, tzinfo=la),
                               datetime(2026, 11, 1, 0, tzinfo=la))
    assert result["remaining"]["hours"] == 4


def test_countdown_endpoint_future_target():
    r = client.get("/api/v1/datetime/countdown",
                   params={"target": "2999-01-01", "timezone": "America/Los_Angeles"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_passed"] is False
    assert body["total_seconds"] > 0
    rem = body["remaining"]
    assert rem["days"] * 86400 + rem["hours"] * 3600 + rem["minutes"] * 60 + rem["seconds"] \
        == body["total_seconds"]
    assert body["unit"] is None and body["value"] is None


def test_countdown_endpoint_with_unit():
    r = client.get("/api/v1/datetime/countdown",
                   params={"target": "2999-01-01T00:00:00", "unit": "hours"})
    body = r.json()
    assert body["unit"] == "hours"
    assert body["value"] == body["total_seconds"] // 3600


def test_countdown_past_target():
    r = client.get("/api/v1/datetime/countdown", params={"target": "2020-01-01"})
    body = r.json()
    assert r.status_code == 200
    assert body["has_passed"] is True
    assert body["total_seconds"] == 0


def test_countdown_missing_target_returns_422():
    r = client.get("/api/v1/datetime/countdown")
    assert r.status_code == 422
    assert r.json()["field"] == "target"


def test_countdown_invalid_target_returns_400():
    r = client.get("/api/v1/datetime/countdown", params={"target": "next friday"})
    assert r.status_code == 400
    assert r.json()["field"] == "target"


def test_countdown_invalid_unit_returns_422():
    r = client.get("/api/v1/datetime/countdown",
                   params={"target": "2999-01-01", "unit": "weeks"})
    assert r.status_code == 422
    assert r.json()["field"] == "unit"


# ---------- Add ----------

def test_add_simple_days():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-01-15", "duration": {"days": 10}
    })
    assert r.status_code == 200
    assert r.json()["result"] == "2026-01-25"
    assert r.json()["clamped"] is False


def test_add_month_clamping_non_leap():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-01-31", "duration": {"months": 1}
    })
    body = r.json()
    assert r.status_code == 200
    assert body["result"] == "2026-02-28"
    assert body["clamped"] is True


def test_add_month_clamping_leap_year():
    r = client.post("/api/v1/dates/add", json={
        "start": "2024-01-31", "duration": {"months": 1}
    })
    assert r.json()["result"] == "2024-02-29"
    assert r.json()["clamped"] is True


def test_add_year_leap_day():
    r = client.post("/api/v1/dates/add", json={
        "start": "2024-02-29", "duration": {"years": 1}
    })
    assert r.json()["result"] == "2025-02-28"


def test_add_datetime_with_timezone():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-11-01T09:30:00",
        "duration": {"hours": 5, "minutes": 15},
        "time_zone": "America/New_York",
    })
    assert r.json()["result"] == "2026-11-01T14:45:00-05:00"


# ---------- Subtract ----------

def test_subtract_month_clamping():
    r = client.post("/api/v1/dates/subtract", json={
        "start": "2026-03-31", "duration": {"months": 1}
    })
    body = r.json()
    assert body["result"] == "2026-02-28"
    assert body["duration"]["months"] == -1


# ---------- Difference ----------

def test_difference_forward():
    r = client.post("/api/v1/dates/difference", json={
        "start": "2024-02-29", "end": "2026-03-01"
    })
    body = r.json()
    assert r.status_code == 200
    assert body["sign"] == 1
    assert body["calendar"] == {"years": 2, "months": 0, "days": 1}
    assert body["total_days"] == 731
    assert body["total_seconds"] == 731 * 86400


def test_difference_reversed():
    r = client.post("/api/v1/dates/difference", json={
        "start": "2026-03-01", "end": "2024-02-29"
    })
    body = r.json()
    assert body["sign"] == -1
    assert body["total_days"] == 731


def test_difference_same_date():
    r = client.post("/api/v1/dates/difference", json={
        "start": "2026-06-15", "end": "2026-06-15"
    })
    body = r.json()
    assert body["sign"] == 1
    assert body["total_days"] == 0
    assert body["calendar"] == {"years": 0, "months": 0, "days": 0}


@pytest.mark.parametrize("unit, expected", [
    ("days", 731), ("weeks", 104), ("months", 24), ("years", 2),
])
def test_difference_with_unit(unit, expected):
    r = client.post("/api/v1/dates/difference", json={
        "start": "2024-02-29", "end": "2026-03-01", "unit": unit
    })
    body = r.json()
    assert r.status_code == 200
    assert body["unit"] == unit
    assert body["value"] == expected


def test_difference_without_unit_has_null_value():
    r = client.post("/api/v1/dates/difference", json={
        "start": "2024-02-29", "end": "2026-03-01"
    })
    assert r.json()["unit"] is None
    assert r.json()["value"] is None


def test_difference_invalid_unit_returns_422():
    r = client.post("/api/v1/dates/difference", json={
        "start": "2024-02-29", "end": "2026-03-01", "unit": "fortnights"
    })
    assert r.status_code == 422
    assert r.json()["field"] == "unit"


# ---------- Errors ----------

def test_invalid_date_returns_400():
    r = client.post("/api/v1/dates/add", json={
        "start": "not-a-date", "duration": {"days": 1}
    })
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "INVALID_INPUT"
    assert body["field"] == "start"


def test_invalid_timezone_returns_400():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-01-15",
        "duration": {"days": 1},
        "time_zone": "Mars/Olympus_Mons",
    })
    assert r.status_code == 400
    assert r.json()["field"] == "time_zone"


def test_empty_duration_returns_422():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-01-15", "duration": {}
    })
    assert r.status_code == 422
    assert r.json()["error_code"] == "VALIDATION_ERROR"


def test_missing_field_returns_422():
    r = client.post("/api/v1/dates/difference", json={"start": "2026-01-15"})
    assert r.status_code == 422


def test_wrong_type_returns_422():
    r = client.post("/api/v1/dates/add", json={
        "start": "2026-01-15", "duration": {"days": "five"}
    })
    assert r.status_code == 422
