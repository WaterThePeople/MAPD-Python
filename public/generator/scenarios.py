from __future__ import annotations

import heapq
import math
import random

from mapd.models import Task

from .constants import (
    DEFAULT_ALGORITHM_CHOICES,
    DEFAULT_CAPACITY_MODEL,
    DEFAULT_HOTSPOT_SHELF_SHARE,
    DEFAULT_HOTSPOT_TASK_SHARE,
    DEFAULT_MODE_CHOICES,
    DEFAULT_SET_ASSIGNMENT_POLICY,
    DEFAULT_STATION_CHOICES,
    DEFAULT_STRATEGY_CHOICES,
    DEFAULT_TYPE_CHOICES,
    DEFAULT_WAVE_ZONE,
    INFLUX_CHOICES,
    SPATIAL_DISTRIBUTIONS,
)
from .definitions import BatchConfig, GeneratedScenario, LayoutContext, ScenarioConfig
from .releases import choose_agent, generate_release_times
from .spatial import build_spatial_weights, weighted_choice


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def title_join(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


def serialize_scenario(config: ScenarioConfig, tasks: list[Task]) -> str:
    lines = [
        f"ID: {config.scenario_id}",
        f"Seed: {config.seed}",
        "",
        f"Size: {config.size.label}",
        f"Layout: [{config.layout_id}]",
        f"Type: {title_join(DEFAULT_TYPE_CHOICES)}",
        "",
        f"Mode: {title_join(DEFAULT_MODE_CHOICES)}",
        f"Station: {title_join(DEFAULT_STATION_CHOICES)}",
        f"Strategy: {title_join(DEFAULT_STRATEGY_CHOICES)}",
        f"Algorithm: {title_join(DEFAULT_ALGORITHM_CHOICES)}",
        "",
        f"Agents: {config.agents}",
        f"Tasks: {len(tasks)}",
        f"DurationSeconds: {config.duration_seconds}",
        f"StepSeconds: {config.step_seconds}",
        f"TimeLimitSteps: {config.time_limit_steps}",
        f"TasksPerAgentPerHour: {format_number(config.tasks_per_agent_per_hour)}",
        "",
        f"Influx: {config.influx}",
        f"LambdaPerHour: {format_number(config.lambda_per_hour)}",
        f"BurstAmount: {config.burst_amount}",
        f"BurstStartStep: {config.burst_start_step}",
        f"BurstDurationSteps: {config.burst_duration_steps}",
        f"BurstAmplitude: {config.burst_amplitude:.2f}",
        "",
        f"SpatialDistribution: {config.spatial_distribution}",
        f"HotspotShelfShare: {config.hotspot_shelf_share:.2f}",
        f"HotspotTaskShare: {config.hotspot_task_share:.2f}",
        f"WaveZone: {config.wave_zone}",
        f"WaveRadius: {config.wave_radius}",
        "",
        "DeadlineSlackPolicy: DensityBased",
        f"DeadlineSlack: {config.deadline_slack:.2f}",
        "",
        f"MaxReplans: {config.max_replans}",
        "",
        "Task Agent Shelf Time Deadline",
    ]
    lines.extend(
        f"{task.task_id} {task.agent_id} {task.shelf_index} {task.release_time} {task.deadline}"
        for task in tasks
    )
    return "\n".join(lines) + "\n"


def generate_tasks(
    config: ScenarioConfig,
    layout: LayoutContext,
    rng: random.Random,
) -> list[Task]:
    release_times = generate_release_times(config, rng)
    spatial_weights = build_spatial_weights(
        layout.warehouse,
        layout.shelf_descriptors,
        config.spatial_distribution,
        rng,
        hotspot_shelf_share=config.hotspot_shelf_share,
        hotspot_task_share=config.hotspot_task_share,
        wave_zone=config.wave_zone,
        wave_radius=config.wave_radius,
    )
    shelf_release_times = [0] * layout.warehouse.shelf_count
    assignment_counts = [0] * config.agents
    agent_available_times = [0] * config.agents
    active_open_tasks: list[int] = []
    tasks: list[Task] = []
    last_release_time = 0

    for task_index, nominal_release in enumerate(release_times):
        agent_id = choose_agent(config.set_assignment_policy, task_index, assignment_counts, rng)
        release_time = max(nominal_release, last_release_time)

        while True:
            while active_open_tasks and active_open_tasks[0] <= release_time:
                heapq.heappop(active_open_tasks)

            if len(active_open_tasks) >= config.max_open_tasks_on_shelves:
                release_time = active_open_tasks[0]
                if release_time >= config.time_limit_steps:
                    raise ValueError(
                        "The selected workload exceeds MaxOpenTasksOnShelves within the time horizon. "
                        "Reduce the number of tasks or increase the maximum simulation time."
                    )
                continue

            available_shelves = [
                descriptor.shelf_index
                for descriptor in layout.shelf_descriptors
                if shelf_release_times[descriptor.shelf_index] <= release_time
                and layout.distances_by_agent[agent_id][descriptor.shelf_index] is not None
            ]

            if available_shelves:
                break

            release_time = min(shelf_release_times)
            if release_time >= config.time_limit_steps:
                raise ValueError(
                    "The selected workload does not fit within the time horizon while preserving one active task per shelf. "
                    "Reduce the number of tasks or increase the maximum simulation time."
                )

        shelf_index = weighted_choice(available_shelves, spatial_weights, rng)
        distance_to_pickup = layout.distances_by_agent[agent_id][shelf_index]
        if distance_to_pickup is None:
            raise ValueError(f"Shelf {shelf_index} is unreachable from agent {agent_id}'s home station.")

        predicted_start = max(release_time, agent_available_times[agent_id])
        predicted_pickup_time = predicted_start + distance_to_pickup
        base_service_time = distance_to_pickup * 2
        deadline = min(
            config.time_limit_steps,
            release_time + max(1, math.ceil(base_service_time * (1.0 + config.deadline_slack))),
        )
        last_release_time = release_time

        tasks.append(
            Task(
                task_id=0,
                agent_id=agent_id,
                shelf_index=shelf_index,
                release_time=release_time,
                deadline=deadline,
            )
        )

        assignment_counts[agent_id] += 1
        agent_available_times[agent_id] = predicted_start + base_service_time
        shelf_release_times[shelf_index] = predicted_pickup_time
        heapq.heappush(active_open_tasks, predicted_pickup_time)

    ordered_tasks = sorted(tasks, key=lambda task: (task.release_time, task.agent_id, task.shelf_index))
    return [
        Task(
            task_id=index,
            agent_id=task.agent_id,
            shelf_index=task.shelf_index,
            release_time=task.release_time,
            deadline=task.deadline,
        )
        for index, task in enumerate(ordered_tasks, start=1)
    ]


def build_scenario_config(
    batch: BatchConfig,
    layout_id: int,
    influx: str,
    spatial_distribution: str,
    file_index: int,
) -> ScenarioConfig:
    scenario_id = f"{batch.folder_name}-{file_index}"
    return ScenarioConfig(
        size_key=batch.size_key,
        layout_id=layout_id,
        agents=batch.agents,
        task_count=batch.task_count,
        duration_seconds=batch.duration_seconds,
        step_seconds=batch.step_seconds,
        tasks_per_agent_per_hour=batch.tasks_per_agent_per_hour,
        capacity_model=DEFAULT_CAPACITY_MODEL,
        capacity_reserve=batch.capacity_reserve,
        capacity_steps_per_task=batch.capacity_steps_per_task,
        load_factor=batch.load_factor,
        influx=influx,
        spatial_distribution=spatial_distribution,
        seed=batch.seed,
        scenario_id=scenario_id,
        output_path=batch.scenario_directory / f"{file_index}.txt",
        lambda_per_hour=batch.lambda_per_hour,
        burst_amount=batch.burst_amount,
        burst_start_step=batch.burst_start_step,
        burst_duration_steps=batch.burst_duration_steps,
        burst_amplitude=batch.burst_amplitude,
        hotspot_shelf_share=DEFAULT_HOTSPOT_SHELF_SHARE,
        hotspot_task_share=DEFAULT_HOTSPOT_TASK_SHARE,
        wave_zone=DEFAULT_WAVE_ZONE,
        wave_radius=batch.wave_radius,
        set_assignment_policy=DEFAULT_SET_ASSIGNMENT_POLICY,
        max_replans=batch.max_replans,
        max_open_tasks_on_shelves=batch.max_open_tasks_on_shelves,
    )


def generate_scenario(config: ScenarioConfig, layout: LayoutContext) -> GeneratedScenario:
    rng = random.Random(config.seed)
    tasks = generate_tasks(config, layout, rng)
    content = serialize_scenario(config, tasks)
    return GeneratedScenario(
        file_index=int(config.output_path.stem),
        config=config,
        content=content,
    )


def generate_batch(batch: BatchConfig, layout_contexts: dict[int, LayoutContext]) -> list[GeneratedScenario]:
    if batch.scenario_directory.exists() and not batch.scenario_directory.is_dir():
        raise ValueError(
            f"Output path '{batch.scenario_directory}' already exists and is not a directory."
        )
    if batch.scenario_directory.exists() and any(batch.scenario_directory.iterdir()):
        raise ValueError(
            f"Output directory '{batch.scenario_directory}' already exists and is not empty. "
            "Use a different seed or remove the folder before generating again."
        )

    generated: list[GeneratedScenario] = []
    file_index = 0

    for layout_id in batch.layout_ids:
        layout = layout_contexts[layout_id]
        for influx in INFLUX_CHOICES:
            for spatial_distribution in SPATIAL_DISTRIBUTIONS:
                scenario_config = build_scenario_config(
                    batch,
                    layout_id=layout_id,
                    influx=influx,
                    spatial_distribution=spatial_distribution,
                    file_index=file_index,
                )
                generated.append(generate_scenario(scenario_config, layout))
                file_index += 1

    return generated


def save_batch(scenarios: list[GeneratedScenario]) -> None:
    if not scenarios:
        return

    target_directory = scenarios[0].config.output_path.parent
    target_directory.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        scenario.config.output_path.write_text(scenario.content, encoding="utf-8")
