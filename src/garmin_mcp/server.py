"""MCP server exposing Garmin Connect data.

Read-only unless ``GARMIN_MCP_ENABLE_WRITES=1`` is set, because these tools act
on a real health record that syncs back to the watch.
"""

from __future__ import annotations

import os
from functools import partial, wraps
from importlib.metadata import version
from typing import Any

import anyio
import anyio.to_thread
from garminconnect import (
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import connection
from .shaping import (
    add_pace,
    cap,
    date_range,
    is_thin,
    label_units,
    parse_date,
    pick,
    prune,
    summarise_activity,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES_ENABLED = os.getenv("GARMIN_MCP_ENABLE_WRITES", "").lower() in {"1", "true", "yes"}

mcp = MCPServer(
    name="garmin",
    version=version("garmin-mcp"),
    instructions=(
        "Read health, sleep, training and activity data from the signed-in user's "
        "Garmin Connect account. Dates accept YYYY-MM-DD, 'today', 'yesterday' or "
        "'-7d'; omitting a date means today. Tools return a compact summary — pass "
        "raw=true only when a specific field is missing from the summary, as raw "
        "payloads are very large. Garmin syncs when the watch last connected to a "
        "phone, so today's figures may be partial."
    ),
)


def explain_failures(fn):
    """Translate expected failures into ``ToolError``.

    The SDK strips the text of any other exception before it reaches the model,
    so an untranslated "you are not logged in" arrives as "Error executing
    tool" — precisely the case where the caller needs to be told what to do.
    """

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (
            connection.NotLoggedIn,
            connection.MFARequired,
            connection.NoPendingLogin,
            ValueError,
        ) as exc:
            raise ToolError(str(exc)) from exc
        except GarminConnectTooManyRequestsError as exc:
            raise ToolError(
                "Garmin is rate-limiting this account. Wait a few minutes before retrying."
            ) from exc
        except GarminConnectConnectionError as exc:
            raise ToolError(f"Could not reach Garmin Connect: {exc}") from exc

    return wrapper


def tool(fn=None, /, **kwargs):
    """Register a read-only tool with failure translation applied."""

    def register(target):
        return mcp.tool(annotations=READ_ONLY, **kwargs)(explain_failures(target))

    return register(fn) if fn else register


def write_tool(fn):
    """Register a mutating tool; same translation, different annotations."""
    return mcp.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False)
    )(explain_failures(fn))


# --- profile and devices -------------------------------------------------


@tool
async def garmin_whoami() -> dict[str, Any]:
    """Identify the signed-in Garmin account and its unit preferences."""
    profile = await connection.call("get_user_profile")
    settings = await connection.call("get_userprofile_settings")
    return prune(
        {
            **pick(profile, "displayName", "fullName", "userName", "location"),
            **pick(
                settings,
                "measurementSystem",
                "timeFormat",
                "dateFormat",
                "firstDayOfWeek",
                "handedness",
            ),
        }
    )


@tool
async def garmin_devices() -> list[dict[str, Any]]:
    """List the Garmin devices registered to the account, newest sync first."""
    devices = await connection.call("get_devices")
    return [
        prune(
            pick(
                d,
                "displayName",
                "productDisplayName",
                "deviceId",
                "serialNumber",
                "lastUsedDeviceUploadTime",
                "softwareVersion",
            )
        )
        for d in devices or []
    ]


# --- daily wellness ------------------------------------------------------


