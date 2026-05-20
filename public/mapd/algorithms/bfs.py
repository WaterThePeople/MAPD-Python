from collections import deque

from mapd.algorithms.base import SearchProblem, StateT, reconstruct_path


BFS_MAX_STATES = 500_000


class BFSAlgorithm:
    name = "BFS"

    def search(self, problem: SearchProblem[StateT]) -> list[StateT]:
        should_abort = problem.should_abort or (lambda: False)
        frontier = deque([problem.start])
        visited = {problem.start}
        came_from: dict[StateT, StateT] = {}
        expanded_states = 0

        while frontier:
            if should_abort():
                raise RuntimeError("BFS path search exceeded the time budget.")
            current = frontier.popleft()
            expanded_states += 1
            if expanded_states > BFS_MAX_STATES:
                raise RuntimeError(
                    f"BFS path search exceeded the state limit ({BFS_MAX_STATES} expanded states)."
                )

            if problem.is_goal(current):
                return reconstruct_path(came_from, current)

            for next_state in problem.neighbors(current):
                if next_state in visited:
                    continue
                if len(visited) >= BFS_MAX_STATES:
                    raise RuntimeError(
                        f"BFS path search exceeded the state limit ({BFS_MAX_STATES} discovered states)."
                    )

                visited.add(next_state)
                came_from[next_state] = current
                frontier.append(next_state)

        raise RuntimeError(f"{self.name} could not find a path.")
