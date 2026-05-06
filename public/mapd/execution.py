from __future__ import annotations

import random
import time

from mapd.models import AgentPlan, FAILURE_MODEL_CHOICES, ScenarioMetadata, Task
from mapd.planner import build_agent_plans_from_state


def should_trigger_delay(seed: int, agent_id: int, time_step: int, probability: float) -> bool:
    if probability <= 0.0:
        return False
    rng = random.Random(seed + agent_id * 1_000_003 + time_step * 9_176)
    return rng.random() < probability


def delay_duration(seed: int, agent_id: int, time_step: int, minimum: int, maximum: int) -> int:
    if minimum >= maximum:
        return minimum
    rng = random.Random(seed + agent_id * 1_999_979 + time_step * 53_123 + 17)
    return rng.randint(minimum, maximum)


def remaining_active_delay(plan: AgentPlan, time_step: int) -> int:
    delay = 0
    next_time = time_step + 1
    while next_time + delay in plan.delayed_times:
        delay += 1
    return delay


def extract_remaining_state(plan: AgentPlan, time_step: int) -> tuple[list[Task], bool]:
    task_index = 0
    while task_index < len(plan.tasks):
        task = plan.tasks[task_index]
        completion_time = plan.completion_times.get(task.task_id)
        if completion_time is not None and completion_time <= time_step:
            task_index += 1
            continue
        break

    remaining_tasks = plan.tasks[task_index:]
    carrying = False
    if remaining_tasks:
        current_task = remaining_tasks[0]
        pickup_time = plan.pickup_times.get(current_task.task_id)
        completion_time = plan.completion_times.get(current_task.task_id)
        carrying = (
            pickup_time is not None
            and pickup_time <= time_step
            and completion_time is not None
            and time_step < completion_time
        )

    return remaining_tasks, carrying


def plan_position_at_time(plan: AgentPlan, time_step: int) -> tuple[int, int]:
    if time_step < len(plan.path):
        return plan.path[time_step]
    return plan.path[-1]


def next_failure_event(
    plans: list[AgentPlan],
    *,
    probability: float,
    duration_min: int,
    duration_max: int,
    seed: int,
    used_triggers: set[tuple[int, int]],
    start_time: int,
    deadline: float | None = None,
) -> tuple[int, dict[int, int]] | None:
    if not plans:
        return None

    max_time = max(len(plan.path) for plan in plans) - 1
    for time_step in range(start_time, max_time):
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Failure handling exceeded the time budget.")
        failures: dict[int, int] = {}
        for plan in plans:
            if time_step >= len(plan.path) - 1:
                continue
            if remaining_active_delay(plan, time_step) > 0:
                continue
            trigger_key = (plan.agent_id, time_step)
            if trigger_key in used_triggers:
                continue
            if not should_trigger_delay(seed, plan.agent_id, time_step, probability):
                continue

            duration = delay_duration(seed, plan.agent_id, time_step, duration_min, duration_max)
            if duration <= 0:
                used_triggers.add(trigger_key)
                continue

            failures[plan.agent_id] = duration
            used_triggers.add(trigger_key)

        if failures:
            return time_step, failures

    return None


def merge_replanned_plans(
    current_plans: list[AgentPlan],
    suffix_plans: list[AgentPlan],
    event_time: int,
) -> list[AgentPlan]:
    suffix_by_id = {plan.agent_id: plan for plan in suffix_plans}
    merged: list[AgentPlan] = []

    for plan in current_plans:
        suffix = suffix_by_id[plan.agent_id]
        prefix_path = plan.path[: event_time + 1]
        path = prefix_path + suffix.path[1:]

        pickup_times: dict[int, int] = {}
        completion_times: dict[int, int] = {}

        for task in plan.tasks:
            task_id = task.task_id
            suffix_pickup = suffix.pickup_times.get(task_id)
            suffix_completion = suffix.completion_times.get(task_id)
            previous_pickup = plan.pickup_times.get(task_id)
            previous_completion = plan.completion_times.get(task_id)

            if suffix_pickup is not None:
                pickup_times[task_id] = suffix_pickup
            elif previous_pickup is not None and previous_pickup <= event_time:
                pickup_times[task_id] = previous_pickup

            if suffix_completion is not None:
                completion_times[task_id] = suffix_completion
            elif previous_completion is not None and previous_completion <= event_time:
                completion_times[task_id] = previous_completion

        missed_deadlines = [
            task.task_id
            for task in plan.tasks
            if task.deadline is not None
            and completion_times.get(task.task_id) is not None
            and completion_times[task.task_id] > task.deadline
        ]

        delayed_times = {time for time in plan.delayed_times if time <= event_time}
        delayed_times.update(event_time + time for time in suffix.delayed_times)

        failure_start_times = {time for time in plan.failure_start_times if time <= event_time}
        failure_start_times.update(event_time + time for time in suffix.failure_start_times)

        merged.append(
            AgentPlan(
                agent_id=plan.agent_id,
                color=plan.color,
                home=plan.home,
                home_index=plan.home_index,
                path=path,
                tasks=plan.tasks,
                pickup_times=pickup_times,
                completion_times=completion_times,
                missed_deadlines=missed_deadlines,
                delayed_times=delayed_times,
                failure_start_times=failure_start_times,
            )
        )

    return merged


