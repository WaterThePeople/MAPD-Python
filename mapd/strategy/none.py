from mapd.models import Task
from mapd.strategy.base import TravelTimesFn


class NoneStrategy:
    name = "None"

    def select_agent(
        self,
        task: Task,
        agent_count: int,
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int:
        raise RuntimeError("Strategy 'None' cannot assign tasks when Mode is Available.")
