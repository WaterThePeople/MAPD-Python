from mapd.models import Task
from mapd.strategy.base import TravelTimesFn


class GreedyCostStrategy:
    name = "GreedyCost"

    def select_agent(
        self,
        task: Task,
        candidate_agent_ids: list[int],
        availability: dict[int, int],
        travel_times: TravelTimesFn,
    ) -> int:
        best_agent = None
        best_key = None
        for candidate in candidate_agent_ids:
            start_time, arrival_time, finish_time, _ = travel_times(candidate, task)
            key = (finish_time, arrival_time, start_time, candidate)
            if best_key is None or key < best_key:
                best_key = key
                best_agent = candidate
        return best_agent
