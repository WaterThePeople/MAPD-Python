from __future__ import annotations

import random
import time

from mapd.collisions import frame_agent_positions
from mapd.models import AgentPlan, FAILURE_MODEL_CHOICES, PlanningStats, ScenarioMetadata, Task
from mapd.planner import build_agent_plans_from_state

LOCAL_FAILURE_SMALL_REPLAN_RADIUS = 8
LOCAL_FAILURE_MEDIUM_REPLAN_RADIUS = 12
LOCAL_FAILURE_LARGE_REPLAN_RADIUS = 16
LOCAL_FAILURE_SMALL_REPLAN_EXPANSIONS = 3
LOCAL_FAILURE_MEDIUM_REPLAN_EXPANSIONS = 4
LOCAL_FAILURE_LARGE_REPLAN_EXPANSIONS = 5
LOCAL_FAILURE_WAIT_REPAIR_MULTIPLIER = 3


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


def shifted_times_after_event(times: dict[int, int], event_time: int, duration: int) -> dict[int, int]:
    return {
        task_id: time_step if time_step <= event_time else time_step + duration
        for task_id, time_step in times.items()
    }


def shifted_time_set_after_event(times: set[int], event_time: int, duration: int) -> set[int]:
    return {
        time_step if time_step <= event_time else time_step + duration
        for time_step in times
    }


