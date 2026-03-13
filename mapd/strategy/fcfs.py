from mapd.models import Task
from mapd.strategy.base import TravelTimesFn


class FCFSStrategy:
    name = "FCFS"

    def select_agent(
        self,
        task: Task,
        agent_count: int,
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int:
        return min(
            range(agent_count),
            key=lambda candidate: (max(availability[candidate], task.release_time), candidate),
        )
