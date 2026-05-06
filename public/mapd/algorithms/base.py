from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Protocol, TypeVar


StateT = TypeVar("StateT")

GoalFn = Callable[[StateT], bool]
HeuristicFn = Callable[[StateT], int]
NeighborFn = Callable[[StateT], Iterable[StateT]]
TieBreakerFn = Callable[[StateT], int]
AbortFn = Callable[[], bool]


@dataclass(frozen=True)
class SearchProblem(Generic[StateT]):
    start: StateT
    is_goal: GoalFn[StateT]
    neighbors: NeighborFn[StateT]
    heuristic: HeuristicFn[StateT] | None = None
    tie_breaker: TieBreakerFn[StateT] | None = None
    should_abort: AbortFn | None = None


class PathfindingAlgorithm(Protocol[StateT]):
    name: str

    def search(self, problem: SearchProblem[StateT]) -> list[StateT]: ...


def reconstruct_path(came_from: dict[StateT, StateT], end_state: StateT) -> list[StateT]:
    state = end_state
    path = [state]

    while state in came_from:
        state = came_from[state]
        path.append(state)

    path.reverse()
    return path
