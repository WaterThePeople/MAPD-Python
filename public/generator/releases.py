from __future__ import annotations

import random

from .definitions import ScenarioConfig


def choose_agent(
    policy: str,
    task_index: int,
    assignment_counts: list[int],
    rng: random.Random,
) -> int:
    if policy == "RoundRobin":
        return task_index % len(assignment_counts)
    if policy == "Balanced":
        min_count = min(assignment_counts)
        candidates = [agent_id for agent_id, count in enumerate(assignment_counts) if count == min_count]
        return candidates[0]
    return rng.randrange(len(assignment_counts))


def generate_random_release_times(task_count: int, release_horizon_steps: int, rng: random.Random) -> list[int]:
    return sorted(rng.randrange(release_horizon_steps) for _ in range(task_count))


def generate_poisson_release_times(task_count: int, release_horizon_steps: int, rng: random.Random) -> list[int]:
    if task_count <= 1 or release_horizon_steps <= 1:
        return [0] * task_count

    rate_per_step = task_count / release_horizon_steps
    event_times = []
    current = 0.0
    for _ in range(task_count):
        current += rng.expovariate(rate_per_step)
        event_times.append(current)

    latest = event_times[-1]
    if latest <= 0:
        return [0] * task_count

    scale = max(1, release_horizon_steps - 1) / latest
    return sorted(min(release_horizon_steps - 1, int(round(event_time * scale))) for event_time in event_times)


def sample_outside_burst(
    task_count: int,
    burst_start_step: int,
    burst_end_step: int,
    release_horizon_steps: int,
    rng: random.Random,
) -> list[int]:
    outside_ranges: list[tuple[int, int]] = []
    if burst_start_step > 0:
        outside_ranges.append((0, burst_start_step))
    if burst_end_step < release_horizon_steps:
        outside_ranges.append((burst_end_step, release_horizon_steps))
    if not outside_ranges:
        return [min(release_horizon_steps - 1, burst_start_step)] * task_count

    weights = [end - start for start, end in outside_ranges]
    releases = []
    for _ in range(task_count):
        start, end = rng.choices(outside_ranges, weights=weights, k=1)[0]
        releases.append(rng.randrange(start, end))
    return releases


def sample_inside_burst(
    task_count: int,
    burst_start_step: int,
    burst_duration_steps: int,
    burst_amplitude: float,
    rng: random.Random,
) -> list[int]:
    if task_count <= 0:
        return []
    if burst_duration_steps <= 1:
        return [burst_start_step] * task_count

    releases = []
    for _ in range(task_count):
        if burst_amplitude <= 1.0:
            offset = rng.randrange(burst_duration_steps)
        else:
            ratio = rng.betavariate(burst_amplitude, burst_amplitude)
            offset = min(burst_duration_steps - 1, int(round(ratio * (burst_duration_steps - 1))))
        releases.append(burst_start_step + offset)
    return releases


def generate_burst_release_times(config: ScenarioConfig, rng: random.Random) -> list[int]:
    if config.burst_amount <= 0 or config.burst_duration_steps <= 0:
        return generate_random_release_times(config.task_count, config.release_horizon_steps, rng)

    burst_task_count = min(config.task_count, config.burst_amount)
    regular_task_count = config.task_count - burst_task_count
    burst_end_step = config.burst_start_step + config.burst_duration_steps

    releases = sample_inside_burst(
        burst_task_count,
        config.burst_start_step,
        config.burst_duration_steps,
        config.burst_amplitude,
        rng,
    )
    releases.extend(
        sample_outside_burst(
            regular_task_count,
            config.burst_start_step,
            burst_end_step,
            config.release_horizon_steps,
            rng,
        )
    )
    return sorted(releases)


def generate_release_times(config: ScenarioConfig, rng: random.Random) -> list[int]:
    if config.influx == "Random":
        return generate_random_release_times(config.task_count, config.release_horizon_steps, rng)
    if config.influx == "Poisson":
        return generate_poisson_release_times(config.task_count, config.release_horizon_steps, rng)
    return generate_burst_release_times(config, rng)