def replan_after_failures(
    warehouse,
    plans: list[AgentPlan],
    *,
    event_time: int,
    new_failures: dict[int, int],
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[AgentPlan]:
    tasks_by_agent: dict[int, list[Task]] = {}
    homes = {}
    start_positions = {}
    carrying_by_agent = {}
    forced_waits = {}
    colors: list[tuple[int, int, int]] = []

    max_agent_id = max((plan.agent_id for plan in plans), default=-1)
    if max_agent_id >= 0:
        colors = [(0, 0, 0)] * (max_agent_id + 1)

    for plan in plans:
        agent_id = plan.agent_id
        homes[agent_id] = plan.home
        start_positions[agent_id] = plan_position_at_time(plan, event_time)
        remaining_tasks, carrying = extract_remaining_state(plan, event_time)
        tasks_by_agent[agent_id] = remaining_tasks
        carrying_by_agent[agent_id] = carrying
        colors[agent_id] = plan.color

        outstanding_delay = remaining_active_delay(plan, event_time)
        if agent_id in new_failures:
            forced_waits[agent_id] = new_failures[agent_id]
        else:
            forced_waits[agent_id] = outstanding_delay

    return build_agent_plans_from_state(
        warehouse=warehouse,
        tasks_by_agent=tasks_by_agent,
        homes=homes,
        start_positions=start_positions,
        carrying_by_agent=carrying_by_agent,
        forced_waits=forced_waits,
        mark_failure_start=set(new_failures),
        absolute_start_time=event_time,
        colors=colors,
        station_mode=station_mode,
        algorithm=algorithm,
        deadline=deadline,
    )


def apply_agent_delay_model(
    warehouse,
    plans: list[AgentPlan],
    metadata: ScenarioMetadata,
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> tuple[list[AgentPlan], int, int]:
    probability = metadata.failure_probability or 0.0
    duration_min = metadata.failure_duration_min or 0
    duration_max = metadata.failure_duration_max or duration_min
    seed = metadata.failure_seed if metadata.failure_seed is not None else metadata.seed or 0

    if probability <= 0.0 or duration_max <= 0:
        return plans, 0, 0

    current_plans = plans
    failure_count = 0
    failure_delay_steps = 0
    used_triggers: set[tuple[int, int]] = set()
    scan_time = 0

    while True:
        next_event = next_failure_event(
            current_plans,
            probability=probability,
            duration_min=duration_min,
            duration_max=duration_max,
            seed=seed,
            used_triggers=used_triggers,
            start_time=scan_time,
            deadline=deadline,
        )
        if next_event is None:
            return current_plans, failure_count, failure_delay_steps

        event_time, new_failures = next_event
        current_plans = merge_replanned_plans(
            current_plans,
            replan_after_failures(
                warehouse,
                current_plans,
                event_time=event_time,
                new_failures=new_failures,
                station_mode=station_mode,
                algorithm=algorithm,
                deadline=deadline,
            ),
            event_time,
        )
        failure_count += len(new_failures)
        failure_delay_steps += sum(new_failures.values())
        scan_time = event_time + 1


def apply_failure_model(
    warehouse,
    plans: list[AgentPlan],
    metadata: ScenarioMetadata,
    failure_model: str,
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> tuple[list[AgentPlan], int, int]:
    normalized = failure_model.strip()
    if normalized not in FAILURE_MODEL_CHOICES:
        raise ValueError(f"Unsupported failure model: {failure_model}")
    if normalized == "None":
        return plans, 0, 0
    return apply_agent_delay_model(warehouse, plans, metadata, station_mode, algorithm, deadline=deadline)
