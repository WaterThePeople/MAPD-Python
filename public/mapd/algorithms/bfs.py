from collections import deque

from mapd.algorithms.base import SearchProblem, StateT, reconstruct_path


class BFSAlgorithm:
    name = "BFS"

    def search(self, problem: SearchProblem[StateT]) -> list[StateT]:
        frontier = deque([problem.start])
        visited = {problem.start}
        came_from: dict[StateT, StateT] = {}

        while frontier:
            current = frontier.popleft()
            if problem.is_goal(current):
                return reconstruct_path(came_from, current)

            for next_state in problem.neighbors(current):
                if next_state in visited:
                    continue

                visited.add(next_state)
                came_from[next_state] = current
                frontier.append(next_state)

        raise RuntimeError(f"{self.name} could not find a path.")