@tool
async def garmin_daily_summary(date: str | None = None) -> dict[str, Any]:
    """Whole-day wellness rollup: steps, calories, distance, floors, stress, body battery."""
    cdate = parse_date(date)
    summary = await connection.call("get_user_summary", cdate)
    return prune(
        {
            "date": cdate,
            **pick(
                summary,
                "totalSteps",
                "dailyStepGoal",
                "totalDistanceMeters",
                "totalKilocalories",
                "activeKilocalories",
                "bmrKilocalories",
                "floorsAscended",
                "floorsAscendedInMeters",
                "userFloorsAscendedGoal",
                "minHeartRate",
                "maxHeartRate",
                "restingHeartRate",
                "averageStressLevel",
                "maxStressLevel",
                "stressDuration",
                "restStressDuration",
                "activityStressDuration",
                "lowStressDuration",
                "mediumStressDuration",
                "highStressDuration",
                "bodyBatteryChargedValue",
                "bodyBatteryDrainedValue",
                "bodyBatteryHighestValue",
                "bodyBatteryLowestValue",
                "bodyBatteryMostRecentValue",
                "moderateIntensityMinutes",
                "vigorousIntensityMinutes",
                "intensityMinutesGoal",
                "sleepingSeconds",
                "measurableAwakeDuration",
                "measurableAsleepDuration",
            ),
        }
    )


@tool
async def garmin_sleep(date: str | None = None, raw: bool = False) -> dict[str, Any]:
    """Sleep for the night ending on the given date: stages, score and overnight vitals."""
    cdate = parse_date(date)
    data = await connection.call("get_sleep_data", cdate)
    if raw:
        return cap(prune(data))

    dto = (data or {}).get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    result = {
        "date": cdate,
        **pick(
            dto,
            "sleepTimeSeconds",
            "napTimeSeconds",
            "deepSleepSeconds",
            "lightSleepSeconds",
            "remSleepSeconds",
            "awakeSleepSeconds",
            "sleepStartTimestampLocal",
            "sleepEndTimestampLocal",
            "averageSpO2Value",
            "lowestSpO2Value",
            "averageRespirationValue",
            "avgSleepStress",
        ),
        **pick(data, "restingHeartRate", "avgOvernightHrv", "bodyBatteryChange"),
    }
    overall = scores.get("overall") or {}
    if overall:
        result["sleepScore"] = overall.get("value")
        result["sleepScoreQualifier"] = overall.get("qualifierKey")
    for stage in ("totalDuration", "stress", "awakeCount", "remPercentage", "deepPercentage"):
        qualifier = (scores.get(stage) or {}).get("qualifierKey")
        if qualifier:
            result.setdefault("sleepScoreDetail", {})[stage] = qualifier
    return prune(result)


@tool
async def garmin_heart_rate(date: str | None = None, raw: bool = False) -> dict[str, Any]:
    """Heart rate for a day. Summary gives min/max/resting; raw adds the 2-minute series."""
    cdate = parse_date(date)
    data = await connection.call("get_heart_rates", cdate)
    if raw:
        return cap(prune(data))
    values = (data or {}).get("heartRateValues") or []
    readings = [v[1] for v in values if isinstance(v, list) and len(v) > 1 and v[1] is not None]
    return prune(
        {
            "date": cdate,
            **pick(data, "restingHeartRate", "minHeartRate", "maxHeartRate", "lastSevenDaysAvgRestingHeartRate"),
            "sampleCount": len(readings),
            "averageHeartRate": round(sum(readings) / len(readings)) if readings else None,
        }
    )


@tool
async def garmin_hrv(date: str | None = None) -> dict[str, Any]:
    """Overnight heart rate variability: last-night average, baseline and status."""
    cdate = parse_date(date)
    data = await connection.call("get_hrv_data", cdate)
    if not data:
        return {"date": cdate, "note": "No HRV data. Requires a supported device worn overnight."}
    return prune(
        {
            "date": cdate,
            **pick(
                data.get("hrvSummary") or {},
                "weeklyAvg",
                "lastNightAvg",
                "lastNight5MinHigh",
                "status",
                "feedbackPhrase",
            ),
            "baseline": pick(
                (data.get("hrvSummary") or {}).get("baseline") or {},
                "lowUpper",
                "balancedLow",
                "balancedUpper",
                "markerValue",
            ),
        }
    )


