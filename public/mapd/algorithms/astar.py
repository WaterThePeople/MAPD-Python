import heapq

from mapd.algorithms.base import SearchProblem, StateT, reconstruct_path


class AStarAlgorithm:
    name = "A*"

    def search(self, problem: SearchProblem[StateT]) -> list[StateT]:
        heuristic = problem.heuristic or (lambda _: 0)
        tie_breaker_value = problem.tie_breaker or (lambda _: 0)
        should_abort = problem.should_abort or (lambda: False)
        frontier: list[tuple[int, int, int, int, StateT]] = [
            (heuristic(problem.start), tie_breaker_value(problem.start), 0, 0, problem.start)
        ]
        came_from: dict[StateT, StateT] = {}
        cost_so_far: dict[StateT, int] = {problem.start: 0}
        tie_breaker = 0

        while frontier:
            if should_abort():
                raise RuntimeError("A* path search exceeded the time budget.")
            _, _, current_cost, _, current = heapq.heappop(frontier)
            if current_cost != cost_so_far.get(current):
                continue

            if problem.is_goal(current):
                return reconstruct_path(came_from, current)

            next_cost = current_cost + 1
            for next_state in problem.neighbors(current):
                known_cost = cost_so_far.get(next_state)
                if known_cost is not None and next_cost >= known_cost:
                    continue

                cost_so_far[next_state] = next_cost
                came_from[next_state] = current
                tie_breaker += 1
                priority = next_cost + heuristic(next_state)
                heapq.heappush(
                    frontier,
                    (priority, tie_breaker_value(next_state), next_cost, tie_breaker, next_state),
                )

        raise RuntimeError(f"{self.name} could not find a path.")
