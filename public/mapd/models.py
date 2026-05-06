from dataclasses import dataclass, field


Coord = tuple[int, int]
FAILURE_MODEL_CHOICES = ("None", "AgentDelay")


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
    delayed_times: set[int] = field(default_factory=set)
    failure_start_times: set[int] = field(default_factory=set)


@dataclass
class PlanningStats:
    replans: int = 0

    def note_replan(self) -> None:
        self.replans += 1


@dataclass(frozen=True)
class ScenarioMetadata:
    scenario_id: str | None = None
    seed: int | None = None
    load_factor: float | None = None
    capacity_model: str | None = None
    capacity_reserve: float | None = None
    capacity_steps_per_task: float | None = None
    max_open_tasks_on_shelves: int | None = None
    set_assignment_policy: str | None = None
    influx: str | None = None
    lambda_value: float | None = None
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
    max_simulation_time_seconds: int | None = None
    failure_probability: float | None = None
    failure_duration_min: int | None = None
    failure_duration_max: int | None = None
    failure_seed: int | None = None


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
    failure_models: list[str]
    metadata: ScenarioMetadata


@dataclass(frozen=True)
class ScenarioVariant:
    layout_id: int
    layout_type: str
    mode: str
    station_mode: str
    strategy: str
    algorithm: str
    failure_model: str


@dataclass
class VariantExecutionResult:
    status: str
    details: str | None
    makespan: int | None
    plans: list[AgentPlan] | None
    collisions: int | None
    replans: int
    simulation_time_seconds: float
    failure_count: int | None = None
    failure_delay_steps: int | None = None