def inject_delay_into_plan(plan: AgentPlan, event_time: int, duration: int) -> AgentPlan:
    if duration <= 0:
        return plan

    event_position = plan_position_at_time(plan, event_time)
    prefix_path = plan.path[: event_time + 1]
    if not prefix_path:
        prefix_path = [event_position]
    suffix_path = plan.path[event_time + 1 :] if event_time + 1 < len(plan.path) else []
    path = prefix_path + [event_position] * duration + suffix_path

    pickup_times = shifted_times_after_event(plan.pickup_times, event_time, duration)
    completion_times = shifted_times_after_event(plan.completion_times, event_time, duration)
    missed_deadlines = [
        task.task_id
        for task in plan.tasks
        if task.deadline is not None
        and completion_times.get(task.task_id) is not None
        and completion_times[task.task_id] > task.deadline
    ]
    delayed_times = shifted_time_set_after_event(plan.delayed_times, event_time, duration)
    delayed_times.update(range(event_time + 1, event_time + duration + 1))
    failure_start_times = shifted_time_set_after_event(plan.failure_start_times, event_time, duration)
    failure_start_times.add(event_time + 1)

    return AgentPlan(
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


def insert_wait_into_plan(plan: AgentPlan, event_time: int, duration: int) -> AgentPlan:
    if duration <= 0:
        return plan

    event_position = plan_position_at_time(plan, event_time)
    prefix_path = plan.path[: min(event_time + 1, len(plan.path))]
    if not prefix_path:
        prefix_path = [event_position]
    while len(prefix_path) <= event_time:
        prefix_path.append(event_position)

    suffix_path = plan.path[event_time + 1 :] if event_time + 1 < len(plan.path) else []
    path = prefix_path + [event_position] * duration + suffix_path

    pickup_times = shifted_times_after_event(plan.pickup_times, event_time, duration)
    completion_times = shifted_times_after_event(plan.completion_times, event_time, duration)
    missed_deadlines = [
        task.task_id
        for task in plan.tasks
        if task.deadline is not None
        and completion_times.get(task.task_id) is not None
        and completion_times[task.task_id] > task.deadline
    ]

    return AgentPlan(
        agent_id=plan.agent_id,
        color=plan.color,
        home=plan.home,
        home_index=plan.home_index,
        path=path,
        tasks=plan.tasks,
        pickup_times=pickup_times,
        completion_times=completion_times,
        missed_deadlines=missed_deadlines,
        delayed_times=shifted_time_set_after_event(plan.delayed_times, event_time, duration),
        failure_start_times=shifted_time_set_after_event(plan.failure_start_times, event_time, duration),
    )


def inject_failure_delays(
    plans: list[AgentPlan],
    event_time: int,
    new_failures: dict[int, int],
) -> list[AgentPlan]:
    return [
        inject_delay_into_plan(plan, event_time, new_failures.get(plan.agent_id, 0))
        for plan in plans
    ]


def collision_agent_ids(
    plans: list[AgentPlan],
    *,
    start_time: int = 0,
    focus_agent_ids: set[int] | None = None,
    stop_on_first: bool = False,
) -> set[int]:
    if not plans:
        return set()

    focus = focus_agent_ids
    max_time = max(len(plan.path) for plan in plans) - 1
    colliding_agent_ids: set[int] = set()

    for time_step in range(max(0, start_time), max_time + 1):
        positions = frame_agent_positions(plans, time_step)
        occupancy: dict[tuple[int, int], list[int]] = {}
        for agent_id, coord in positions.items():
            occupancy.setdefault(coord, []).append(agent_id)

        for agent_ids in occupancy.values():
            if len(agent_ids) <= 1:
                continue
            agent_set = set(agent_ids)
            if focus is None or agent_set & focus:
                colliding_agent_ids.update(agent_set)
                if stop_on_first:
                    return colliding_agent_ids

        if time_step <= 0:
            continue

        previous_positions = frame_agent_positions(plans, time_step - 1)
        agent_ids = sorted(positions)
        for index, first_agent_id in enumerate(agent_ids):
            first_coord = positions[first_agent_id]
            for second_agent_id in agent_ids[index + 1 :]:
                second_coord = positions[second_agent_id]
                if first_coord == second_coord:
                    continue
                if (
                    previous_positions[first_agent_id] == second_coord
                    and previous_positions[second_agent_id] == first_coord
                ):
                    agent_set = {first_agent_id, second_agent_id}
                    if focus is None or agent_set & focus:
                        colliding_agent_ids.update(agent_set)
                        if stop_on_first:
                            return colliding_agent_ids

    return colliding_agent_ids


def first_collision(
    plans: list[AgentPlan],
    *,
    start_time: int = 0,
    focus_agent_ids: set[int] | None = None,
) -> tuple[int, set[int]] | None:
    if not plans:
        return None

    focus = focus_agent_ids
    max_time = max(len(plan.path) for plan in plans) - 1

    for time_step in range(max(0, start_time), max_time + 1):
        positions = frame_agent_positions(plans, time_step)
        occupancy: dict[tuple[int, int], list[int]] = {}
        for agent_id, coord in positions.items():
            occupancy.setdefault(coord, []).append(agent_id)

        for agent_ids in occupancy.values():
            if len(agent_ids) <= 1:
                continue
            agent_set = set(agent_ids)
            if focus is None or agent_set & focus:
                return time_step, agent_set

        if time_step <= 0:
            continue

        previous_positions = frame_agent_positions(plans, time_step - 1)
        agent_ids = sorted(positions)
        for index, first_agent_id in enumerate(agent_ids):
            first_coord = positions[first_agent_id]
            for second_agent_id in agent_ids[index + 1 :]:
                second_coord = positions[second_agent_id]
                if first_coord == second_coord:
                    continue
                if (
                    previous_positions[first_agent_id] == second_coord
                    and previous_positions[second_agent_id] == first_coord
                ):
                    agent_set = {first_agent_id, second_agent_id}
                    if focus is None or agent_set & focus:
                        return time_step, agent_set

    return None


def has_collisions(plans: list[AgentPlan], *, start_time: int = 0) -> bool:
    return bool(collision_agent_ids(plans, start_time=start_time, stop_on_first=True))


def local_failure_replan_radius(warehouse) -> int:
    agent_capacity = len(warehouse.stations)
    if agent_capacity <= 20:
        return LOCAL_FAILURE_SMALL_REPLAN_RADIUS
    if agent_capacity <= 40:
        return LOCAL_FAILURE_MEDIUM_REPLAN_RADIUS
    return LOCAL_FAILURE_LARGE_REPLAN_RADIUS


def local_failure_replan_max_expansions(warehouse) -> int:
    agent_capacity = len(warehouse.stations)
    if agent_capacity <= 20:
        return LOCAL_FAILURE_SMALL_REPLAN_EXPANSIONS
    if agent_capacity <= 40:
        return LOCAL_FAILURE_MEDIUM_REPLAN_EXPANSIONS
    return LOCAL_FAILURE_LARGE_REPLAN_EXPANSIONS


def local_failure_wait_repair_limit(warehouse, new_failures: dict[int, int]) -> int:
    base_limit = local_failure_replan_radius(warehouse)
    delay_budget = sum(new_failures.values()) * LOCAL_FAILURE_WAIT_REPAIR_MULTIPLIER
    return max(base_limit, delay_budget)


def repair_collisions_with_local_waits(
    plans: list[AgentPlan],
    warehouse,
    *,
    start_time: int,
    new_failures: dict[int, int],
    deadline: float | None = None,
) -> list[AgentPlan] | None:
    repaired_plans = plans[:]
    failed_agent_ids = set(new_failures)
    repair_limit = local_failure_wait_repair_limit(warehouse, new_failures)

    for _ in range(repair_limit):
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Failure wait repair exceeded the time budget.")

        collision = first_collision(repaired_plans, start_time=start_time)
        if collision is None:
            return repaired_plans

        collision_time, colliding_agents = collision
        plan_by_id = {plan.agent_id: plan for plan in repaired_plans}
        moving_non_failed = [
            agent_id
            for agent_id in sorted(colliding_agents)
            if agent_id not in failed_agent_ids and collision_time < len(plan_by_id[agent_id].path) - 1
        ]
        moving_any = [
            agent_id
            for agent_id in sorted(colliding_agents)
            if collision_time < len(plan_by_id[agent_id].path) - 1
        ]
        repair_candidates = moving_non_failed or moving_any
        if not repair_candidates:
            return None

        repaired_agent_id = repair_candidates[0]
        wait_insert_time = max(0, collision_time - 1)
        repaired_plans = [
            insert_wait_into_plan(plan, wait_insert_time, 1)
            if plan.agent_id == repaired_agent_id
            else plan
            for plan in repaired_plans
        ]

    if first_collision(repaired_plans, start_time=start_time) is None:
        return repaired_plans
    return None


def fixed_suffix_plan(plan: AgentPlan, event_time: int) -> AgentPlan:
    if event_time < len(plan.path):
        path = plan.path[event_time:]
    else:
        path = [plan.path[-1]]

    return AgentPlan(
        agent_id=plan.agent_id,
        color=plan.color,
        home=plan.home,
        home_index=plan.home_index,
        path=path,
        tasks=[],
        pickup_times={},
        completion_times={},
        missed_deadlines=[],
    )


def local_replan_agent_ids(
    warehouse,
    plans: list[AgentPlan],
    event_time: int,
    new_failures: dict[int, int],
    *,
    radius: int,
) -> set[int]:
    failed_agent_ids = set(new_failures)
    selected = set(failed_agent_ids)
    selected.update(
        collision_agent_ids(
            plans,
            start_time=event_time + 1,
            focus_agent_ids=failed_agent_ids,
        )
    )

    failure_positions = [
        plan_position_at_time(plan, event_time)
        for plan in plans
        if plan.agent_id in failed_agent_ids
    ]
    if not failure_positions:
        return selected

    lookahead = max(new_failures.values(), default=0) + radius
    for plan in plans:
        for time_step in range(event_time, event_time + lookahead + 1):
            coord = plan_position_at_time(plan, time_step)
            if any(warehouse.distance(coord, failure_coord) <= radius for failure_coord in failure_positions):
                selected.add(plan.agent_id)
                break

    return selected


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
        suffix = suffix_by_id.get(plan.agent_id)
        if suffix is None:
            merged.append(plan)
            continue

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
    local_agent_ids: set[int] | None = None,
    station_mode: str,
    algorithm: str,
    stats: PlanningStats | None = None,
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

    local_agents = set(local_agent_ids) if local_agent_ids is not None else {plan.agent_id for plan in plans}
    fixed_plans: list[AgentPlan] = []

    for plan in plans:
        agent_id = plan.agent_id
        if agent_id not in local_agents:
            fixed_plans.append(fixed_suffix_plan(plan, event_time))
            continue

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
        stats=stats,
        deadline=deadline,
        fixed_plans=fixed_plans,
        planning_attempt_agent_count=len(plans),
    )


