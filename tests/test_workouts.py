"""Translating plain steps into Garmin's nested workout format."""

import pytest

from zonetwo.workouts import build_running_workout, pace_to_speed, parse_length


@pytest.mark.parametrize(
    "text,expected",
    [
        ("800m", ("distance", 800.0)),
        ("5km", ("distance", 5000.0)),
        ("1k", ("distance", 1000.0)),
        ("3mi", ("distance", 4828.032)),
        ("10min", ("time", 600.0)),
        ("90s", ("time", 90.0)),
        ("1h", ("time", 3600.0)),
    ],
)
def test_parse_length(text, expected):
    unit, amount = parse_length(text)
    assert unit == expected[0]
    assert amount == pytest.approx(expected[1])


def test_minutes_are_not_read_as_metres():
    """The ambiguity that would silently turn a 10 minute warmup into 10 metres."""
    assert parse_length("10min") == ("time", 600.0)
    assert parse_length("10m") == ("distance", 10.0)


def test_parse_length_rejects_nonsense():
    with pytest.raises(ValueError, match="Could not read a length"):
        parse_length("a bit")


def test_pace_converts_to_metres_per_second():
    assert pace_to_speed("5:00") == pytest.approx(3.3333, abs=1e-4)
    assert pace_to_speed("4:00") == pytest.approx(4.1667, abs=1e-4)
    with pytest.raises(ValueError, match="Could not read a pace"):
        pace_to_speed("fast")


def _steps(workout):
    return workout.to_dict()["workoutSegments"][0]["workoutSteps"]


def test_simple_workout_orders_and_types_its_steps():
    w = build_running_workout("Easy", [
        {"kind": "warmup", "length": "10min"},
        {"kind": "run", "length": "5km"},
        {"kind": "cooldown", "length": "5min"},
    ])
    steps = _steps(w)
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "interval", "cooldown"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 3]
    assert steps[1]["endCondition"]["conditionTypeKey"] == "distance"
    assert steps[1]["endConditionValue"] == 5000.0


def test_repeat_group_nests_its_children():
    w = build_running_workout("Intervals", [
        {"kind": "warmup", "length": "10min"},
        {"repeat": 6, "steps": [
            {"kind": "interval", "length": "800m", "pace": "4:30-4:20"},
            {"kind": "recovery", "length": "90s"},
        ]},
    ])
    steps = _steps(w)
    group = steps[1]
    assert group["stepType"]["stepTypeKey"] == "repeat"
    assert group["numberOfIterations"] == 6
    assert [c["stepType"]["stepTypeKey"] for c in group["workoutSteps"]] == ["interval", "recovery"]


def test_pace_range_becomes_a_speed_band_in_metres_per_second():
    w = build_running_workout("Tempo", [{"kind": "run", "length": "5km", "pace": "4:30-4:20"}])
    step = _steps(w)[0]
    assert step["targetType"]["workoutTargetTypeKey"] == "speed.zone"
    assert step["targetValueOne"] == pytest.approx(3.7037, abs=1e-3)  # 4:30/km, slower
    assert step["targetValueTwo"] == pytest.approx(3.8462, abs=1e-3)  # 4:20/km, faster


def test_a_single_pace_becomes_a_narrow_band():
    """Garmin needs a range; a bare pace would otherwise be unachievable."""
    w = build_running_workout("Steady", [{"kind": "run", "length": "5km", "pace": "5:00"}])
    step = _steps(w)[0]
    assert step["targetValueOne"] < step["targetValueTwo"]


def test_heart_rate_zone_target():
    w = build_running_workout("Easy", [{"kind": "run", "length": "40min", "heart_rate_zone": 2}])
    step = _steps(w)[0]
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert step["targetValueOne"] == 2


def test_unknown_kind_is_rejected_with_the_valid_list():
    with pytest.raises(ValueError, match="Unknown step kind"):
        build_running_workout("Bad", [{"kind": "sprint", "length": "400m"}])


def test_missing_length_names_the_offending_step():
    with pytest.raises(ValueError, match="needs a 'length'"):
        build_running_workout("Bad", [{"kind": "warmup"}])


def test_repeat_without_nested_steps_explains_the_shape():
    with pytest.raises(ValueError, match="nested 'steps' list"):
        build_running_workout("Bad", [{"repeat": 4, "kind": "interval", "length": "400m"}])


def test_empty_workout_is_rejected():
    with pytest.raises(ValueError, match="at least one step"):
        build_running_workout("Nothing", [])


def test_duration_estimate_accounts_for_repeats():
    w = build_running_workout("Intervals", [
        {"kind": "warmup", "length": "10min"},
        {"repeat": 4, "steps": [{"kind": "interval", "length": "60s"}, {"kind": "recovery", "length": "60s"}]},
    ])
    # 10 min warmup + 4 x (60s + 60s) = 18 minutes
    assert w.estimatedDurationInSecs == pytest.approx(18 * 60, abs=5)
