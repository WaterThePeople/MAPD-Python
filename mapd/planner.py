import colorsys
from collections import defaultdict
from collections import deque

from mapd.models import AgentPlan, Coord, Task
from mapd.warehouse import WarehouseMap


class ReservationTable:
    def __init__(self) -> None:
        self.vertex = defaultdict(set)
        self.edge = defaultdict(set)
        self.permanent = {}
        self.latest_time = 0

    def is_vertex_reserved(self, coord: Coord, time: int) -> bool:
        if coord in self.vertex[time]:
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
    palette = []
    for idx in range(count):
        hue = idx / count
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.75, 0.92)
        palette.append((int(red * 255), int(green * 255), int(blue * 255)))
    return palette


def rebuild_path(came_from: dict[tuple[Coord, int], tuple[Coord, int]], end_state: tuple[Coord, int]) -> list[Coord]:
    state = end_state
    path = [state[0]]

    while state in came_from:
        state = came_from[state]
        path.append(state[0])

    path.reverse()
    return path


def find_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    blocked_cells: set[Coord] | None = None,
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()

    max_time = start_time + warehouse.cell_count * 8 + reservations.latest_time + 20
    queue = deque()
    queue.append((start, start_time))

    visited = set()
    visited.add((start, start_time))
    came_from = {}

    while queue:
        current, time = queue.popleft()

        if current in goals and not reservations.is_vertex_reserved(current, time):
            return rebuild_path(came_from, (current, time))

        if time >= max_time:
            continue

        next_positions = [current]
        next_positions.extend(warehouse.neighbors(current))

        for next_coord in next_positions:
            if next_coord in blocked_cells:
                continue

            next_time = time + 1
            if reservations.is_vertex_reserved(next_coord, next_time):
                continue
            if reservations.is_edge_conflict(current, next_coord, time):
                continue

            next_state = (next_coord, next_time)
            if next_state in visited:
                continue

            visited.add(next_state)
            came_from[next_state] = (current, time)
            queue.append(next_state)

    raise RuntimeError(f"Could not find a collision-free path from {start} at time {start_time}.")


def merge_segments(base_path: list[Coord], segment: list[Coord]) -> list[Coord]:
    if not base_path:
        return segment[:]
    return [*base_path, *segment[1:]]


def wait_until_time(path: list[Coord], target_time: int) -> list[Coord]:
    while len(path) - 1 < target_time:
        path.append(path[-1])
    return path


def assign_home_stations(warehouse: WarehouseMap, agent_count: int) -> dict[int, Coord]:
    if len(warehouse.stations) < agent_count:
        raise ValueError(
            f"Map contains only {len(warehouse.stations)} stations, but scenario requires {agent_count} agents."
        )

    ordered_stations = sorted(warehouse.stations, key=warehouse.coord_to_index)
    homes = {}
    for agent_id in range(agent_count):
        homes[agent_id] = ordered_stations[agent_id]
    return homes


def shortest_distance(
    warehouse: WarehouseMap,
    start: Coord,
    goals: set[Coord],
    blocked_cells: set[Coord],
) -> int:
    queue = deque()
    queue.append((start, 0))
    visited = {start}

    while queue:
        current, distance = queue.popleft()
        if current in goals:
            return distance

        for next_coord in warehouse.neighbors(current):
            if next_coord in blocked_cells:
                continue
            if next_coord in visited:
                continue

            visited.add(next_coord)
            queue.append((next_coord, distance + 1))

    raise RuntimeError(f"Could not find a static path from {start} to task goals.")


def assign_available_tasks(warehouse: WarehouseMap, agent_count: int, tasks: list[Task]) -> list[Task]:
    homes = assign_home_stations(warehouse, agent_count)
    all_homes = set(homes.values())
    availability = {}
    distance_cache = {}
    assigned_tasks = []

    for agent_id in range(agent_count):
        availability[agent_id] = 0

    ordered_tasks = sorted(tasks, key=lambda task: (task.release_time, task.task_id))

    for task in ordered_tasks:
        best_agent_id = None
        best_finish_time = None
        best_lateness = None

        for agent_id in range(agent_count):
            blocked_cells = all_homes - {homes[agent_id]}
            cache_key = (agent_id, task.location_index)

            if cache_key not in distance_cache:
                pickup_goals = warehouse.pickup_positions(task.location_index)
                distance_cache[cache_key] = shortest_distance(
                    warehouse,
                    homes[agent_id],
                    pickup_goals,
                    blocked_cells,
                )

            travel_time = distance_cache[cache_key] * 2
            start_time = max(availability[agent_id], task.release_time)
            finish_time = start_time + travel_time
            lateness = 0
            if task.deadline is not None and finish_time > task.deadline:
                lateness = finish_time - task.deadline

            if best_finish_time is None:
                best_agent_id = agent_id
                best_finish_time = finish_time
                best_lateness = lateness
            else:
                if lateness < best_lateness:
                    best_agent_id = agent_id
                    best_finish_time = finish_time
                    best_lateness = lateness
                elif lateness == best_lateness:
                    if finish_time < best_finish_time:
                        best_agent_id = agent_id
                        best_finish_time = finish_time
                        best_lateness = lateness
                    elif finish_time == best_finish_time and agent_id < best_agent_id:
                        best_agent_id = agent_id
                        best_finish_time = finish_time
                        best_lateness = lateness

        assigned_tasks.append(
            Task(
                task_id=task.task_id,
                agent_id=best_agent_id,
                location_index=task.location_index,
                release_time=task.release_time,
                deadline=task.deadline,
            )
        )
        availability[best_agent_id] = best_finish_time

    return assigned_tasks


def build_agent_plans(warehouse: WarehouseMap, agent_count: int, tasks: list[Task], mode: str) -> list[AgentPlan]:
    if mode == "Available":
        tasks = assign_available_tasks(warehouse, agent_count, tasks)

    for task in tasks:
        if task.agent_id < 0 or task.agent_id >= agent_count:
            raise ValueError(f"Task {task.task_id} references unknown agent {task.agent_id}.")

    tasks_by_agent = {}
    for agent_id in range(agent_count):
        tasks_by_agent[agent_id] = []

    for task in tasks:
        tasks_by_agent[task.agent_id].append(task)

    homes = assign_home_stations(warehouse, agent_count)
    dedicated_stations = set(homes.values())
    colors = build_color_palette(agent_count)
    reservations = ReservationTable()
    plans = []

    for agent_id in range(agent_count):
        home = homes[agent_id]
        blocked_cells = dedicated_stations - {home}
        current = home
        current_time = 0
        path = [home]
        pickup_times = {}
        completion_times = {}
        missed_deadlines = []

        for task in tasks_by_agent[agent_id]:
            if current_time < task.release_time:
                path = wait_until_time(path, task.release_time)
                current = path[-1]
                current_time = len(path) - 1

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
            completion_times[task.task_id] = current_time
            if task.deadline is not None and current_time > task.deadline:
                missed_deadlines.append(task.task_id)

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
                completion_times=completion_times,
                missed_deadlines=missed_deadlines,
            )
        )

    return plans