@tool
async def garmin_stress(date: str | None = None) -> dict[str, Any]:
    """All-day stress: average, max and time spent in each stress band."""
    cdate = parse_date(date)
    data = await connection.call("get_all_day_stress", cdate)
    return prune(
        {
            "date": cdate,
            **pick(
                data,
                "avgStressLevel",
                "maxStressLevel",
                "stressChartValueOffset",
                "restStressDuration",
                "lowStressDuration",
                "mediumStressDuration",
                "highStressDuration",
                "activityStressDuration",
                "uncategorizedStressDuration",
            ),
        }
    )


@tool
async def garmin_body_battery(
    start: str | None = None, end: str | None = None
) -> list[dict[str, Any]]:
    """Body Battery charge and drain per day over a date range (default: last 7 days)."""
    first, last = date_range(start, end)
    data = await connection.call("get_body_battery", first, last)
    return [
        prune(
            pick(
                day,
                "date",
                "charged",
                "drained",
                "startTimestampLocal",
                "endTimestampLocal",
                "bodyBatteryDynamicFeedbackEvent",
            )
        )
        for day in data or []
    ]


@tool
async def garmin_steps(
    start: str | None = None, end: str | None = None, intraday_date: str | None = None
) -> Any:
    """Daily step totals over a range, or 15-minute buckets for one day via intraday_date."""
    if intraday_date:
        cdate = parse_date(intraday_date)
        buckets = await connection.call("get_steps_data", cdate)
        return cap({"date": cdate, "buckets": prune(buckets)})
    first, last = date_range(start, end)
    days = await connection.call("get_daily_steps", first, last)
    return [
        prune(pick(d, "calendarDate", "totalSteps", "stepGoal", "totalDistance"))
        for d in days or []
    ]


@tool
async def garmin_spo2(date: str | None = None) -> dict[str, Any]:
    """Pulse oximetry for a day: average and lowest overnight SpO2."""
    cdate = parse_date(date)
    data = await connection.call("get_spo2_data", cdate)
    if not data:
        return {"date": cdate, "note": "No SpO2 data for this date."}
    return prune(
        {
            "date": cdate,
            **pick(
                data,
                "averageSpO2",
                "lowestSpO2",
                "latestSpO2",
                "latestSpO2TimestampLocal",
                "avgSleepSpO2",
                "avgTomorrowSleepSpO2",
            ),
        }
    )


@tool
async def garmin_respiration(date: str | None = None) -> dict[str, Any]:
    """Breathing rate for a day: waking, sleeping, highest and lowest breaths per minute."""
    cdate = parse_date(date)
    data = await connection.call("get_respiration_data", cdate)
    return prune(
        {
            "date": cdate,
            **pick(
                data,
                "avgWakingRespirationValue",
                "avgSleepRespirationValue",
                "highestRespirationValue",
                "lowestRespirationValue",
                "latestRespirationValue",
            ),
        }
    )


@tool
async def garmin_intensity_minutes(date: str | None = None) -> dict[str, Any]:
    """Moderate and vigorous intensity minutes for a day, against the weekly goal."""
    cdate = parse_date(date)
    data = await connection.call("get_intensity_minutes_data", cdate)
    return cap(prune({"date": cdate, **(data or {})}))


# --- training ------------------------------------------------------------


@tool
async def garmin_training_readiness(date: str | None = None) -> Any:
    """Training readiness score for a day, with the factors that drove it."""
    cdate = parse_date(date)
    data = await connection.call("get_training_readiness", cdate)
    entries = data if isinstance(data, list) else [data] if data else []
    return [
        prune(
            pick(
                entry,
                "calendarDate",
                "timestampLocal",
                "score",
                "level",
                "feedbackShort",
                "feedbackLong",
                "sleepScore",
                "sleepScoreFactorPercent",
                "sleepScoreFactorFeedback",
                "recoveryTime",
                "recoveryTimeFactorPercent",
                "recoveryTimeFactorFeedback",
                "acuteLoad",
                "acwrFactorPercent",
                "acwrFactorFeedback",
                "hrvFactorPercent",
                "hrvFactorFeedback",
                "stressHistoryFactorPercent",
                "stressHistoryFactorFeedback",
            )
        )
        for entry in entries
    ]


