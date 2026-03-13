from typing import Callable, Protocol

from mapd.models import Task

TravelTimesFn = Callable[[int, Task], tuple[int, int, int, int]]


class AssignmentStrategy(Protocol):
    name: str

    def select_agent(
        self,
        task: Task,
        agent_count: int,
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int: ...
