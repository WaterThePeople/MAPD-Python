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


@dataclass(frozen=True)
class ScenarioDefinition:
    agent_count: int
    tasks: list[Task]
    layout_ids: list[int]
    layout_types: list[str]
    modes: list[str]
    station_modes: list[str]
    strategies: list[str]
    algorithms: list[str]


@dataclass(frozen=True)
class ScenarioVariant:
    layout_id: int
    layout_type: str
    mode: str
    station_mode: str
    strategy: str
    algorithm: str
