from __future__ import annotations

import heapq
from dataclasses import dataclass
from functools import lru_cache

from mapd.models import Coord
from mapd.warehouse import WarehouseMap


@dataclass(frozen=True)
class IntervalState:
    coord: Coord
    safe_from: int
    safe_to: int


class SIPPAlgorithm:
    name = "SIPP"

    def find_path(
        self,
        warehouse: WarehouseMap,
        reservations,
        start: Coord,
        start_time: int,
        goals: set[Coord],
        max_time: int,
        blocked_cells: set[Coord] | None = None,
        goal_available_after: dict[Coord, int] | None = None,
    ) -> list[Coord]:
        if blocked_cells is None:
            blocked_cells = set()
        if goal_available_after is None:
            goal_available_after = {}
        if not goals:
            raise RuntimeError("SIPP could not find a path because no goal cells were available.")

        @lru_cache(maxsize=None)
        def safe_intervals(coord: Coord) -> tuple[tuple[int, int], ...]:
            if coord in blocked_cells:
                return ()

            permanent_from = reservations.permanent.get(coord)
            blocked_times = sorted(
                time
                for time, coords in reservations.vertex.items()
                if time <= max_time and coord in coords and (permanent_from is None or time < permanent_from)
            )

            intervals: list[tuple[int, int]] = []
            current_start = 0
            for blocked_time in blocked_times:
                if blocked_time > current_start:
                    intervals.append((current_start, blocked_time - 1))
                current_start = blocked_time + 1

            safe_limit = max_time if permanent_from is None else min(max_time, permanent_from - 1)
            if current_start <= safe_limit:
                intervals.append((current_start, safe_limit))

            return tuple(intervals)

        def containing_interval(coord: Coord, time: int) -> IntervalState | None:
            for safe_from, safe_to in safe_intervals(coord):
                if safe_from <= time <= safe_to:
                    return IntervalState(coord, safe_from, safe_to)
            return None

        start_state = containing_interval(start, start_time)
        if start_state is None:
            raise RuntimeError(f"SIPP could not start from reserved cell {start} at time {start_time}.")

        def heuristic(coord: Coord) -> int:
            return min(warehouse.distance(coord, goal) for goal in goals)

        def coord_priority(coord: Coord) -> int:
            return -warehouse.coord_to_index(coord)

        frontier: list[tuple[int, int, int, int, IntervalState]] = [
            (heuristic(start), coord_priority(start), start_time, 0, start_state)
        ]
        arrival_times: dict[IntervalState, int] = {start_state: start_time}
        came_from: dict[IntervalState, IntervalState] = {}
        insertion_order = 0

        while frontier:
            _, _, current_time, _, current_state = heapq.heappop(frontier)
            if current_time != arrival_times.get(current_state):
                continue

            required_time = goal_available_after.get(current_state.coord, 0)
            if current_state.coord in goals and required_time <= current_state.safe_to:
                final_time = max(current_time, required_time)
                return self._reconstruct_path(came_from, arrival_times, current_state, final_time)

            for next_coord in warehouse.neighbors(current_state.coord):
                if next_coord in blocked_cells:
                    continue

                for safe_from, safe_to in safe_intervals(next_coord):
                    earliest_arrival = safe_from
                    if next_coord in goals:
                        earliest_arrival = max(earliest_arrival, goal_available_after.get(next_coord, 0))

                    departure_time = max(current_time, earliest_arrival - 1)
                    if departure_time > current_state.safe_to:
                        continue

                    while departure_time <= current_state.safe_to and departure_time + 1 <= safe_to:
                        if reservations.is_edge_conflict(current_state.coord, next_coord, departure_time):
                            departure_time += 1
                            continue

                        arrival_time = departure_time + 1
                        next_state = IntervalState(next_coord, safe_from, safe_to)
                        known_arrival = arrival_times.get(next_state)
                        if known_arrival is None or arrival_time < known_arrival:
                            arrival_times[next_state] = arrival_time
                            came_from[next_state] = current_state
                            insertion_order += 1
                            priority = arrival_time + heuristic(next_coord)
                            heapq.heappush(
                                frontier,
                                (priority, coord_priority(next_coord), arrival_time, insertion_order, next_state),
                            )
                        break

        raise RuntimeError(f"{self.name} could not find a path.")

    def _reconstruct_path(
        self,
        came_from: dict[IntervalState, IntervalState],
        arrival_times: dict[IntervalState, int],
        end_state: IntervalState,
        final_time: int,
    ) -> list[Coord]:
        states = [end_state]
        current_state = end_state

        while current_state in came_from:
            current_state = came_from[current_state]
            states.append(current_state)

        states.reverse()
        path = [states[0].coord]

        for previous_state, next_state in zip(states, states[1:]):
            previous_arrival = arrival_times[previous_state]
            next_arrival = arrival_times[next_state]
            wait_steps = next_arrival - previous_arrival - 1
            if wait_steps > 0:
                path.extend([previous_state.coord] * wait_steps)
            path.append(next_state.coord)

        end_arrival = arrival_times[end_state]
        if final_time > end_arrival:
            path.extend([end_state.coord] * (final_time - end_arrival))

        return path
