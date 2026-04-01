from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from mapd.models import Coord
from mapd.warehouse import WarehouseMap

from .constants import (
    DEFAULT_BURST_AMPLITUDE,
    DEFAULT_BURST_DURATION_SHARE,
    DEFAULT_BURST_SHARE,
    DEFAULT_BURST_START_SHARE,
    SIZE_PROFILES,
    SizeProfile,
)


@dataclass(frozen=True)
class BatchConfig:
    agents: int
    layout_ids: tuple[int, ...]
    time_limit_steps: int
    step_seconds: int
    tasks_per_agent_per_hour: float
    capacity_steps_per_task: float
    capacity_reserve: float
    seed: int
    output_root: Path
    size_key: str

    @property
    def size(self) -> SizeProfile:
        return SIZE_PROFILES[self.size_key]

    @property
    def hours(self) -> float:
        return self.duration_seconds / 3600

    @property
    def duration_seconds(self) -> int:
        return self.time_limit_steps * self.step_seconds

    @property
    def task_count(self) -> int:
        expected_task_count = self.tasks_per_agent_per_hour * self.agents * self.hours
        return max(1, int(math.floor(expected_task_count)))

    @property
    def load_factor(self) -> float:
        raw_capacity = self.raw_tasks_per_agent_per_hour * self.agents * self.hours
        if raw_capacity <= 0:
            return 0.0
        return self.task_count / raw_capacity

    @property
    def raw_tasks_per_agent_per_hour(self) -> float:
        return (3600 / self.step_seconds) / self.capacity_steps_per_task

    @property
    def deadline_slack(self) -> float:
        density = self.agents / self.size.max_agents
        if density <= (1 / 3):
            return 0.20
        if density <= (2 / 3):
            return 0.35
        return 0.50

    @property
    def release_horizon_steps(self) -> int:
        deadline_buffer = max(1, int(math.ceil(self.capacity_steps_per_task * (1.0 + self.deadline_slack))))
        return max(1, self.time_limit_steps - deadline_buffer)

    @property
    def max_replans(self) -> int:
        return self.size.max_replans

    @property
    def max_open_tasks_on_shelves(self) -> int:
        return self.size.shelf_count

    @property
    def lambda_per_hour(self) -> float:
        return self.task_count / self.hours

    @property
    def burst_amount(self) -> int:
        return max(1, int(round(self.task_count * DEFAULT_BURST_SHARE)))

    @property
    def burst_start_step(self) -> int:
        if self.release_horizon_steps <= 1:
            return 0
        return min(
            self.release_horizon_steps - 1,
            max(0, int(round(self.release_horizon_steps * DEFAULT_BURST_START_SHARE))),
        )

    @property
    def burst_duration_steps(self) -> int:
        return max(1, int(round(self.release_horizon_steps * DEFAULT_BURST_DURATION_SHARE)))

    @property
    def burst_amplitude(self) -> float:
        return DEFAULT_BURST_AMPLITUDE

    @property
    def wave_radius(self) -> int:
        return self.size.default_wave_radius

    @property
    def folder_name(self) -> str:
        return f"{self.agents}-{self.task_count}-{self.size_key}-{self.time_limit_steps}-{self.seed}"

    @property
    def scenario_directory(self) -> Path:
        return self.output_root / self.folder_name


@dataclass(frozen=True)
class ScenarioConfig:
    size_key: str
    layout_id: int
    agents: int
    task_count: int
    duration_seconds: int
    step_seconds: int
    tasks_per_agent_per_hour: float
    capacity_model: str
    capacity_reserve: float
    capacity_steps_per_task: float
    load_factor: float
    influx: str
    spatial_distribution: str
    seed: int
    scenario_id: str
    output_path: Path
    lambda_per_hour: float
    burst_amount: int
    burst_start_step: int
    burst_duration_steps: int
    burst_amplitude: float
    hotspot_shelf_share: float
    hotspot_task_share: float
    wave_zone: str
    wave_radius: int
    set_assignment_policy: str
    max_replans: int
    max_open_tasks_on_shelves: int

    @property
    def size(self) -> SizeProfile:
        return SIZE_PROFILES[self.size_key]

    @property
    def time_limit_steps(self) -> int:
        return self.duration_seconds // self.step_seconds

    @property
    def hours(self) -> float:
        return self.duration_seconds / 3600

    @property
    def deadline_slack(self) -> float:
        density = self.agents / self.size.max_agents
        if density <= (1 / 3):
            return 0.20
        if density <= (2 / 3):
            return 0.35
        return 0.50

    @property
    def release_horizon_steps(self) -> int:
        deadline_buffer = max(1, int(math.ceil(self.capacity_steps_per_task * (1.0 + self.deadline_slack))))
        return max(1, self.time_limit_steps - deadline_buffer)


@dataclass(frozen=True)
class ShelfDescriptor:
    shelf_index: int
    coord: Coord
    pickup_positions: tuple[Coord, ...]
    station_distance: int


@dataclass(frozen=True)
class LayoutContext:
    size_key: str
    layout_id: int
    warehouse: WarehouseMap
    shelf_descriptors: list[ShelfDescriptor]
    distances_by_agent: list[list[int | None]]


@dataclass(frozen=True)
class GeneratedScenario:
    file_index: int
    config: ScenarioConfig
    content: str
