from datetime import date, timedelta

import pytest

from garmin_mcp.shaping import (
    add_pace,
    cap,
    date_range,
    label_units,
    parse_date,
    pick,
    prune,
    summarise_activity,
)


def test_parse_date_keywords():
    today = date.today().isoformat()
    assert parse_date(None) == today
    assert parse_date("") == today
    assert parse_date("today") == today
    assert parse_date("yesterday") == (date.today() - timedelta(days=1)).isoformat()
    assert parse_date("-7d") == (date.today() - timedelta(days=7)).isoformat()
    assert parse_date("2026-03-04") == "2026-03-04"


def test_parse_date_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_date("last tuesday")
    with pytest.raises(ValueError):
        parse_date("2026-13-40")


def test_date_range_defaults_to_a_week_inclusive():
    start, end = date_range(None, "2026-03-10")
    assert (start, end) == ("2026-03-04", "2026-03-10")


def test_date_range_rejects_inverted():
    with pytest.raises(ValueError):
        date_range("2026-03-10", "2026-03-01")


def test_prune_drops_nulls_and_empties():
    assert prune({"a": 1, "b": None, "c": {}, "d": {"e": None}, "f": [1, None]}) == {
        "a": 1,
        "f": [1],
    }


def test_pick_skips_absent_and_null():
    assert pick({"a": 1, "b": None}, "a", "b", "c") == {"a": 1}
    assert pick(None, "a") == {}


def test_cap_marks_oversized_payloads_instead_of_truncating():
    small = {"ok": True}
    assert cap(small) is small
    huge = [{"filler": "x" * 200} for _ in range(1000)]
    capped = cap(huge)
    assert capped["truncated"] is True
    assert "preview" in capped


def test_summarise_activity_flattens_type_key():
    activity = {
        "activityId": 42,
        "activityName": "Morning Run",
        "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
        "distance": 10000.0,
        "duration": 3000.0,
        "averageHR": 148,
        "unrelated": "dropped",
    }
    out = summarise_activity(activity)
    assert out["activityType"] == "running"
    assert out["activityId"] == 42
    assert out["averageHeartRateBpm"] == 148
    assert out["distanceMeters"] == 10000.0
    assert "unrelated" not in out


def test_label_units_renames_and_converts_grams():
    out = label_units({"distance": 5000.0, "averageSpeed": 2.75, "weight": 74500.0})
    assert out == {
        "distanceMeters": 5000.0,
        "averageSpeedMetersPerSecond": 2.75,
        "weightKg": 74.5,
    }


def test_label_units_leaves_unknown_fields_alone():
    assert label_units({"bmi": 22.1, "sourceType": "INDEX_SCALE"}) == {
        "bmi": 22.1,
        "sourceType": "INDEX_SCALE",
    }


def test_add_pace_for_a_run():
    data = {"distanceMeters": 5000.0, "movingDurationSeconds": 1500.0}
    assert add_pace(dict(data), "running")["pace"] == "5:00 min/km"


def test_add_pace_prefers_moving_time_over_elapsed():
    data = {
        "distanceMeters": 5000.0,
        "movingDurationSeconds": 1500.0,
        "durationSeconds": 1800.0,
    }
    assert add_pace(dict(data), "running")["pace"] == "5:00 min/km"


def test_add_pace_skipped_for_cycling_and_missing_data():
    assert "pace" not in add_pace({"distanceMeters": 5000.0, "durationSeconds": 900.0}, "cycling")
    assert "pace" not in add_pace({"distanceMeters": 0, "durationSeconds": 900.0}, "running")
    assert "pace" not in add_pace({"distanceMeters": 5000.0}, "running")
