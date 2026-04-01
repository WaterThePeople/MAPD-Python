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
        candidate_agent_ids: list[int],
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int:
        eligible = set(candidate_agent_ids)
        for offset in range(self._agent_count):
            candidate = (self._next_agent + offset) % self._agent_count
            if candidate in eligible:
                self._next_agent = (candidate + 1) % self._agent_count
                return candidate

        raise RuntimeError("RobinStrategy could not find an eligible agent.")