@tool
async def garmin_training_status(date: str | None = None) -> dict[str, Any]:
    """Training status, acute/chronic load balance and VO2 max as of a date."""
    cdate = parse_date(date)
    data = await connection.call("get_training_status", cdate)
    result: dict[str, Any] = {"date": cdate}
    latest = (data or {}).get("mostRecentTrainingStatus") or {}
    for device in (latest.get("latestTrainingStatusData") or {}).values():
        result.update(
            pick(
                device,
                "trainingStatus",
                "trainingStatusFeedbackPhrase",
                "weeklyTrainingLoad",
                "loadTunnelMin",
                "loadTunnelMax",
                "fitnessTrend",
                "fitnessTrendSport",
            )
        )
        break
    load = (data or {}).get("mostRecentTrainingLoadBalance") or {}
    for device in (load.get("metricsTrainingLoadBalanceDTOMap") or {}).values():
        result["loadBalance"] = prune(
            pick(
                device,
                "monthlyLoadAerobicLow",
                "monthlyLoadAerobicHigh",
                "monthlyLoadAnaerobic",
                "trainingBalanceFeedbackPhrase",
            )
        )
        break
    vo2 = (data or {}).get("mostRecentVO2Max") or {}
    result["vo2Max"] = prune(
        {
            **pick(vo2.get("generic") or {}, "vo2MaxPreciseValue", "fitnessAge", "maxMetCategory"),
            "cycling": (vo2.get("cycling") or {}).get("vo2MaxPreciseValue"),
        }
    )
    return prune(result)


@tool
async def garmin_vo2max(date: str | None = None) -> dict[str, Any]:
    """VO2 max, fitness age and heat/altitude acclimation as of a date."""
    cdate = parse_date(date)
    data = await connection.call("get_max_metrics", cdate)
    entries = data if isinstance(data, list) else [data] if data else []
    if not entries:
        return {"date": cdate, "note": "No VO2 max data for this date."}
    entry = entries[0] or {}
    return prune(
        {
            "date": cdate,
            "running": pick(
                entry.get("generic") or {},
                "vo2MaxPreciseValue",
                "fitnessAge",
                "maxMetCategory",
            ),
            "cycling": pick(entry.get("cycling") or {}, "vo2MaxPreciseValue"),
            "heatAltitude": pick(
                entry.get("heatAltitudeAcclimation") or {},
                "heatAcclimationPercentage",
                "altitudeAcclimation",
                "currentAltitude",
                "acclimationPercentage",
            ),
        }
    )


@tool
async def garmin_race_predictions() -> dict[str, Any]:
    """Predicted race times for 5K, 10K, half and full marathon."""
    data = await connection.call("get_race_predictions")
    entry = data[0] if isinstance(data, list) and data else data
    return prune(
        pick(
            entry or {},
            "calendarDate",
            "time5K",
            "time10K",
            "timeHalfMarathon",
            "timeMarathon",
        )
    )


@tool
async def garmin_personal_records() -> Any:
    """Personal records across activity types."""
    data = await connection.call("get_personal_record")
    records = data if isinstance(data, list) else (data or {}).get("personalRecords") or []
    return cap(
        [
            prune(
                pick(
                    r,
                    "typeId",
                    "activityId",
                    "activityName",
                    "activityType",
                    "activityStartDateTimeLocal",
                    "value",
                    "prStartTimeGmtFormatted",
                )
            )
            for r in records
        ]
    )


# --- activities ----------------------------------------------------------


