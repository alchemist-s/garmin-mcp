"""Build Garmin structured workouts from a schema a model can actually write.

Garmin's own format nests four dictionaries per step, with ids and display
orders that have to agree with their keys, and speeds in metres per second. No
model should be hand-writing that, and any that tries will get it subtly wrong.
So tools take plain steps — ``{"kind": "interval", "length": "800m",
"pace": "4:30-4:20"}`` — and this module does the translation.
"""

from __future__ import annotations

import re
from typing import Any

from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    SportType,
    StepType,
    TargetType,
    WorkoutSegment,
)

STEP_KINDS = {
    "warmup": (StepType.WARMUP, "warmup", 1),
    "interval": (StepType.INTERVAL, "interval", 3),
    "recovery": (StepType.RECOVERY, "recovery", 4),
    "rest": (StepType.REST, "rest", 5),
    "cooldown": (StepType.COOLDOWN, "cooldown", 2),
    # "run" is the natural word for a steady effort; Garmin calls it an interval.
    "run": (StepType.INTERVAL, "interval", 3),
}

_DISTANCE = re.compile(r"^([\d.]+)\s*(km|k|m|mi)$", re.I)
_TIME = re.compile(r"^([\d.]+)\s*(min|mins|minutes|s|sec|secs|h|hr|hours)$", re.I)
_PACE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_length(value: str) -> tuple[str, float]:
    """Return ("distance", metres) or ("time", seconds) from "800m", "10min", "1km".

    Distance is checked first, and "min" before "m", so "10min" is never read as
    ten metres.
    """
    text = str(value).strip().lower()

    if m := _TIME.match(text):
        amount, unit = float(m.group(1)), m.group(2)
        if unit.startswith("h"):
            return "time", amount * 3600
        if unit.startswith("s"):
            return "time", amount
        return "time", amount * 60

    if m := _DISTANCE.match(text):
        amount, unit = float(m.group(1)), m.group(2)
        if unit in ("km", "k"):
            return "distance", amount * 1000
        if unit == "mi":
            return "distance", amount * 1609.344
        return "distance", amount

    raise ValueError(
        f"Could not read a length from {value!r}. Use a distance like '800m', "
        "'5km' or '3mi', or a time like '10min', '90s' or '1h'."
    )


def pace_to_speed(pace: str) -> float:
    """Convert "5:30" (min:sec per kilometre) to metres per second."""
    m = _PACE.match(str(pace).strip())
    if not m:
        raise ValueError(f"Could not read a pace from {pace!r}. Use mm:ss per km, e.g. '5:30'.")
    seconds = int(m.group(1)) * 60 + int(m.group(2))
    if seconds <= 0:
        raise ValueError("Pace must be greater than zero.")
    return 1000.0 / seconds


def _no_target() -> dict[str, Any]:
    return {
        "workoutTargetTypeId": TargetType.NO_TARGET,
        "workoutTargetTypeKey": "no.target",
        "displayOrder": 1,
    }


def _target_for(step: dict[str, Any]) -> tuple[dict[str, Any], float | None, float | None]:
    """Build the target for a step: a pace range, a heart-rate zone, or nothing."""
    pace = step.get("pace")
    if pace:
        # "4:30-4:20" or a single "5:00" treated as a tight range around itself.
        parts = [p.strip() for p in str(pace).replace("/km", "").split("-")]
        speeds = sorted(pace_to_speed(p) for p in parts)
        slowest = speeds[0]
        fastest = speeds[-1]
        if slowest == fastest:
            # Garmin needs a band, not a point; ±2% is about ±6s/km at 5:00.
            slowest, fastest = slowest * 0.98, fastest * 1.02
        return (
            {
                "workoutTargetTypeId": TargetType.SPEED,
                "workoutTargetTypeKey": "speed.zone",
                "displayOrder": 5,
            },
            round(slowest, 4),
            round(fastest, 4),
        )

    zone = step.get("heart_rate_zone")
    if zone:
        return (
            {
                "workoutTargetTypeId": TargetType.HEART_RATE,
                "workoutTargetTypeKey": "heart.rate.zone",
                "displayOrder": 4,
            },
            int(zone),
            int(zone),
        )

    return _no_target(), None, None


