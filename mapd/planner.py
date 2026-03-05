from __future__ import annotations

import colorsys
import heapq
import math
from collections import defaultdict
from typing import Iterable

from mapd.models import AgentPlan, Coord, Task
from mapd.warehouse import WarehouseMap


class ReservationTable:
    def __init__(self) -> None:
        self.vertex: dict[int, set[Coord]] = defaultdict(set)
        self.edge: dict[int, set[tuple[Coord, Coord]]] = defaultdict(set)
        self.permanent: dict[Coord, int] = {}
        self.latest_time = 0

    def is_vertex_reserved(self, coord: Coord, time: int) -> bool:
        if coord in self.vertex.get(time, set()):
            return True

        permanent_from = self.permanent.get(coord)
        return permanent_from is not None and time >= permanent_from

    def is_edge_conflict(self, src: Coord, dst: Coord, time: int) -> bool:
        return (dst, src) in self.edge.get(time, set())

    def reserve_path(self, path: list[Coord]) -> None:
        for time, coord in enumerate(path):
            self.vertex[time].add(coord)
        for time in range(len(path) - 1):
            self.edge[time].add((path[time], path[time + 1]))

        final_coord = path[-1]
        final_time = len(path) - 1
        existing = self.permanent.get(final_coord)
        self.permanent[final_coord] = final_time if existing is None else min(existing, final_time)
        self.latest_time = max(self.latest_time, final_time)


def build_color_palette(count: int) -> list[tuple[int, int, int]]:
    if count <= 0:
        return []

    palette: list[tuple[int, int, int]] = []
    for idx in range(count):
        hue = idx / max(count, 1)
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.75, 0.92)
        palette.append((int(red * 255), int(green * 255), int(blue * 255)))
    return palette


def reconstruct_path(came_from: dict[tuple[Coord, int], tuple[Coord, int]], end_state: tuple[Coord, int]) -> list[Coord]:
    state = end_state
    path: list[Coord] = [state[0]]

    while state in came_from:
        state = came_from[state]
        path.append(state[0])

    path.reverse()
    return path


def heuristic(coord: Coord, goals: Iterable[Coord]) -> int:
    row, col = coord
    return min(abs(row - goal_row) + abs(col - goal_col) for goal_row, goal_col in goals)


def find_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    blocked_cells: set[Coord] | None = None,
) -> list[Coord]:
    blocked_cells = blocked_cells or set()
    max_time = start_time + warehouse.cell_count * 8 + reservations.latest_time + 20
    frontier: list[tuple[int, int, Coord, int]] = []
    heapq.heappush(frontier, (heuristic(start, goals), 0, start, start_time))

    came_from: dict[tuple[Coord, int], tuple[Coord, int]] = {}
    best_cost: dict[tuple[Coord, int], int] = {(start, start_time): 0}

    while frontier:
        _, cost_so_far, current, time = heapq.heappop(frontier)
        state = (current, time)
        if cost_so_far != best_cost.get(state):
            continue

        if current in goals and not reservations.is_vertex_reserved(current, time):
            return reconstruct_path(came_from, state)

        if time >= max_time:
            continue

        for next_coord in [current, *warehouse.neighbors(current)]:
            if next_coord in blocked_cells:
                continue

            next_time = time + 1
            if reservations.is_vertex_reserved(next_coord, next_time):
                continue
            if reservations.is_edge_conflict(current, next_coord, time):
                continue

            next_cost = cost_so_far + 1
            next_state = (next_coord, next_time)
            if next_cost >= best_cost.get(next_state, math.inf):
                continue

            best_cost[next_state] = next_cost
            came_from[next_state] = state
            priority = next_cost + heuristic(next_coord, goals)
            heapq.heappush(frontier, (priority, next_cost, next_coord, next_time))

    raise RuntimeError(f"Could not find a collision-free path from {start} at time {start_time}.")


def merge_segments(base_path: list[Coord], segment: list[Coord]) -> list[Coord]:
    if not base_path:
        return segment[:]
    return [*base_path, *segment[1:]]


def assign_home_stations(warehouse: WarehouseMap, agent_count: int) -> dict[int, Coord]:
    if len(warehouse.stations) < agent_count:
        raise ValueError(
            f"Map contains only {len(warehouse.stations)} stations, but scenario requires {agent_count} agents."
        )

    ordered_stations = sorted(warehouse.stations, key=warehouse.coord_to_index)
    return {agent_id: ordered_stations[agent_id] for agent_id in range(agent_count)}


def build_agent_plans(warehouse: WarehouseMap, agent_count: int, tasks: list[Task]) -> list[AgentPlan]:
    for task in tasks:
        if task.agent_id < 0 or task.agent_id >= agent_count:
            raise ValueError(f"Task {task.task_id} references unknown agent {task.agent_id}.")

    tasks_by_agent: dict[int, list[Task]] = {agent_id: [] for agent_id in range(agent_count)}
    for task in sorted(tasks, key=lambda item: item.task_id):
        tasks_by_agent[task.agent_id].append(task)

    homes = assign_home_stations(warehouse, agent_count)
    dedicated_stations = set(homes.values())
    colors = build_color_palette(agent_count)
    reservations = ReservationTable()
    plans: list[AgentPlan] = []

    for agent_id in range(agent_count):
        home = homes[agent_id]
        blocked_cells = dedicated_stations - {home}
        current = home
        current_time = 0
        path = [home]
        pickup_times: dict[int, int] = {}

        for task in tasks_by_agent[agent_id]:
            pickup_goals = warehouse.pickup_positions(task.location_index)
            to_pickup = find_path(
                warehouse,
                reservations,
                current,
                current_time,
                pickup_goals,
                blocked_cells=blocked_cells,
            )
            path = merge_segments(path, to_pickup)
            current = path[-1]
            current_time = len(path) - 1
            pickup_times[task.task_id] = current_time

            back_home = find_path(
                warehouse,
                reservations,
                current,
                current_time,
                {home},
                blocked_cells=blocked_cells,
            )
            path = merge_segments(path, back_home)
            current = path[-1]
            current_time = len(path) - 1

        reservations.reserve_path(path)
        plans.append(
            AgentPlan(
                agent_id=agent_id,
                color=colors[agent_id],
                home=home,
                home_index=warehouse.coord_to_index(home),
                path=path,
                tasks=tasks_by_agent[agent_id],
                pickup_times=pickup_times,
            )
        )

    return plans