@tool
async def garmin_activities(
    limit: int = 10, offset: int = 0, activity_type: str | None = None
) -> list[dict[str, Any]]:
    """Most recent activities, newest first. activity_type filters e.g. running, cycling."""
    data = await connection.call("get_activities", offset, limit, activity_type)
    items = data if isinstance(data, list) else (data or {}).get("activityList") or []
    return [summarise_activity(a) for a in items]


@tool
async def garmin_activities_by_date(
    start: str | None = None, end: str | None = None, activity_type: str | None = None
) -> list[dict[str, Any]]:
    """Activities within a date range (default: last 7 days), oldest first."""
    first, last = date_range(start, end)
    data = await connection.call("get_activities_by_date", first, last, activity_type)
    return [summarise_activity(a) for a in data or []]


@tool
async def garmin_last_activity() -> dict[str, Any]:
    """The most recently recorded activity."""
    data = await connection.call("get_last_activity")
    return summarise_activity(data or {})


@tool
async def garmin_activity(activity_id: str, raw: bool = False) -> dict[str, Any]:
    """One activity in detail. raw=true returns the full payload including chart samples."""
    if raw:
        data = await connection.call("get_activity_details", activity_id, 100, 100)
        return cap(prune(data))
    data = await connection.call("get_activity", activity_id)
    summary = (data or {}).get("summaryDTO") or {}
    type_key = ((data or {}).get("activityTypeDTO") or {}).get("typeKey")
    result = prune(
        {
            "activityId": (data or {}).get("activityId"),
            "activityName": (data or {}).get("activityName"),
            "activityType": type_key,
            "description": (data or {}).get("description"),
            **label_units(pick(
                summary,
                "startTimeLocal",
                "distance",
                "duration",
                "movingDuration",
                "elevationGain",
                "elevationLoss",
                "averageSpeed",
                "maxSpeed",
                "calories",
                "averageHR",
                "maxHR",
                "averageRunCadence",
                "averagePower",
                "normalizedPower",
                "trainingEffect",
                "anaerobicTrainingEffect",
                "trainingEffectLabel",
                "vO2MaxValue",
                "averageTemperature",
            )),
        }
    )
    return add_pace(result, type_key)


@tool
async def garmin_activity_splits(activity_id: str) -> Any:
    """Per-lap splits for an activity."""
    data = await connection.call("get_activity_splits", activity_id)
    laps = (data or {}).get("lapDTOs") or []
    return cap(
        [
            prune(
                label_units(
                    pick(
                        lap,
                        "lapIndex",
                        "distance",
                        "duration",
                        "movingDuration",
                        "averageSpeed",
                        "maxSpeed",
                        "averageHR",
                        "maxHR",
                        "elevationGain",
                        "elevationLoss",
                        "calories",
                    )
                )
            )
            for lap in laps
        ]
    )


@tool
async def garmin_activity_weather(activity_id: str) -> dict[str, Any]:
    """Weather recorded during an activity."""
    data = await connection.call("get_activity_weather", activity_id)
    return prune(
        pick(
            data or {},
            "temp",
            "apparentTemp",
            "dewPoint",
            "relativeHumidity",
            "windDirectionCompassPoint",
            "windSpeed",
            "weatherTypeDTO",
            "weatherStationDTO",
        )
    )


# --- body composition ----------------------------------------------------


@tool
async def garmin_weight(start: str | None = None, end: str | None = None) -> Any:
    """Weigh-ins over a date range (default: last 7 days), with body composition where recorded."""
    first, last = date_range(start, end)
    data = await connection.call("get_weigh_ins", first, last)
    days = (data or {}).get("dailyWeightSummaries") or []
    out = []
    for day in days:
        latest = day.get("latestWeight") or {}
        out.append(
            prune(
                {
                    "date": day.get("summaryDate"),
                    **label_units(
                        pick(
                            latest,
                            "weight",
                            "bmi",
                            "bodyFat",
                            "bodyWater",
                            "boneMass",
                            "muscleMass",
                            "sourceType",
                        )
                    ),
                }
            )
        )
    return cap(out)