def apply_agent_delay_model(
    warehouse,
    plans: list[AgentPlan],
    metadata: ScenarioMetadata,
    station_mode: str,
    algorithm: str,
    stats: PlanningStats | None = None,
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
        injected_plans = inject_failure_delays(current_plans, event_time, new_failures)
        start_repair_time = event_time + 1
        wait_repaired_plans = repair_collisions_with_local_waits(
            injected_plans,
            warehouse,
            start_time=start_repair_time,
            new_failures=new_failures,
            deadline=deadline,
        )
        if wait_repaired_plans is not None:
            current_plans = wait_repaired_plans
        else:
            local_agent_ids: set[int] = set()
            if stats is not None:
                stats.note_failure_replan()
            last_local_error: RuntimeError | None = None
            base_radius = local_failure_replan_radius(warehouse)
            max_expansions = local_failure_replan_max_expansions(warehouse)

            local_agent_ids.update(new_failures)
            local_agent_ids.update(
                collision_agent_ids(
                    injected_plans,
                    start_time=start_repair_time,
                    focus_agent_ids=set(new_failures),
                )
            )

            for expansion_index in range(max_expansions + 1):
                if expansion_index > 0:
                    radius = base_radius * expansion_index
                    local_agent_ids.update(
                        local_replan_agent_ids(
                            warehouse,
                            injected_plans,
                            event_time,
                            new_failures,
                            radius=radius,
                        )
                    )

                try:
                    candidate_plans = merge_replanned_plans(
                        current_plans,
                        replan_after_failures(
                            warehouse,
                            current_plans,
                            event_time=event_time,
                            new_failures=new_failures,
                            local_agent_ids=local_agent_ids,
                            station_mode=station_mode,
                            algorithm=algorithm,
                            stats=stats,
                            deadline=deadline,
                        ),
                        event_time,
                    )
                except RuntimeError as exc:
                    last_local_error = exc
                    continue

                wait_repaired_candidate = repair_collisions_with_local_waits(
                    candidate_plans,
                    warehouse,
                    start_time=start_repair_time,
                    new_failures=new_failures,
                    deadline=deadline,
                )
                if wait_repaired_candidate is not None:
                    current_plans = wait_repaired_candidate
                    break

                colliding_agents = collision_agent_ids(candidate_plans, start_time=start_repair_time)
                if not colliding_agents:
                    current_plans = candidate_plans
                    break

                last_local_error = RuntimeError("Local replanning after agent delay produced a colliding plan.")
                local_agent_ids.update(colliding_agents)
            else:
                if last_local_error is not None:
                    raise last_local_error
                raise RuntimeError("Local replanning after agent delay failed.")

            if has_collisions(current_plans, start_time=start_repair_time):
                raise RuntimeError("Local replanning after agent delay produced a colliding plan.")

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
    stats: PlanningStats | None = None,
    deadline: float | None = None,
) -> tuple[list[AgentPlan], int, int]:
    normalized = failure_model.strip()
    if normalized not in FAILURE_MODEL_CHOICES:
        raise ValueError(f"Unsupported failure model: {failure_model}")
    if normalized == "None":
        return plans, 0, 0
    return apply_agent_delay_model(warehouse, plans, metadata, station_mode, algorithm, stats=stats, deadline=deadline)
