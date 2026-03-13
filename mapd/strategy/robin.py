from mapd.models import Task
from mapd.strategy.base import TravelTimesFn


class RobinStrategy:
    name = "Robin"

    def __init__(self, agent_count: int) -> None:
        self._agent_count = agent_count
        self._next_agent = 0

    def select_agent(
        self,
        task: Task,
        agent_count: int,
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int:
        if self._agent_count != agent_count:
            self._agent_count = agent_count
            self._next_agent = 0
        agent_id = self._next_agent
        self._next_agent = (self._next_agent + 1) % agent_count
        return agent_id
