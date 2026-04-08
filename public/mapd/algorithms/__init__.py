from mapd.algorithms.astar import AStarAlgorithm
from mapd.algorithms.base import PathfindingAlgorithm
from mapd.algorithms.bfs import BFSAlgorithm
from mapd.algorithms.sipp import SIPPAlgorithm


def normalize_algorithm_name(name: str) -> str:
    key = name.strip().lower().replace(" ", "")
    if key in ("bfs", "breadthfirstsearch"):
        return "BFS"
    if key in ("a", "a*", "astar"):
        return "A*"
    if key in (
        "whca",
        "whca*",
        "windowedhierarchicalcooperativea*",
        "windowedhierarchicalcooperativeastar",
    ):
        return "WHCA*"
    if key in ("sipp", "safeinterval", "safeintervalpathplanning", "dijkstra", "dijkstras"):
        return "SIPP"
    raise ValueError(f"Unsupported pathfinding algorithm: {name}")


def get_algorithm(name: str) -> PathfindingAlgorithm:
    canonical_name = normalize_algorithm_name(name)
    if canonical_name == "BFS":
        return BFSAlgorithm()
    if canonical_name == "SIPP":
        return SIPPAlgorithm()
    return AStarAlgorithm()


__all__ = ["PathfindingAlgorithm", "get_algorithm", "normalize_algorithm_name"]
