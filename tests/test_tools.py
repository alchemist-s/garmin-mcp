"""Tool tests against a stub client shaped like real Garmin Connect payloads."""

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from garmin_mcp import connection, server


class StubGarmin:
    """Records calls and returns canned payloads."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if isinstance(value, Exception):
                raise value
            return value

        return method


@pytest.fixture
def stub(monkeypatch):
    holder = {}

    def install(responses):
        client = StubGarmin(responses)
        holder["client"] = client
        monkeypatch.setattr(connection, "client", lambda: client)
        return client

    return install


@pytest.mark.anyio
async def test_sleep_summary_extracts_stages_and_score(stub):
    client = stub(
        {
            "get_sleep_data": {
                "dailySleepDTO": {
                    "sleepTimeSeconds": 27000,
                    "deepSleepSeconds": 5400,
                    "lightSleepSeconds": 15000,
                    "remSleepSeconds": 5400,
                    "awakeSleepSeconds": 1200,
                    "averageSpO2Value": 95.0,
                    "sleepScores": {
                        "overall": {"value": 82, "qualifierKey": "GOOD"},
                        "deepPercentage": {"qualifierKey": "FAIR"},
                    },
                },
                "restingHeartRate": 48,
                "sleepLevels": [{"x": i} for i in range(2000)],
            }
        }
    )
    out = await server.garmin_sleep(date="2026-03-04")
    assert out["date"] == "2026-03-04"
    assert out["sleepTimeSeconds"] == 27000
    assert out["sleepScore"] == 82
    assert out["sleepScoreQualifier"] == "GOOD"
    assert out["restingHeartRate"] == 48
    assert out["sleepScoreDetail"]["deepPercentage"] == "FAIR"
    # the per-minute series must not leak into the summary
    assert "sleepLevels" not in out
    assert client.calls[0] == ("get_sleep_data", ("2026-03-04",), {})


@pytest.mark.anyio
async def test_sleep_raw_returns_full_payload(stub):
    stub({"get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 1}, "sleepLevels": [1, 2, 3]}})
    out = await server.garmin_sleep(date="2026-03-04", raw=True)
    assert out["sleepLevels"] == [1, 2, 3]


@pytest.mark.anyio
async def test_heart_rate_summary_averages_the_series(stub):
    stub(
        {
            "get_heart_rates": {
                "restingHeartRate": 50,
                "maxHeartRate": 160,
                "minHeartRate": 45,
                "heartRateValues": [[1, 60], [2, 70], [3, 80], [4, None]],
            }
        }
    )
    out = await server.garmin_heart_rate()
    assert out["sampleCount"] == 3
    assert out["averageHeartRate"] == 70
    assert out["restingHeartRate"] == 50


@pytest.mark.anyio
async def test_heart_rate_handles_empty_day(stub):
    stub({"get_heart_rates": {"heartRateValues": None}})
    out = await server.garmin_heart_rate()
    assert out["sampleCount"] == 0
    assert "averageHeartRate" not in out


@pytest.mark.anyio
async def test_activities_summarised_and_paged(stub):
    client = stub(
        {
            "get_activities": [
                {
                    "activityId": 1,
                    "activityName": "Run",
                    "activityType": {"typeKey": "running"},
                    "distance": 5000,
                }
            ]
        }
    )
    out = await server.garmin_activities(limit=5, offset=10, activity_type="running")
    assert out == [
        {"activityId": 1, "activityName": "Run", "distance": 5000, "activityType": "running"}
    ]
    assert client.calls[0] == ("get_activities", (10, 5, "running"), {})


@pytest.mark.anyio
async def test_activities_accepts_wrapped_list_shape(stub):
    stub({"get_activities": {"activityList": [{"activityId": 7}]}})
    out = await server.garmin_activities()
    assert out[0]["activityId"] == 7


@pytest.mark.anyio
async def test_training_readiness_normalises_single_object(stub):
    stub({"get_training_readiness": {"score": 71, "level": "READY", "sleepScore": 80}})
    out = await server.garmin_training_readiness()
    assert out[0]["score"] == 71
    assert out[0]["level"] == "READY"


@pytest.mark.anyio
async def test_training_status_pulls_first_device_entry(stub):
    stub(
        {
            "get_training_status": {
                "mostRecentTrainingStatus": {
                    "latestTrainingStatusData": {
                        "3299238123": {
                            "trainingStatus": 3,
                            "trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
                            "weeklyTrainingLoad": 420,
                        }
                    }
                },
                "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 52.3, "fitnessAge": 31}},
            }
        }
    )
    out = await server.garmin_training_status(date="2026-03-04")
    assert out["trainingStatusFeedbackPhrase"] == "PRODUCTIVE_1"
    assert out["weeklyTrainingLoad"] == 420
    assert out["vo2Max"]["vo2MaxPreciseValue"] == 52.3


@pytest.mark.anyio
async def test_weight_flattens_daily_summaries(stub):
    stub(
        {
            "get_weigh_ins": {
                "dailyWeightSummaries": [
                    {
                        "summaryDate": "2026-03-04",
                        "latestWeight": {"weight": 74500.0, "bmi": 22.1, "bodyFat": None},
                    }
                ]
            }
        }
    )
    out = await server.garmin_weight(start="2026-03-01", end="2026-03-04")
    assert out == [{"date": "2026-03-04", "weight": 74500.0, "bmi": 22.1}]


@pytest.mark.anyio
async def test_steps_range_versus_intraday(stub):
    client = stub(
        {
            "get_daily_steps": [{"calendarDate": "2026-03-04", "totalSteps": 9000, "stepGoal": 8000}],
            "get_steps_data": [{"startGMT": "...", "steps": 120}],
        }
    )
    ranged = await server.garmin_steps(start="2026-03-01", end="2026-03-04")
    assert ranged[0]["totalSteps"] == 9000
    assert client.calls[0][0] == "get_daily_steps"

    intraday = await server.garmin_steps(intraday_date="2026-03-04")
    assert intraday["date"] == "2026-03-04"
    assert client.calls[1][0] == "get_steps_data"


@pytest.mark.anyio
async def test_hrv_missing_data_explains_itself(stub):
    stub({"get_hrv_data": None})
    out = await server.garmin_hrv(date="2026-03-04")
    assert "No HRV data" in out["note"]


@pytest.mark.anyio
async def test_api_get_requires_absolute_path(stub):
    stub({"connectapi": {"ok": True}})
    with pytest.raises(ToolError, match="must start with"):
        await server.garmin_api_get("usersummary-service/foo")
    assert await server.garmin_api_get("/usersummary-service/foo") == {"ok": True}


@pytest.mark.anyio
async def test_invalid_date_is_rejected_before_any_network_call(stub):
    client = stub({"get_sleep_data": {}})
    with pytest.raises(ToolError, match="Unrecognised date"):
        await server.garmin_sleep(date="not a date")
    assert client.calls == []


@pytest.mark.anyio
async def test_call_tool_round_trip_validates_schema(stub):
    """Exercise the real MCP dispatch path, not just the bare function."""
    stub({"get_activities": [{"activityId": 99, "activityType": {"typeKey": "cycling"}}]})
    result = await server.mcp.call_tool("garmin_activities", {"limit": 1})
    payload = result[1] if isinstance(result, tuple) else result
    assert "99" in str(payload)


@pytest.mark.anyio
async def test_unknown_tool_is_rejected():
    with pytest.raises(Exception):
        await server.mcp.call_tool("garmin_not_a_tool", {})


@pytest.mark.anyio
async def test_missing_tokens_tell_the_caller_how_to_fix_it(monkeypatch):
    """The SDK only forwards ToolError text, so this message must be a ToolError."""

    def unauthenticated():
        raise connection.NotLoggedIn("No Garmin tokens at /tmp/x. Run `garmin-mcp login`.")

    monkeypatch.setattr(connection, "client", unauthenticated)
    with pytest.raises(ToolError, match="garmin-mcp login"):
        await server.garmin_whoami()
