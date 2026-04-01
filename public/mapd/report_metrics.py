from __future__ import annotations

from mapd.models import AgentPlan, Coord


def status_label(status: str) -> str:
    return "solved" if status == "Solved" else "unsolved"


def mode_label(value: str) -> str:
    return value.strip().lower()


def strategy_label(value: str) -> str:
    normalized = value.strip().lower()
    return "" if normalized == "none" else normalized


def algorithm_label(value: str) -> str:
    return value.strip().lower()


def layout_size_label(value: str | None) -> str:
    return "legacy" if value is None else value.strip().lower()


def format_duration(seconds: float) -> str:
    return f"{seconds:.2f}s"


def missed_deadline_count(plans: list[AgentPlan] | None) -> int | None:
    if plans is None:
        return None
    return sum(len(plan.missed_deadlines) for plan in plans)


def wait_step_count(plans: list[AgentPlan] | None, station_cells: set[Coord]) -> int | None:
    if plans is None:
        return None

    waits = 0
    for plan in plans:
        for previous, current in zip(plan.path, plan.path[1:]):
            if previous == current and current not in station_cells:
                waits += 1
    return waits


def distance_step_sum(plans: list[AgentPlan] | None) -> int | None:
    if plans is None:
        return None

    distance = 0
    for plan in plans:
        for previous, current in zip(plan.path, plan.path[1:]):
            if previous != current:
                distance += 1
    return distance


def throughput(task_count: int, makespan: int | None) -> float | None:
    if makespan is None or makespan <= 0:
        return None
    return round(task_count / makespan, 4)