# --- composite -----------------------------------------------------------


@tool
async def garmin_briefing(date: str | None = None) -> dict[str, Any]:
    """Morning snapshot in one call: sleep, HRV, Body Battery, readiness, stress and RHR.

    Fetches each section concurrently so answering "how am I doing today?" costs
    one round trip instead of six. A section that fails is named in
    ``sectionsUnavailable`` rather than failing the whole briefing, and a
    section the device does not record simply reports its own note.

    Returns the numbers only — no training advice. Read readiness, HRV and Body
    Battery together rather than any one alone.
    """
    cdate = parse_date(date)

    sections: dict[str, Any] = {}
    unavailable: dict[str, str] = {}

    async def section(name: str, fetch) -> None:
        try:
            sections[name] = await fetch()
        except Exception as exc:  # noqa: BLE001 - one bad section must not sink the rest
            unavailable[name] = str(exc)

    async with anyio.create_task_group() as tg:
        for name, fetch in (
            ("sleep", partial(garmin_sleep, cdate)),
            ("hrv", partial(garmin_hrv, cdate)),
            ("stress", partial(garmin_stress, cdate)),
            ("trainingReadiness", partial(garmin_training_readiness, cdate)),
            ("dailySummary", partial(garmin_daily_summary, cdate)),
            ("bodyBattery", partial(garmin_body_battery, end=cdate)),
        ):
            tg.start_soon(section, name, fetch)

    no_data = sorted(name for name, payload in sections.items() if is_thin(payload))
    result: dict[str, Any] = {
        "date": cdate,
        **{k: v for k, v in sections.items() if k not in no_data},
    }
    if no_data:
        result["sectionsNoData"] = no_data
    if unavailable:
        result["sectionsUnavailable"] = unavailable
    return cap(prune(result))


# --- escape hatch --------------------------------------------------------


@tool
async def garmin_api_get(path: str) -> Any:
    """Call any Garmin Connect API path directly (read-only GET).

    For endpoints the dedicated tools do not cover. Paths look like
    '/usersummary-service/usersummary/daily/{displayName}?calendarDate=2026-09-19'.
    Prefer a dedicated tool when one exists — this returns unshaped payloads.
    """
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    data = await connection.call("connectapi", path)
    return cap(prune(data))


# --- sign-in -------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def garmin_submit_mfa_code(code: str) -> dict[str, Any]:
    """Finish signing in to Garmin with the one-time code it emailed.

    Only needed when a Garmin tool has just reported that a code was sent. The
    code is valid for 30 minutes; afterwards the saved tokens last about a year
    and this is not asked for again.
    """
    name = await anyio.to_thread.run_sync(connection.submit_mfa_code, code)
    return {"status": "signed in", "account": name}


# --- writes (opt-in) -----------------------------------------------------

if WRITES_ENABLED:

    @write_tool
    async def garmin_rename_activity(activity_id: str, name: str) -> dict[str, Any]:
        """Rename an activity in Garmin Connect."""
        await connection.call("set_activity_name", activity_id, name)
        return {"activityId": activity_id, "activityName": name, "status": "renamed"}

    @write_tool
    async def garmin_add_weight(weight: float, unit: str = "kg") -> dict[str, Any]:
        """Record a manual weigh-in. unit is 'kg' or 'lbs'."""
        if unit not in {"kg", "lbs"}:
            raise ValueError("unit must be 'kg' or 'lbs'")
        await connection.call("add_weigh_in", weight, unit)
        return {"weight": weight, "unit": unit, "status": "recorded"}

    @write_tool
    async def garmin_add_hydration(milliliters: float, date: str | None = None) -> dict[str, Any]:
        """Log fluid intake in millilitres for a day."""
        cdate = parse_date(date)
        await connection.call("add_hydration_data", milliliters, None, cdate)
        return {"date": cdate, "milliliters": milliliters, "status": "recorded"}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
