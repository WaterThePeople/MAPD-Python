from dataclasses import dataclass


Coord = tuple[int, int]


@dataclass(frozen=True)
class Task:
    task_id: int
    agent_id: int
    location_index: int
    release_time: int


@dataclass
class AgentPlan:
    agent_id: int
    color: tuple[int, int, int]
    home: Coord
    home_index: int
    path: list[Coord]
    tasks: list[Task]
    pickup_times: dict[int, int]
