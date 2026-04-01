from dataclasses import dataclass


Coord = tuple[int, int]


@dataclass(frozen=True)
class Task:
    task_id: int
    agent_id: int
    shelf_index: int
    release_time: int
    deadline: int | None


@dataclass
class AgentPlan:
    agent_id: int
    color: tuple[int, int, int]
    home: Coord
    home_index: int
    path: list[Coord]
    tasks: list[Task]
    pickup_times: dict[int, int]
    completion_times: dict[int, int]
    missed_deadlines: list[int]


@dataclass
class PlanningStats:
    replans: int = 0
    max_replans: int | None = None

    def note_replan(self) -> None:
        if self.max_replans is not None and self.replans >= self.max_replans:
            raise PlanningLimitExceeded(f"Exceeded scenario replan budget ({self.max_replans}).")
        self.replans += 1


class PlanningLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ScenarioMetadata:
    scenario_id: str | None = None
    seed: int | None = None
    hours: float | None = None
    step_seconds: int | None = None
    time_limit_steps: int | None = None
    load_factor: float | None = None
    tasks_per_agent_per_hour: float | None = None
    max_open_tasks_on_shelves: int | None = None
    set_assignment_policy: str | None = None
    influx: str | None = None
    lambda_per_hour: float | None = None
    burst_amount: int | None = None
    burst_start_step: int | None = None
    burst_duration_steps: int | None = None
    burst_amplitude: float | None = None
    spatial_distribution: str | None = None
    hotspot_shelf_share: float | None = None
    hotspot_task_share: float | None = None
    wave_zone: str | None = None
    wave_radius: int | None = None
    deadline_slack_policy: str | None = None
    deadline_slack: float | None = None
    max_replans: int | None = None


@dataclass(frozen=True)
class ScenarioDefinition:
    agent_count: int
    tasks: list[Task]
    layout_size: str | None
    layout_ids: list[int]
    layout_types: list[str]
    modes: list[str]
    station_modes: list[str]
    strategies: list[str]
    algorithms: list[str]
    metadata: ScenarioMetadata


@dataclass(frozen=True)
class ScenarioVariant:
    layout_id: int
    layout_type: str
    mode: str
    station_mode: str
    strategy: str
    algorithm: str


@dataclass
class VariantExecutionResult:
    status: str
    details: str | None
    makespan: int | None
    plans: list[AgentPlan] | None
    collisions: int | None
    replans: int
    simulation_time_seconds: float
