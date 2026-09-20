"""The briefing fuses six sections and must survive any one of them failing."""

import pytest

from garmin_mcp import server


@pytest.mark.anyio
async def test_briefing_gathers_every_section(stub):
    stub(
        {
            "get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 27000}},
            "get_hrv_data": {"hrvSummary": {"lastNightAvg": 43, "status": "BALANCED"}},
            "get_all_day_stress": {"avgStressLevel": 18},
            "get_training_readiness": {"score": 71, "level": "READY"},
            "get_user_summary": {"totalSteps": 9412, "restingHeartRate": 62},
            "get_body_battery": [{"date": "2026-03-04", "charged": 62}],
        }
    )
    out = await server.garmin_briefing(date="2026-03-04")

    assert out["date"] == "2026-03-04"
    assert out["sleep"]["sleepTimeSeconds"] == 27000
    assert out["hrv"]["lastNightAvg"] == 43
    assert out["stress"]["avgStressLevel"] == 18
    assert out["trainingReadiness"][0]["score"] == 71
    assert out["dailySummary"]["totalSteps"] == 9412
    assert out["bodyBattery"][0]["charged"] == 62
    assert "sectionsUnavailable" not in out


@pytest.mark.anyio
async def test_one_failing_section_does_not_sink_the_briefing(stub):
    stub(
        {
            "get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 27000}},
            "get_hrv_data": RuntimeError("garmin 500"),
            "get_all_day_stress": {"avgStressLevel": 18},
            "get_training_readiness": [],
            "get_user_summary": {"totalSteps": 9412},
            "get_body_battery": [],
        }
    )
    out = await server.garmin_briefing(date="2026-03-04")

    assert out["sleep"]["sleepTimeSeconds"] == 27000
    assert out["stress"]["avgStressLevel"] == 18
    assert "hrv" not in out
    assert "hrv" in out["sectionsUnavailable"]


@pytest.mark.anyio
async def test_briefing_rejects_a_bad_date_before_calling_garmin(stub):
    from mcp.server.mcpserver.exceptions import ToolError

    client = stub({})
    with pytest.raises(ToolError, match="Unrecognised date"):
        await server.garmin_briefing(date="whenever")
    assert client.calls == []


@pytest.mark.anyio
async def test_sections_with_no_reading_are_named_not_dropped(stub):
    """An unsupported metric is a fact about the watch, not a missing section."""
    stub(
        {
            "get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 27000}},
            "get_hrv_data": None,  # device records no HRV -> note only
            "get_all_day_stress": {},  # -> date only
            "get_training_readiness": [],  # Venu 3 returns an empty list
            "get_user_summary": {"totalSteps": 9412},
            "get_body_battery": [],
        }
    )
    out = await server.garmin_briefing(date="2026-03-04")

    assert out["sleep"]["sleepTimeSeconds"] == 27000
    assert set(out["sectionsNoData"]) == {
        "hrv",
        "stress",
        "trainingReadiness",
        "bodyBattery",
    }
    assert "sectionsUnavailable" not in out