def _executable(step: dict[str, Any], order: int) -> ExecutableStep:
    kind = str(step.get("kind", "run")).lower()
    if kind not in STEP_KINDS:
        raise ValueError(
            f"Unknown step kind {kind!r}. Use one of: {', '.join(sorted(STEP_KINDS))}."
        )
    type_id, type_key, display = STEP_KINDS[kind]

    if "length" not in step:
        raise ValueError(f"Step {order} ({kind}) needs a 'length', such as '800m' or '10min'.")
    unit, amount = parse_length(step["length"])

    condition = (
        {
            "conditionTypeId": ConditionType.DISTANCE,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }
        if unit == "distance"
        else {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
    )

    target, one, two = _target_for(step)
    built = ExecutableStep(
        type="ExecutableStepDTO",
        stepOrder=order,
        stepType={"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": display},
        endCondition=condition,
        endConditionValue=amount,
        targetType=target,
    )
    if one is not None:
        built.targetValueOne = one
        built.targetValueTwo = two
    if step.get("note"):
        built.description = str(step["note"])
    return built


def _estimate_seconds(steps: list[dict[str, Any]]) -> int:
    """Rough duration, used only for the summary Garmin shows in its list."""
    total = 0.0
    for step in steps:
        repeat = int(step.get("repeat", 1) or 1)
        children = step.get("steps") or [step]
        for child in children:
            if "length" not in child:
                continue
            unit, amount = parse_length(child["length"])
            if unit == "time":
                total += amount * repeat
            else:
                # Assume 6:00/km when no pace is given: only affects the estimate.
                speed = 1000 / 360
                if child.get("pace"):
                    parts = [p.strip() for p in str(child["pace"]).replace("/km", "").split("-")]
                    speed = sum(pace_to_speed(p) for p in parts) / len(parts)
                total += (amount / speed) * repeat
    return int(total)


def build_running_workout(
    name: str, steps: list[dict[str, Any]], description: str | None = None
) -> RunningWorkout:
    """Assemble a Garmin running workout from plain steps.

    A step with ``repeat`` and a nested ``steps`` list becomes a repeat group,
    which is how Garmin represents "6 x 800m with 90s jog".
    """
    if not steps:
        raise ValueError("A workout needs at least one step.")

    built: list[Any] = []
    order = 1
    for step in steps:
        repeat = int(step.get("repeat", 1) or 1)
        if repeat > 1 or step.get("steps"):
            children = step.get("steps")
            if not children:
                raise ValueError(
                    "A repeated step needs a nested 'steps' list, e.g. "
                    "{'repeat': 6, 'steps': [{'kind': 'interval', 'length': '800m'}, "
                    "{'kind': 'recovery', 'length': '90s'}]}"
                )
            group = RepeatGroup(
                type="RepeatGroupDTO",
                stepOrder=order,
                stepType={
                    "stepTypeId": StepType.REPEAT,
                    "stepTypeKey": "repeat",
                    "displayOrder": 6,
                },
                numberOfIterations=repeat,
                workoutSteps=[],
                endCondition={
                    "conditionTypeId": ConditionType.ITERATIONS,
                    "conditionTypeKey": "iterations",
                    "displayOrder": 7,
                    "displayable": False,
                },
                endConditionValue=float(repeat),
                smartRepeat=False,
            )
            order += 1
            for child in children:
                group.workoutSteps.append(_executable(child, order))
                order += 1
            built.append(group)
        else:
            built.append(_executable(step, order))
            order += 1

    return RunningWorkout(
        workoutName=name,
        description=description,
        estimatedDurationInSecs=_estimate_seconds(steps),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={
                    "sportTypeId": SportType.RUNNING,
                    "sportTypeKey": "running",
                    "displayOrder": 1,
                },
                workoutSteps=built,
            )
        ],
    )
