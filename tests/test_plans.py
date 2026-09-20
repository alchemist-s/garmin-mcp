"""Plan tools: history, export, and creating or scheduling workouts."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from zonetwo import server


@pytest.mark.anyio
async def test_training_history_buckets_activities_into_weeks(stub):
    stub({"get_activities_by_date": [
        {"startTimeLocal": "2026-03-02 07:00:00", "distance": 10000, "movingDuration": 3000},
        {"startTimeLocal": "2026-03-04 07:00:00", "distance": 5000, "movingDuration": 1500},
        {"startTimeLocal": "2026-03-09 07:00:00", "distance": 21000, "movingDuration": 7000},
    ]})
    out = await server.garmin_training_history(weeks=4)

    weeks = {w["weekStarting"]: w for w in out["weeks"]}
    assert weeks["2026-03-02"]["sessions"] == 2
    assert weeks["2026-03-02"]["distanceKm"] == 15.0
    assert weeks["2026-03-02"]["longestRunKm"] == 10.0
    assert weeks["2026-03-09"]["distanceKm"] == 21.0
    assert out["totals"]["distanceKm"] == 36.0
    assert out["totals"]["longestRunKm"] == 21.0


@pytest.mark.anyio
async def test_training_history_computes_pace_per_week(stub):
    stub({"get_activities_by_date": [
        {"startTimeLocal": "2026-03-02 07:00:00", "distance": 10000, "movingDuration": 3000},
    ]})
    out = await server.garmin_training_history(weeks=2)
    assert out["weeks"][0]["averagePace"] == "5:00 min/km"  # 3000s / 10km


@pytest.mark.anyio
async def test_training_history_rejects_silly_ranges(stub):
    stub({})
    with pytest.raises(ToolError, match="between 1 and 104"):
        await server.garmin_training_history(weeks=500)


@pytest.mark.anyio
async def test_export_produces_csv_with_a_header(stub):
    stub({"get_activities_by_date": [
        {"startTimeLocal": "2026-03-02 07:00:00", "activityName": "Morning Run",
         "activityType": {"typeKey": "running"}, "distance": 10000,
         "movingDuration": 3000, "averageHR": 148},
    ]})
    csv = await server.garmin_export_activities(start="2026-03-01", end="2026-03-31")
    lines = csv.splitlines()
    assert lines[0].startswith("date,name,type,distance_km")
    assert "2026-03-02" in lines[1]
    assert "10.00" in lines[1]
    assert "148" in lines[1]


@pytest.mark.anyio
async def test_export_quotes_names_containing_commas(stub):
    stub({"get_activities_by_date": [
        {"startTimeLocal": "2026-03-02 07:00:00", "activityName": "Long, slow run",
         "activityType": {"typeKey": "running"}, "distance": 1000},
    ]})
    csv = await server.garmin_export_activities()
    assert '"Long, slow run"' in csv


@pytest.mark.anyio
async def test_create_workout_uploads_and_can_schedule(stub):
    client = stub({
        "upload_running_workout": {"workoutId": 42},
        "schedule_workout": {"ok": True},
    })
    out = await server.garmin_create_workout(
        name="Intervals",
        steps=[
            {"kind": "warmup", "length": "10min"},
            {"repeat": 6, "steps": [
                {"kind": "interval", "length": "800m", "pace": "4:30-4:20"},
                {"kind": "recovery", "length": "90s"},
            ]},
        ],
        schedule_date="2026-03-04",
    )
    assert out["workoutId"] == 42
    assert out["scheduledFor"] == "2026-03-04"
    assert out["status"] == "created and scheduled"
    assert [c[0] for c in client.calls] == ["upload_running_workout", "schedule_workout"]


@pytest.mark.anyio
async def test_create_workout_without_a_date_does_not_schedule(stub):
    client = stub({"upload_running_workout": {"workoutId": 7}})
    out = await server.garmin_create_workout(name="Easy", steps=[{"kind": "run", "length": "5km"}])
    assert out["status"] == "created"
    assert "scheduledFor" not in out
    assert [c[0] for c in client.calls] == ["upload_running_workout"]


@pytest.mark.anyio
async def test_a_bad_step_is_rejected_before_anything_is_uploaded(stub):
    client = stub({"upload_running_workout": {"workoutId": 1}})
    with pytest.raises(ToolError, match="Unknown step kind"):
        await server.garmin_create_workout(name="Bad", steps=[{"kind": "sprint", "length": "400m"}])
    assert client.calls == []


@pytest.mark.anyio
async def test_deleting_a_workout_is_allowed_but_activities_have_no_delete_tool():
    """Plans are drafts; recorded history is not deletable by design."""
    import asyncio

    names = {t.name for t in await server.mcp.list_tools()}
    assert "garmin_delete_workout" in names
    assert not any("delete" in n and "workout" not in n for n in names)
