"""The `garmin-mcp check` command must report, never crash."""

import pytest

from zonetwo import cli, connection


@pytest.mark.anyio
async def test_check_reports_ok_empty_and_failed(stub, capsys):
    responses = {
        "get_user_profile": {"displayName": "al", "fullName": "Al Tester"},
        "get_userprofile_settings": {"measurementSystem": "metric"},
        "get_devices": [{"displayName": "fenix", "deviceId": 1}],
        "get_user_summary": {"totalSteps": 9000},
        "get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 27000}},
        "get_heart_rates": {"restingHeartRate": 48, "heartRateValues": [[1, 60]]},
        "get_hrv_data": None,  # unsupported device -> "none", not a failure
        "get_all_day_stress": {"avgStressLevel": 30},
        "get_body_battery": [{"date": "2026-03-04", "charged": 60}],
        "get_daily_steps": [{"calendarDate": "2026-03-04", "totalSteps": 9000}],
        "get_spo2_data": {"averageSpO2": 95},
        "get_respiration_data": {"avgWakingRespirationValue": 14},
        "get_intensity_minutes_data": {"moderateValue": 30},
        "get_training_readiness": {"score": 71},
        "get_training_status": {"mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 52.0}}},
        "get_max_metrics": {"generic": {"vo2MaxPreciseValue": 52.0}},
        "get_race_predictions": {"time5K": 1200},
        "get_personal_record": [{"activityId": 5, "value": 1200}],
        "get_activities": [{"activityId": 11, "activityName": "Run"}],
        "get_activities_by_date": [{"activityId": 11}],
        "get_last_activity": {"activityId": 11, "activityName": "Run"},
        "get_weigh_ins": {"dailyWeightSummaries": []},
        "get_activity": {"activityId": 11, "summaryDTO": {"distance": 5000}},
        "get_activity_splits": {"lapDTOs": [{"lapIndex": 1, "distance": 1000}]},
        "get_activity_weather": RuntimeError("garmin 500"),  # one genuine failure
    }
    stub(responses)

    exit_code = await cli._check_tools(None)
    out = capsys.readouterr().out

    assert "garmin_whoami" in out and "Al Tester" in out
    assert "garmin_hrv" in out and "no data" in out
    assert "garmin_activity_splits" in out  # reached via the discovered activity id
    assert "garmin_activity_weather" in out and "FAIL" in out
    assert exit_code == 1  # one failure
    assert "1 failed" in out


@pytest.mark.anyio
async def test_check_surfaces_login_failure_per_tool(monkeypatch, capsys):
    def unauthenticated():
        raise connection.NotLoggedIn("Run `garmin-mcp login`.")

    monkeypatch.setattr(connection, "client", unauthenticated)
    exit_code = await cli._check_tools(None)
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "garmin-mcp login" in out


@pytest.mark.anyio
async def test_check_counts_date_only_and_note_only_as_no_data(stub, capsys):
    """A response carrying only an echoed date says nothing about the account."""
    stub(
        {
            "get_user_profile": {"fullName": "Al Tester"},
            "get_all_day_stress": {},  # -> {"date": ...} only
            "get_hrv_data": None,  # -> note only
            "get_last_activity": {"activityId": 11},
            "get_activity": {"activityId": 11, "summaryDTO": {"distance": 10000}},
            "get_activity_splits": {"lapDTOs": []},
        }
    )
    exit_code = await cli._check_tools(None)
    out = capsys.readouterr().out
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("  garmin_")}
    assert "none" in lines["garmin_stress"]
    assert "none" in lines["garmin_hrv"]
    assert "none" in lines["garmin_activity_splits"]
    assert "ok" in lines["garmin_activity"]
    assert exit_code == 0  # no data is not a failure
