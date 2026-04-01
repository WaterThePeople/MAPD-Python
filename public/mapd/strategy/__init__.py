from mapd.strategy.base import AssignmentStrategy
from mapd.strategy.fcfs import FCFSStrategy
from mapd.strategy.greedy_cost import GreedyCostStrategy
from mapd.strategy.none import NoneStrategy
from mapd.strategy.robin import RobinStrategy


def get_strategy(name: str, agent_count: int) -> AssignmentStrategy:
    key = name.strip().lower()
    if key == "fcfs":
        return FCFSStrategy()
    if key in ("greedy", "greedycost"):
        return GreedyCostStrategy()
    if key == "robin":
        return RobinStrategy(agent_count)
    if key == "none":
        return NoneStrategy()
    raise ValueError(f"Unsupported assignment strategy: {name}")


__all__ = ["AssignmentStrategy", "get_strategy"]
