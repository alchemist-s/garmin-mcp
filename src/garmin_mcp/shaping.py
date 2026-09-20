"""Date parsing and response shaping.

Garmin's payloads are built for a dashboard, not a context window: a single
activity detail carries thousands of chart samples, and a 20-activity list runs
past 100 kB of mostly-null fields. Every tool therefore returns a compact
projection by default and the raw payload only when asked.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

MAX_CHARS = 60_000


def parse_date(value: str | None) -> str:
    """Accept ``YYYY-MM-DD``, ``today``, ``yesterday``, ``-3d``, or None (today)."""
    if value is None or value.strip() == "":
        return date.today().isoformat()
    text = value.strip().lower()
    if text == "today":
        return date.today().isoformat()
    if text == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if text.startswith("-") and text.endswith("d"):
        try:
            return (date.today() - timedelta(days=int(text[1:-1]))).isoformat()
        except ValueError:
            pass
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Unrecognised date {value!r}. Use YYYY-MM-DD, 'today', 'yesterday', or '-7d'."
        ) from exc


def date_range(start: str | None, end: str | None, default_days: int = 7) -> tuple[str, str]:
    """Resolve a start/end pair, defaulting to the last ``default_days`` ending today."""
    resolved_end = parse_date(end)
    if start is None or start.strip() == "":
        resolved_start = (
            date.fromisoformat(resolved_end) - timedelta(days=default_days - 1)
        ).isoformat()
    else:
        resolved_start = parse_date(start)
    if resolved_start > resolved_end:
        raise ValueError(f"start {resolved_start} is after end {resolved_end}")
    return resolved_start, resolved_end


def prune(value: Any) -> Any:
    """Recursively drop nulls and empty containers, which dominate Garmin payloads."""
    if isinstance(value, dict):
        cleaned = {k: prune(v) for k, v in value.items() if v is not None}
        return {k: v for k, v in cleaned.items() if v != {} and v != []}
    if isinstance(value, list):
        return [prune(v) for v in value if v is not None]
    return value


def pick(source: dict[str, Any] | None, *keys: str) -> dict[str, Any]:
    """Project the named keys that are actually present and non-null."""
    if not isinstance(source, dict):
        return {}
    return {k: source[k] for k in keys if source.get(k) is not None}


def cap(payload: Any) -> Any:
    """Guard against a payload that would swamp the caller's context.

    Returning a marker rather than silently truncated JSON matters: truncated
    JSON reads as complete data that happens to end early, and gets summarised
    as fact.
    """
    rendered = json.dumps(payload, default=str)
    if len(rendered) <= MAX_CHARS:
        return payload
    return {
        "truncated": True,
        "reason": (
            f"Response was {len(rendered)} characters, over the {MAX_CHARS} limit. "
            "Narrow the date range, lower the limit, or drop raw=true."
        ),
        "preview": rendered[:2000],
    }


ACTIVITY_KEYS = (
    "activityId",
    "activityName",
    "startTimeLocal",
    "distance",
    "duration",
    "elapsedDuration",
    "movingDuration",
    "elevationGain",
    "elevationLoss",
    "averageSpeed",
    "maxSpeed",
    "calories",
    "averageHR",
    "maxHR",
    "averageRunningCadenceInStepsPerMinute",
    "avgPower",
    "maxPower",
    "normPower",
    "aerobicTrainingEffect",
    "anaerobicTrainingEffect",
    "vO2MaxValue",
    "steps",
)


def summarise_activity(activity: dict[str, Any]) -> dict[str, Any]:
    """One activity, flattened to the fields a person actually asks about."""
    summary = label_units(pick(activity, *ACTIVITY_KEYS))
    type_key = (activity.get("activityType") or {}).get("typeKey")
    if type_key:
        summary["activityType"] = type_key
    location = activity.get("locationName")
    if location:
        summary["locationName"] = location
    return add_pace(summary, type_key)


# Garmin reports raw SI-ish values with no units attached: distances in metres,
# durations in seconds, speeds in metres per second — and body mass in *grams*.
# A bare ``"weight": 74500`` reads as kilograms to anything summarising it, and
# ``"averageSpeed": 2.75`` reads as km/h. So the unit travels in the key name,
# and grams are converted, because that one is actively wrong rather than merely
# ambiguous.
GRAMS_TO_KG = 0.001

FIELD_UNITS: dict[str, tuple[str, float | None]] = {
    "distance": ("distanceMeters", None),
    "duration": ("durationSeconds", None),
    "elapsedDuration": ("elapsedDurationSeconds", None),
    "movingDuration": ("movingDurationSeconds", None),
    "elevationGain": ("elevationGainMeters", None),
    "elevationLoss": ("elevationLossMeters", None),
    "averageSpeed": ("averageSpeedMetersPerSecond", None),
    "maxSpeed": ("maxSpeedMetersPerSecond", None),
    "calories": ("caloriesKcal", None),
    "averageHR": ("averageHeartRateBpm", None),
    "maxHR": ("maxHeartRateBpm", None),
    "averageRunningCadenceInStepsPerMinute": ("averageCadenceStepsPerMinute", None),
    "averageRunCadence": ("averageCadenceStepsPerMinute", None),
    "avgPower": ("averagePowerWatts", None),
    "averagePower": ("averagePowerWatts", None),
    "maxPower": ("maxPowerWatts", None),
    "normPower": ("normalizedPowerWatts", None),
    "normalizedPower": ("normalizedPowerWatts", None),
    "averageTemperature": ("averageTemperatureCelsius", None),
    # Body composition: grams on the wire.
    "weight": ("weightKg", GRAMS_TO_KG),
    "muscleMass": ("muscleMassKg", GRAMS_TO_KG),
    "boneMass": ("boneMassKg", GRAMS_TO_KG),
    "bodyFat": ("bodyFatPercent", None),
    "bodyWater": ("bodyWaterPercent", None),
}

# Pace is what a runner actually reads; deriving it removes the main reason
# anyone would misinterpret a speed in metres per second.
PACED_ACTIVITIES = ("running", "walking", "hiking", "trail_running", "treadmill_running")


def label_units(data: dict[str, Any]) -> dict[str, Any]:
    """Rename fields so their unit is explicit, converting where Garmin uses grams."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        name, factor = FIELD_UNITS.get(key, (key, None))
        if factor is not None and isinstance(value, (int, float)):
            value = round(value * factor, 3)
        out[name] = value
    return out


def add_pace(data: dict[str, Any], activity_type: str | None) -> dict[str, Any]:
    """Add minutes-per-kilometre for foot-based activities, when both inputs exist."""
    if not activity_type or not any(p in activity_type for p in PACED_ACTIVITIES):
        return data
    metres = data.get("distanceMeters")
    seconds = data.get("movingDurationSeconds") or data.get("durationSeconds")
    if not isinstance(metres, (int, float)) or not isinstance(seconds, (int, float)):
        return data
    if metres <= 0 or seconds <= 0:
        return data
    pace = (seconds / 60) / (metres / 1000)
    minutes, fraction = divmod(pace, 1)
    data["pace"] = f"{int(minutes)}:{round(fraction * 60):02d} min/km"
    return data


def is_thin(payload: Any) -> bool:
    """True when a section came back successfully but carries no actual reading.

    Distinguishing this from a failure matters: an unsupported metric is a fact
    about the watch, not an outage, and dropping it silently would leave the
    caller unable to tell it was requested at all.
    """
    if not payload:
        return True
    if isinstance(payload, dict):
        return not {k for k in payload if k not in {"note", "date"}}
    return False
