import colorsys
import heapq
import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from mapd.algorithms import get_algorithm, normalize_algorithm_name
from mapd.algorithms.base import SearchProblem, reconstruct_path
from mapd.collisions import total_collision_count
from mapd.models import AgentPlan, Coord, PlanningStats, Task
from mapd.strategy import get_strategy
from mapd.warehouse import WarehouseMap

SOFT_COLLISION_PENALTY = 1000
WHCA_WINDOW_SIZE = 32
WHCA_MAX_TIME_FACTOR = 24
WHCA_MIN_STALL_WINDOWS = 24


@dataclass
class WindowAgentProgress:
    path: list[Coord]
    task_index: int = 0
    carrying: bool = False
    pickup_times: dict[int, int] = field(default_factory=dict)
    completion_times: dict[int, int] = field(default_factory=dict)
    missed_deadlines: list[int] = field(default_factory=list)


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

    def reserve_path(self, path: list[Coord], permanent_final: bool = True) -> None:
        for time, coord in enumerate(path):
            self.vertex[time].add(coord)

        for time in range(len(path) - 1):
            self.edge[time].add((path[time], path[time + 1]))

        if permanent_final:
            final_coord = path[-1]
            final_time = len(path) - 1
            existing = self.permanent.get(final_coord)
            self.permanent[final_coord] = final_time if existing is None else min(existing, final_time)
            self.latest_time = max(self.latest_time, final_time)


@lru_cache(maxsize=None)
def build_color_palette(count: int) -> tuple[tuple[int, int, int], ...]:
    palette = []
    for idx in range(count):
        hue = idx / count
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.75, 0.92)
        palette.append((int(red * 255), int(green * 255), int(blue * 255)))
    return tuple(palette)


def goal_heuristic(warehouse: WarehouseMap, coord: Coord, goals: set[Coord]) -> int:
    if not goals:
        return 0
    return min(warehouse.distance(coord, goal) for goal in goals)


def descending_coord_priority(warehouse: WarehouseMap, coord: Coord) -> int:
    # On this warehouse family, preferring later-index cells on equal f-costs
    # keeps the heuristic search from over-occupying the upper station lanes.
    return -warehouse.coord_to_index(coord)


def is_windowed_algorithm(algorithm: str) -> bool:
    return normalize_algorithm_name(algorithm) == "WHCA*"


def low_level_algorithm_name(algorithm: str) -> str:
    canonical = normalize_algorithm_name(algorithm)
    if canonical in {"SIPP", "WHCA*"}:
        return "A*"
    return canonical


def find_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    algorithm: str,
    blocked_cells: set[Coord] | None = None,
    goal_available_after: dict[Coord, int] | None = None,
    deadline: float | None = None,
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()
    if goal_available_after is None:
        goal_available_after = {}
    if not goals:
        raise RuntimeError("Could not find a collision-free path because no goal cells were available.")

    max_time = start_time + warehouse.cell_count * 8 + reservations.latest_time + 20
    canonical_algorithm = normalize_algorithm_name(algorithm)
    search = get_algorithm(canonical_algorithm)

    if canonical_algorithm == "SIPP":
        try:
            return search.find_path(
                warehouse=warehouse,
                reservations=reservations,
                start=start,
                start_time=start_time,
                goals=goals,
                max_time=max_time,
                blocked_cells=blocked_cells,
                goal_available_after=goal_available_after,
                deadline=deadline,
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"Could not find a collision-free path from {start} at time {start_time} using {search.name}."
            ) from exc

    def is_goal(state: tuple[Coord, int]) -> bool:
        current, time = state
        return (
            current in goals
            and not reservations.is_vertex_reserved(current, time)
            and time >= goal_available_after.get(current, 0)
        )

    def neighbors(state: tuple[Coord, int]) -> list[tuple[Coord, int]]:
        current, time = state
        if time >= max_time:
            return []

        next_states = []
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

            next_states.append((next_coord, next_time))

        return next_states

    problem = SearchProblem(
        start=(start, start_time),
        is_goal=is_goal,
        neighbors=neighbors,
        heuristic=lambda state: goal_heuristic(warehouse, state[0], goals),
        tie_breaker=lambda state: descending_coord_priority(warehouse, state[0]),
        should_abort=(lambda: deadline is not None and time.perf_counter() >= deadline),
    )

    try:
        states = search.search(problem)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Could not find a collision-free path from {start} at time {start_time} using {search.name}."
        ) from exc

    return [coord for coord, _ in states]


def merge_segments(base_path: list[Coord], segment: list[Coord]) -> list[Coord]:
    if not base_path:
        return segment[:]
    return [*base_path, *segment[1:]]


def find_soft_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    blocked_cells: set[Coord] | None = None,
    goal_available_after: dict[Coord, int] | None = None,
    *,
    max_expansions: int | None = None,
    deadline: float | None = None,
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()
    if goal_available_after is None:
        goal_available_after = {}
    if not goals:
        raise RuntimeError("Could not find a relaxed path because no goal cells were available.")

    max_time = start_time + warehouse.cell_count * 12 + reservations.latest_time + 50
    start_state = (start, start_time)
    frontier: list[tuple[int, int, int, int, tuple[Coord, int]]] = [
        (goal_heuristic(warehouse, start, goals), 0, descending_coord_priority(warehouse, start), 0, start_state)
    ]
    came_from: dict[tuple[Coord, int], tuple[Coord, int]] = {}
    cost_so_far: dict[tuple[Coord, int], int] = {start_state: 0}
    tie_counter = 0
    expanded_states = 0

    while frontier:
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Relaxed path search exceeded the time budget.")

        _, _, _, _, current = heapq.heappop(frontier)
        current_cost = cost_so_far.get(current)
        if current_cost is None:
            continue
        expanded_states += 1
        if max_expansions is not None and expanded_states > max_expansions:
            raise RuntimeError(
                f"Relaxed path search exceeded the expansion budget ({max_expansions} states)."
            )

        coord, current_time = current
        if coord in goals:
            states = reconstruct_path(came_from, current)
            return [state_coord for state_coord, _ in states]
        if current_time >= max_time:
            continue

        next_positions = [coord]
        next_positions.extend(warehouse.neighbors(coord))

        for next_coord in next_positions:
            if next_coord in blocked_cells:
                continue

            next_time = current_time + 1
            penalty = 0
            if reservations.is_vertex_reserved(next_coord, next_time):
                penalty += SOFT_COLLISION_PENALTY
            if reservations.is_edge_conflict(coord, next_coord, current_time):
                penalty += SOFT_COLLISION_PENALTY

            available_after = goal_available_after.get(next_coord)
            if available_after is not None and next_time < available_after:
                penalty += (available_after - next_time) * SOFT_COLLISION_PENALTY

            next_state = (next_coord, next_time)
            next_cost = current_cost + 1 + penalty
            known_cost = cost_so_far.get(next_state)
            if known_cost is not None and next_cost >= known_cost:
                continue

            cost_so_far[next_state] = next_cost
            came_from[next_state] = current
            tie_counter += 1
            priority = next_cost + goal_heuristic(warehouse, next_coord, goals)
            heapq.heappush(
                frontier,
                (
                    priority,
                    penalty,
                    descending_coord_priority(warehouse, next_coord),
                    tie_counter,
                    next_state,
                ),
            )

    raise RuntimeError(
        f"Could not find a relaxed path from {start} at time {start_time} within {max_time - start_time} steps."
    )


def wait_until_time(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    path: list[Coord],
    target_time: int,
    algorithm: str,
    blocked_cells: set[Coord] | None = None,
    deadline: float | None = None,
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()

    start = path[-1]
    start_time = len(path) - 1
    wait_path = find_wait_path(
        warehouse,
        reservations,
        start,
        start_time,
        target_time,
        algorithm,
        blocked_cells=blocked_cells,
        deadline=deadline,
    )
    return merge_segments(path, wait_path)


def find_wait_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    target_time: int,
    algorithm: str,
    blocked_cells: set[Coord] | None = None,
    deadline: float | None = None,
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()

    if start_time >= target_time:
        return [start]

    can_wait = True
    for time_step in range(start_time + 1, target_time + 1):
        if reservations.is_vertex_reserved(start, time_step):
            can_wait = False
            break

    if can_wait:
        return [start] * (target_time - start_time + 1)

    search_algorithm = low_level_algorithm_name(algorithm)
    search = get_algorithm(search_algorithm)

    def neighbors(state: tuple[Coord, int]) -> list[tuple[Coord, int]]:
        current, time = state
        next_states = []

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

            next_states.append((next_coord, next_time))

        return next_states

    problem = SearchProblem(
        start=(start, start_time),
        is_goal=lambda state: state[1] == target_time,
        neighbors=neighbors,
        heuristic=lambda state: max(0, target_time - state[1]),
        tie_breaker=lambda state: descending_coord_priority(warehouse, state[0]),
        should_abort=(lambda: deadline is not None and time.perf_counter() >= deadline),
    )

    try:
        states = search.search(problem)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Could not find a collision-free waiting path from {start} at time {start_time} "
            f"to time {target_time} using {search.name}."
        ) from exc

    return [coord for coord, _ in states]


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
    algorithm: str,
    deadline: float | None = None,
) -> int:
    if not goals:
        raise RuntimeError("Could not find a static path because no goal cells were available.")

    search_algorithm = low_level_algorithm_name(algorithm)
    search = get_algorithm(search_algorithm)
    problem = SearchProblem(
        start=start,
        is_goal=lambda coord: coord in goals,
        neighbors=lambda coord: [next_coord for next_coord in warehouse.neighbors(coord) if next_coord not in blocked_cells],
        heuristic=lambda coord: goal_heuristic(warehouse, coord, goals),
        tie_breaker=lambda coord: descending_coord_priority(warehouse, coord),
        should_abort=(lambda: deadline is not None and time.perf_counter() >= deadline),
    )

    try:
        path = search.search(problem)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not find a static path from {start} to task goals using {search.name}.") from exc

    return len(path) - 1


def assign_available_tasks(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    station_mode: str,
    strategy: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[Task]:
    strategy_impl = get_strategy(strategy, agent_count)
    if strategy_impl.name == "None":
        raise ValueError("Strategy 'None' is only valid when Mode is Set.")

    homes = assign_home_stations(warehouse, agent_count)
    availability = {}
    distance_cache = {}
    return_cache = {}
    station_goals = set(warehouse.stations)
    assigned_tasks = []
    decision_time = 0

    for agent_id in range(agent_count):
        availability[agent_id] = 0

    ordered_tasks = sorted(tasks, key=lambda task: (task.release_time, task.task_id))
    pending_tasks: list[Task] = []
    next_task_index = 0

    def select_pending_task(free_agents: list[int]) -> tuple[Task, int]:
        if strategy_impl.name != "GreedyCost":
            task = pending_tasks[0]
            agent_id = strategy_impl.select_agent(task, free_agents, availability, travel_times)
            return task, agent_id

        best_task = None
        best_agent = None
        best_key = None
        for task in pending_tasks:
            for agent_id in free_agents:
                start_time, arrival_time, finish_time, _ = travel_times(agent_id, task)
                key = (finish_time, arrival_time, start_time, task.release_time, task.task_id, agent_id)
                if best_key is None or key < best_key:
                    best_key = key
                    best_task = task
                    best_agent = agent_id

        if best_task is None or best_agent is None:
            raise RuntimeError("Could not select a task for GreedyCost.")
        return best_task, best_agent

    def travel_times(agent_id: int, task: Task) -> tuple[int, int, int, int]:
        cache_key = (agent_id, task.shelf_index)
        if cache_key not in distance_cache:
            pickup_goals = warehouse.pickup_positions(task.shelf_index)
            distance_cache[cache_key] = shortest_distance(
                warehouse,
                homes[agent_id],
                pickup_goals,
                set(),
                algorithm,
                deadline=deadline,
            )

        distance_to_pickup = distance_cache[cache_key]
        if station_mode == "Available":
            if task.shelf_index not in return_cache:
                pickup_goals = warehouse.pickup_positions(task.shelf_index)
                return_cache[task.shelf_index] = min(
                    shortest_distance(
                        warehouse,
                        pickup,
                        station_goals,
                        set(),
                        algorithm,
                        deadline=deadline,
                    )
                    for pickup in pickup_goals
                )
            return_distance = return_cache[task.shelf_index]
        else:
            return_distance = distance_to_pickup

        start_time = max(availability[agent_id], task.release_time, decision_time)
        arrival_time = start_time + distance_to_pickup
        finish_time = start_time + distance_to_pickup + return_distance
        return start_time, arrival_time, finish_time, distance_to_pickup

    while next_task_index < len(ordered_tasks) or pending_tasks:
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Task assignment exceeded the time budget.")
        while next_task_index < len(ordered_tasks) and ordered_tasks[next_task_index].release_time <= decision_time:
            pending_tasks.append(ordered_tasks[next_task_index])
            next_task_index += 1

        free_agents = [agent_id for agent_id in range(agent_count) if availability[agent_id] <= decision_time]

        if pending_tasks and free_agents:
            task, agent_id = select_pending_task(free_agents)
            pending_tasks.remove(task)
            start_time, _, finish_time, _ = travel_times(agent_id, task)
            assigned_tasks.append(
                Task(
                    task_id=task.task_id,
                    agent_id=agent_id,
                    shelf_index=task.shelf_index,
                    release_time=task.release_time,
                    deadline=task.deadline,
                )
            )
            availability[agent_id] = finish_time
            continue

        future_times = []
        if next_task_index < len(ordered_tasks):
            future_times.append(ordered_tasks[next_task_index].release_time)
        future_times.extend(
            availability[agent_id]
            for agent_id in range(agent_count)
            if availability[agent_id] > decision_time
        )
        if not future_times:
            break

        decision_time = min(future_times)

    return assigned_tasks


def reserve_initial_positions(
    reservations: ReservationTable,
    homes: dict[int, Coord],
    tasks_by_agent: dict[int, list[Task]],
) -> None:
    for agent_id, home in homes.items():
        tasks = tasks_by_agent.get(agent_id, [])
        if not tasks:
            reservations.vertex[0].add(home)
            reservations.permanent[home] = 0
            reservations.latest_time = max(reservations.latest_time, 0)
            continue

        first_release = min(task.release_time for task in tasks)
        for time in range(first_release + 1):
            reservations.vertex[time].add(home)
        reservations.latest_time = max(reservations.latest_time, first_release)


def reserve_state_positions(
    reservations: ReservationTable,
    start_positions: dict[int, Coord],
    pending_agent_ids: set[int],
    hold_untils: dict[int, int] | None = None,
    *,
    exclude_agent_id: int | None = None,
) -> None:
    if hold_untils is None:
        hold_untils = {}
    for agent_id in pending_agent_ids:
        if agent_id == exclude_agent_id:
            continue
        hold_until = max(0, hold_untils.get(agent_id, 0))
        for time in range(hold_until + 1):
            reservations.vertex[time].add(start_positions[agent_id])
        reservations.latest_time = max(reservations.latest_time, hold_until)
    if pending_agent_ids:
        reservations.latest_time = max(reservations.latest_time, 0)


def reserve_forced_waits(
    reservations: ReservationTable,
    start_positions: dict[int, Coord],
    forced_waits: dict[int, int],
    *,
    exclude_agent_id: int | None = None,
) -> None:
    for agent_id, duration in forced_waits.items():
        if agent_id == exclude_agent_id or duration <= 0:
            continue
        coord = start_positions[agent_id]
        for time in range(1, duration + 1):
            reservations.vertex[time].add(coord)
        reservations.latest_time = max(reservations.latest_time, duration)


def estimate_finish_times(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    algorithm: str,
    deadline: float | None = None,
) -> dict[int, int]:
    estimates: dict[int, int] = {}
    distance_cache: dict[tuple[int, int], int] = {}

    for agent_id, tasks in tasks_by_agent.items():
        home = homes[agent_id]
        current_time = 0
        for task in tasks:
            if current_time < task.release_time:
                current_time = task.release_time

            cache_key = (agent_id, task.shelf_index)
            if cache_key not in distance_cache:
                pickup_goals = warehouse.pickup_positions(task.shelf_index)
                distance_cache[cache_key] = shortest_distance(
                    warehouse,
                    home,
                    pickup_goals,
                    set(),
                    algorithm,
                    deadline=deadline,
                )

            current_time += distance_cache[cache_key] * 2

        estimates[agent_id] = current_time

    return estimates


def first_release_times(tasks_by_agent: dict[int, list[Task]]) -> dict[int, int]:
    release_times = {}
    for agent_id, tasks in tasks_by_agent.items():
        release_times[agent_id] = min((task.release_time for task in tasks), default=0)
    return release_times


def rotate_order(order: list[int], offset: int) -> list[int]:
    if not order:
        return []
    normalized_offset = offset % len(order)
    if normalized_offset == 0:
        return order[:]
    return order[normalized_offset:] + order[:normalized_offset]


def alternating_order(order: list[int]) -> list[int]:
    result = []
    left = 0
    right = len(order) - 1
    while left <= right:
        result.append(order[left])
        left += 1
        if left <= right:
            result.append(order[right])
            right -= 1
    return result


def unique_orders(candidate_orders: list[list[int]]) -> list[list[int]]:
    seen: set[tuple[int, ...]] = set()
    unique: list[list[int]] = []
    for order in candidate_orders:
        key = tuple(order)
        if key in seen:
            continue
        seen.add(key)
        unique.append(order)
    return unique


def build_planning_orders(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[list[int]]:
    agent_ids = sorted(tasks_by_agent)
    finish_estimates = estimate_finish_times(warehouse, tasks_by_agent, homes, algorithm, deadline=deadline)
    release_times = first_release_times(tasks_by_agent)
    task_counts = {agent_id: len(tasks) for agent_id, tasks in tasks_by_agent.items()}

    default_order = agent_ids[:]
    if station_mode == "Set":
        default_order = sorted(agent_ids, key=lambda agent_id: (finish_estimates[agent_id], agent_id))

    candidate_orders = [
        default_order,
        list(reversed(default_order)),
        agent_ids,
        list(reversed(agent_ids)),
        sorted(agent_ids, key=lambda agent_id: (release_times[agent_id], finish_estimates[agent_id], agent_id)),
        sorted(agent_ids, key=lambda agent_id: (-task_counts[agent_id], finish_estimates[agent_id], agent_id)),
        sorted(agent_ids, key=lambda agent_id: (task_counts[agent_id], release_times[agent_id], agent_id)),
        alternating_order(default_order),
        alternating_order(list(reversed(default_order))),
    ]

    if len(default_order) > 2:
        offsets = {1, len(default_order) // 2, max(1, len(default_order) // 3)}
        for offset in sorted(offsets):
            candidate_orders.append(rotate_order(default_order, offset))
            candidate_orders.append(rotate_order(list(reversed(default_order)), offset))

    return unique_orders(candidate_orders)


def prepare_planning_inputs(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    deadline: float | None = None,
) -> tuple[list[Task], dict[int, list[Task]], dict[int, Coord], set[Coord], list[tuple[int, int, int]]]:
    resolved_tasks = tasks
    if mode == "Available":
        resolved_tasks = assign_available_tasks(
            warehouse,
            agent_count,
            tasks,
            station_mode,
            strategy,
            algorithm,
            deadline=deadline,
        )

    for task in resolved_tasks:
        if task.agent_id < 0 or task.agent_id >= agent_count:
            raise ValueError(f"Task {task.task_id} references unknown agent {task.agent_id}.")

    tasks_by_agent = {agent_id: [] for agent_id in range(agent_count)}
    for task in resolved_tasks:
        tasks_by_agent[task.agent_id].append(task)

    homes = assign_home_stations(warehouse, agent_count)
    station_cells = set(warehouse.stations)
    colors = build_color_palette(agent_count)
    return resolved_tasks, tasks_by_agent, homes, station_cells, colors


def goal_availability(
    reservations: ReservationTable,
    goals: set[Coord],
) -> tuple[set[Coord], dict[Coord, int]]:
    permanently_taken = set(reservations.permanent.keys()) & goals
    available_goals = goals - permanently_taken
    latest_visit: dict[Coord, int] = {goal: -1 for goal in available_goals}

    for time, coords in reservations.vertex.items():
        for coord in coords:
            if coord in latest_visit and time > latest_visit[coord]:
                latest_visit[coord] = time

    available_after = {goal: latest_time + 1 for goal, latest_time in latest_visit.items()}
    return available_goals, available_after


def station_availability(
    reservations: ReservationTable,
    stations: set[Coord],
) -> tuple[set[Coord], dict[Coord, int]]:
    return goal_availability(reservations, stations)


def reserve_agent_plan(
    reservations: ReservationTable,
    plan: AgentPlan,
    station_cells: set[Coord],
) -> None:
    reservations.reserve_path(plan.path)


def build_reservations(
    homes: dict[int, Coord],
    tasks_by_agent: dict[int, list[Task]],
    plans_by_id: dict[int, AgentPlan],
    pending_agent_ids: set[int],
    station_cells: set[Coord],
) -> ReservationTable:
    reservations = ReservationTable()

    if pending_agent_ids:
        reserve_initial_positions(
            reservations,
            {agent_id: homes[agent_id] for agent_id in pending_agent_ids},
            {agent_id: tasks_by_agent[agent_id] for agent_id in pending_agent_ids},
        )

    for agent_id in sorted(plans_by_id):
        reserve_agent_plan(reservations, plans_by_id[agent_id], station_cells)

    return reservations


def build_reservations_from_state(
    start_positions: dict[int, Coord],
    hold_untils: dict[int, int],
    forced_waits: dict[int, int],
    plans_by_id: dict[int, AgentPlan],
    pending_agent_ids: set[int],
    station_cells: set[Coord],
    *,
    permanent_station_agents: set[int] | None = None,
    exclude_agent_id: int | None = None,
) -> ReservationTable:
    reservations = ReservationTable()
    if pending_agent_ids:
        reserve_state_positions(
            reservations,
            start_positions,
            pending_agent_ids,
            hold_untils,
            exclude_agent_id=exclude_agent_id,
        )
    reserve_forced_waits(
        reservations,
        start_positions,
        forced_waits,
        exclude_agent_id=exclude_agent_id,
    )
    if permanent_station_agents is None:
        permanent_station_agents = set()
    for agent_id in permanent_station_agents:
        if agent_id == exclude_agent_id:
            continue
        coord = start_positions[agent_id]
        if coord in station_cells:
            existing = reservations.permanent.get(coord)
            reservations.permanent[coord] = 0 if existing is None else min(existing, 0)
    for agent_id in sorted(plans_by_id):
        reserve_agent_plan(reservations, plans_by_id[agent_id], station_cells)
    return reservations


def build_agent_plan(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    agent_id: int,
    home: Coord,
    home_index: int,
    color: tuple[int, int, int],
    agent_tasks: list[Task],
    station_mode: str,
    algorithm: str,
    blocked_cells: set[Coord],
    deadline: float | None = None,
) -> AgentPlan:
    if station_mode == "Set":
        return_goals = {home}
    else:
        return_goals = set(warehouse.stations)

    current = home
    current_time = 0
    path = [home]
    pickup_times = {}
    completion_times = {}
    missed_deadlines = []

    for task_index, task in enumerate(agent_tasks):
        if current_time < task.release_time:
            path = wait_until_time(
                warehouse,
                reservations,
                path,
                task.release_time,
                algorithm,
                blocked_cells=blocked_cells,
                deadline=deadline,
            )
            current = path[-1]
            current_time = len(path) - 1

        pickup_goals = warehouse.pickup_positions(task.shelf_index)
        to_pickup = find_path(
            warehouse,
            reservations,
            current,
            current_time,
            pickup_goals,
            algorithm,
            blocked_cells=blocked_cells,
            deadline=deadline,
        )
        path = merge_segments(path, to_pickup)
        current = path[-1]
        current_time = len(path) - 1
        pickup_times[task.task_id] = current_time

        goal_available_after = None
        if task_index == len(agent_tasks) - 1:
            if station_mode == "Available":
                return_goals, goal_available_after = station_availability(reservations, return_goals)
                if not return_goals:
                    raise RuntimeError("No free station available for final return.")
            else:
                return_goals, goal_available_after = goal_availability(reservations, return_goals)

        back_home = find_path(
            warehouse,
            reservations,
            current,
            current_time,
            return_goals,
            algorithm,
            blocked_cells=blocked_cells,
            goal_available_after=goal_available_after,
            deadline=deadline,
        )
        path = merge_segments(path, back_home)
        current = path[-1]
        current_time = len(path) - 1
        completion_times[task.task_id] = current_time
        if task.deadline is not None and current_time > task.deadline:
            missed_deadlines.append(task.task_id)

    return AgentPlan(
        agent_id=agent_id,
        color=color,
        home=home,
        home_index=home_index,
        path=path,
        tasks=agent_tasks,
        pickup_times=pickup_times,
        completion_times=completion_times,
        missed_deadlines=missed_deadlines,
    )


def build_agent_plan_from_state(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    agent_id: int,
    start: Coord,
    home: Coord,
    home_index: int,
    color: tuple[int, int, int],
    agent_tasks: list[Task],
    *,
    carrying: bool,
    initial_wait: int,
    mark_failure_start: bool,
    absolute_start_time: int,
    station_mode: str,
    algorithm: str,
    blocked_cells: set[Coord],
    deadline: float | None = None,
) -> AgentPlan:
    return_goals_default = {home} if station_mode == "Set" else set(warehouse.stations)

    current = start
    current_time = 0
    path = [start]
    pickup_times: dict[int, int] = {}
    completion_times: dict[int, int] = {}
    missed_deadlines: list[int] = []
    delayed_times: set[int] = set()
    failure_start_times: set[int] = set()

    if initial_wait > 0:
        if mark_failure_start:
            failure_start_times.add(1)
        for _ in range(initial_wait):
            current_time += 1
            path.append(current)
            delayed_times.add(current_time)

    if not agent_tasks:
        if current not in return_goals_default:
            goal_available_after = None
            if station_mode == "Available":
                return_goals, goal_available_after = station_availability(reservations, return_goals_default)
                if not return_goals:
                    raise RuntimeError("No free station available for idle return.")
            else:
                return_goals, goal_available_after = goal_availability(reservations, return_goals_default)

            return_path = find_path(
                warehouse,
                reservations,
                current,
                current_time,
                return_goals,
                algorithm,
                blocked_cells=blocked_cells,
                goal_available_after=goal_available_after,
                deadline=deadline,
            )
            path = merge_segments(path, return_path)

        return AgentPlan(
            agent_id=agent_id,
            color=color,
            home=home,
            home_index=home_index,
            path=path,
            tasks=agent_tasks,
            pickup_times=pickup_times,
            completion_times=completion_times,
            missed_deadlines=missed_deadlines,
            delayed_times=delayed_times,
            failure_start_times=failure_start_times,
        )

    for task_index, task in enumerate(agent_tasks):
        task_already_picked = carrying and task_index == 0

        if not task_already_picked:
            release_time = max(0, task.release_time - absolute_start_time)
            if current_time < release_time:
                path = wait_until_time(
                    warehouse,
                    reservations,
                    path,
                    release_time,
                    algorithm,
                    blocked_cells=blocked_cells,
                    deadline=deadline,
                )
                current = path[-1]
                current_time = len(path) - 1

            pickup_goals = warehouse.pickup_positions(task.shelf_index)
            to_pickup = find_path(
                warehouse,
                reservations,
                current,
                current_time,
                pickup_goals,
                algorithm,
                blocked_cells=blocked_cells,
                deadline=deadline,
            )
            path = merge_segments(path, to_pickup)
            current = path[-1]
            current_time = len(path) - 1
            pickup_times[task.task_id] = absolute_start_time + current_time

        return_goals = return_goals_default
        goal_available_after = None
        if task_index == len(agent_tasks) - 1:
            if station_mode == "Available":
                return_goals, goal_available_after = station_availability(reservations, return_goals)
                if not return_goals:
                    raise RuntimeError("No free station available for final return.")
            else:
                return_goals, goal_available_after = goal_availability(reservations, return_goals)

        back_to_station = find_path(
            warehouse,
            reservations,
            current,
            current_time,
            return_goals,
            algorithm,
            blocked_cells=blocked_cells,
            goal_available_after=goal_available_after,
            deadline=deadline,
        )
        path = merge_segments(path, back_to_station)
        current = path[-1]
        current_time = len(path) - 1
        completion_time = absolute_start_time + current_time
        completion_times[task.task_id] = completion_time
        if task.deadline is not None and completion_time > task.deadline:
            missed_deadlines.append(task.task_id)

    return AgentPlan(
        agent_id=agent_id,
        color=color,
        home=home,
        home_index=home_index,
        path=path,
        tasks=agent_tasks,
        pickup_times=pickup_times,
        completion_times=completion_times,
        missed_deadlines=missed_deadlines,
        delayed_times=delayed_times,
        failure_start_times=failure_start_times,
    )


def build_soft_agent_plan(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    agent_id: int,
    home: Coord,
    home_index: int,
    color: tuple[int, int, int],
    agent_tasks: list[Task],
    station_mode: str,
    blocked_cells: set[Coord],
    *,
    soft_max_expansions: int | None = None,
    deadline: float | None = None,
) -> AgentPlan:
    if station_mode == "Set":
        default_return_goals = {home}
    else:
        default_return_goals = set(warehouse.stations)

    current = home
    current_time = 0
    path = [home]
    pickup_times = {}
    completion_times = {}
    missed_deadlines = []

    for task_index, task in enumerate(agent_tasks):
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Relaxed planning exceeded the time budget.")

        if current_time < task.release_time:
            while len(path) - 1 < task.release_time:
                path.append(path[-1])
            current = path[-1]
            current_time = len(path) - 1

        pickup_goals = warehouse.pickup_positions(task.shelf_index)
        to_pickup = find_soft_path(
            warehouse,
            reservations,
            current,
            current_time,
            pickup_goals,
            blocked_cells=blocked_cells,
            max_expansions=soft_max_expansions,
            deadline=deadline,
        )
        path = merge_segments(path, to_pickup)
        current = path[-1]
        current_time = len(path) - 1
        pickup_times[task.task_id] = current_time

        return_goals = default_return_goals
        goal_available_after = {}
        if task_index == len(agent_tasks) - 1:
            if station_mode == "Available":
                available_goals, goal_available_after = station_availability(reservations, return_goals)
                if available_goals:
                    return_goals = available_goals
            else:
                available_goals, goal_available_after = goal_availability(reservations, return_goals)
                if available_goals:
                    return_goals = available_goals

        back_home = find_soft_path(
            warehouse,
            reservations,
            current,
            current_time,
            return_goals,
            blocked_cells=blocked_cells,
            goal_available_after=goal_available_after,
            max_expansions=soft_max_expansions,
            deadline=deadline,
        )
        path = merge_segments(path, back_home)
        current = path[-1]
        current_time = len(path) - 1
        completion_times[task.task_id] = current_time
        if task.deadline is not None and current_time > task.deadline:
            missed_deadlines.append(task.task_id)

    return AgentPlan(
        agent_id=agent_id,
        color=color,
        home=home,
        home_index=home_index,
        path=path,
        tasks=agent_tasks,
        pickup_times=pickup_times,
        completion_times=completion_times,
        missed_deadlines=missed_deadlines,
    )


def finished_station_conflicts(
    path: list[Coord],
    plans_by_id: dict[int, AgentPlan],
    station_cells: set[Coord],
) -> list[tuple[int, int, int]]:
    conflicts = []
    for blocker_id, plan in plans_by_id.items():
        if not plan.path:
            continue
        blocker_coord = plan.path[-1]
        if blocker_coord not in station_cells:
            continue

        blocker_end_time = len(plan.path) - 1
        use_times = []
        for time in range(blocker_end_time + 1, len(path)):
            if path[time] == blocker_coord:
                use_times.append(time)

        if use_times:
            conflicts.append((use_times[0], use_times[-1], blocker_id))

    conflicts.sort()
    return conflicts


def relocate_finished_agent(
    warehouse: WarehouseMap,
    blocker_id: int,
    plans_by_id: dict[int, AgentPlan],
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    pending_agent_ids: set[int],
    station_cells: set[Coord],
    protected_path: list[Coord],
    station_release_time: int,
    algorithm: str,
    stats: PlanningStats | None = None,
    deadline: float | None = None,
) -> bool:
    blocker_plan = plans_by_id[blocker_id]
    if not blocker_plan.path:
        return True
    blocker_coord = blocker_plan.path[-1]
    if blocker_coord not in station_cells:
        return True

    reservations = build_reservations(
        homes,
        tasks_by_agent,
        {agent_id: plan for agent_id, plan in plans_by_id.items() if agent_id != blocker_id},
        pending_agent_ids,
        station_cells,
    )
    reservations.reserve_path(protected_path)

    parking_goals, goal_available_after = goal_availability(
        reservations,
        warehouse.traversable - station_cells,
    )
    if not parking_goals:
        return False

    try:
        move_to_parking = find_path(
            warehouse,
            reservations,
            blocker_coord,
            len(blocker_plan.path) - 1,
            parking_goals,
            algorithm,
            goal_available_after=goal_available_after,
            deadline=deadline,
        )
    except RuntimeError:
        return False

    updated_path = merge_segments(blocker_plan.path, move_to_parking)
    parking_time = len(updated_path) - 1
    try:
        back_to_station = find_path(
            warehouse,
            reservations,
            updated_path[-1],
            parking_time,
            {blocker_coord},
            algorithm,
            goal_available_after={blocker_coord: station_release_time + 1},
            deadline=deadline,
        )
    except RuntimeError:
        return False
    blocker_plan.path = merge_segments(updated_path, back_to_station)
    if stats is not None:
        stats.note_replan()
    return True


def build_agent_plans_once(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    station_cells: set[Coord],
    colors: list[tuple[int, int, int]],
    planning_order: list[int],
    station_mode: str,
    algorithm: str,
    *,
    allow_soft_collisions: bool = False,
    soft_max_expansions: int | None = None,
    deadline: float | None = None,
    stats: PlanningStats | None = None,
) -> list[AgentPlan]:
    plans_by_id: dict[int, AgentPlan] = {}

    for agent_id in planning_order:
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Planning exceeded the time budget.")

        home = homes[agent_id]
        pending_agent_ids = set(tasks_by_agent) - set(plans_by_id) - {agent_id}
        blocked_cells = set()

        while True:
            reservations = build_reservations(
                homes,
                tasks_by_agent,
                plans_by_id,
                pending_agent_ids,
                station_cells,
            )
            try:
                plan = build_agent_plan(
                    warehouse=warehouse,
                    reservations=reservations,
                    agent_id=agent_id,
                    home=home,
                    home_index=warehouse.coord_to_index(home),
                    color=colors[agent_id],
                    agent_tasks=tasks_by_agent[agent_id],
                    station_mode=station_mode,
                    algorithm=algorithm,
                    blocked_cells=blocked_cells,
                    deadline=deadline,
                )
            except RuntimeError:
                if not allow_soft_collisions:
                    raise
                plan = build_soft_agent_plan(
                    warehouse=warehouse,
                    reservations=reservations,
                    agent_id=agent_id,
                    home=home,
                    home_index=warehouse.coord_to_index(home),
                    color=colors[agent_id],
                    agent_tasks=tasks_by_agent[agent_id],
                    station_mode=station_mode,
                    blocked_cells=blocked_cells,
                    soft_max_expansions=soft_max_expansions,
                    deadline=deadline,
                )

            failed_blocker = None
            for _, last_use_time, blocker_id in finished_station_conflicts(plan.path, plans_by_id, station_cells):
                moved = relocate_finished_agent(
                    warehouse=warehouse,
                    blocker_id=blocker_id,
                    plans_by_id=plans_by_id,
                    tasks_by_agent=tasks_by_agent,
                    homes=homes,
                    pending_agent_ids=pending_agent_ids,
                    station_cells=station_cells,
                    protected_path=plan.path,
                    station_release_time=last_use_time,
                    algorithm=algorithm,
                    stats=stats,
                    deadline=deadline,
                )
                if not moved:
                    failed_blocker = blocker_id
                    break

            if failed_blocker is None:
                break

            blocker_plan = plans_by_id[failed_blocker]
            blocker_coord = blocker_plan.path[-1]
            if blocker_coord in blocked_cells:
                raise RuntimeError("Could not resolve a repeated station-lane conflict during replanning.")
            if stats is not None:
                stats.note_replan()
            blocked_cells.add(blocker_coord)

        plans_by_id[agent_id] = plan

    return [plans_by_id[agent_id] for agent_id in sorted(plans_by_id)]


def build_agent_plans_from_state_once(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    start_positions: dict[int, Coord],
    carrying_by_agent: dict[int, bool],
    forced_waits: dict[int, int],
    mark_failure_start: set[int],
    absolute_start_time: int,
    colors: list[tuple[int, int, int]],
    planning_order: list[int],
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[AgentPlan]:
    station_cells = set(warehouse.stations)
    plans_by_id: dict[int, AgentPlan] = {}
    hold_untils: dict[int, int] = {}

    for agent_id, tasks in tasks_by_agent.items():
        hold_until = max(0, forced_waits.get(agent_id, 0))
        if not carrying_by_agent.get(agent_id, False) and tasks:
            next_release_offset = max(0, tasks[0].release_time - absolute_start_time)
            hold_until = max(hold_until, next_release_offset)
        hold_untils[agent_id] = hold_until

    permanent_station_agents = {
        agent_id
        for agent_id, tasks in tasks_by_agent.items()
        if not tasks and not carrying_by_agent.get(agent_id, False) and start_positions[agent_id] in station_cells
    }

    for index, agent_id in enumerate(planning_order):
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("Replanning from state exceeded the time budget.")
        pending_agent_ids = set(planning_order[index:])
        reservations = build_reservations_from_state(
            start_positions,
            hold_untils,
            forced_waits,
            plans_by_id,
            pending_agent_ids,
            station_cells,
            permanent_station_agents=permanent_station_agents,
            exclude_agent_id=agent_id,
        )
        plan = build_agent_plan_from_state(
            warehouse=warehouse,
            reservations=reservations,
            agent_id=agent_id,
            start=start_positions[agent_id],
            home=homes[agent_id],
            home_index=warehouse.coord_to_index(homes[agent_id]),
            color=colors[agent_id],
            agent_tasks=tasks_by_agent[agent_id],
            carrying=carrying_by_agent.get(agent_id, False),
            initial_wait=forced_waits.get(agent_id, 0),
            mark_failure_start=agent_id in mark_failure_start,
            absolute_start_time=absolute_start_time,
            station_mode=station_mode,
            algorithm=algorithm,
            blocked_cells=set(),
            deadline=deadline,
        )
        plans_by_id[agent_id] = plan

    return [plans_by_id[agent_id] for agent_id in sorted(plans_by_id)]


def truncate_path_to_steps(path: list[Coord], max_steps: int) -> list[Coord]:
    if not path:
        return []
    if max_steps <= 0 or len(path) == 1:
        return [path[0]]
    return path[: min(len(path), max_steps + 1)]


def trim_trailing_waits(path: list[Coord]) -> list[Coord]:
    trimmed = path[:]
    while len(trimmed) > 1 and trimmed[-1] == trimmed[-2]:
        trimmed.pop()
    return trimmed


def build_final_return_reservations(
    plans: list[AgentPlan],
    *,
    exclude_agent_id: int | None = None,
) -> ReservationTable:
    reservations = ReservationTable()
    for plan in plans:
        if plan.agent_id == exclude_agent_id:
            continue
        reservations.reserve_path(plan.path, permanent_final=True)
    return reservations


def return_completed_agents_to_stations(
    warehouse: WarehouseMap,
    plans: list[AgentPlan],
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[AgentPlan]:
    updated_plans = [plan for plan in plans]
    station_cells = set(warehouse.stations)
    current_return_start = max((len(plan.path) - 1 for plan in updated_plans), default=0)

    for index, plan in enumerate(updated_plans):
        if plan.path[-1] in station_cells:
            continue

        return_goals = {plan.home} if station_mode == "Set" else station_cells
        waited_path = plan.path[:]
        missing_waits = current_return_start - (len(waited_path) - 1)
        if missing_waits > 0:
            waited_path.extend([waited_path[-1]] * missing_waits)

        reservations = build_final_return_reservations(updated_plans, exclude_agent_id=plan.agent_id)
        final_return = find_path(
            warehouse,
            reservations,
            waited_path[-1],
            len(waited_path) - 1,
            return_goals,
            low_level_algorithm_name(algorithm),
            deadline=deadline,
        )
        returned_plan = AgentPlan(
            agent_id=plan.agent_id,
            color=plan.color,
            home=plan.home,
            home_index=plan.home_index,
            path=merge_segments(waited_path, final_return),
            tasks=plan.tasks,
            pickup_times=plan.pickup_times,
            completion_times=plan.completion_times,
            missed_deadlines=plan.missed_deadlines,
            delayed_times=plan.delayed_times,
        )
        updated_plans[index] = returned_plan
        current_return_start = len(returned_plan.path) - 1

    return updated_plans


def build_window_reservations(
    progress_by_id: dict[int, WindowAgentProgress],
    window_end: int,
    *,
    exclude_agent_id: int | None = None,
) -> ReservationTable:
    reservations = ReservationTable()
    for agent_id in sorted(progress_by_id):
        if agent_id == exclude_agent_id:
            continue
        progress_path = progress_by_id[agent_id].path
        padded_path = progress_path[:]
        missing_steps = window_end - (len(progress_path) - 1)
        if missing_steps > 0:
            padded_path.extend([progress_path[-1]] * missing_steps)
        reservations.reserve_path(padded_path, permanent_final=False)
    return reservations


def all_window_agents_done(
    progress_by_id: dict[int, WindowAgentProgress],
    tasks_by_agent: dict[int, list[Task]],
) -> bool:
    return all(
        not progress.carrying and progress.task_index >= len(tasks_by_agent[agent_id])
        for agent_id, progress in progress_by_id.items()
    )


def whca_time_limit(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
) -> int:
    latest_release = max(
        (task.release_time for agent_tasks in tasks_by_agent.values() for task in agent_tasks),
        default=0,
    )
    task_count = sum(len(agent_tasks) for agent_tasks in tasks_by_agent.values())
    return latest_release + warehouse.cell_count * WHCA_MAX_TIME_FACTOR + max(1, task_count) * WHCA_WINDOW_SIZE * 4


def whca_stall_window_limit(warehouse: WarehouseMap) -> int:
    window_span = max(1, WHCA_WINDOW_SIZE * 2)
    return max(WHCA_MIN_STALL_WINDOWS, (warehouse.cell_count + window_span - 1) // window_span)


def whca_progress_signature(progress_by_id: dict[int, WindowAgentProgress]) -> tuple[tuple[int, bool], ...]:
    return tuple(
        (progress.task_index, progress.carrying)
        for _, progress in sorted(progress_by_id.items())
    )


def whca_waits_for_future_release(
    progress_by_id: dict[int, WindowAgentProgress],
    tasks_by_agent: dict[int, list[Task]],
    current_time: int,
) -> bool:
    for agent_id, progress in progress_by_id.items():
        if progress.carrying:
            continue
        agent_tasks = tasks_by_agent[agent_id]
        if progress.task_index >= len(agent_tasks):
            continue
        if agent_tasks[progress.task_index].release_time > current_time:
            return True
    return False


def window_path_search(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    algorithm: str,
    blocked_cells: set[Coord],
    *,
    allow_soft_collisions: bool,
    goal_available_after: dict[Coord, int] | None = None,
    soft_max_expansions: int | None = None,
    deadline: float | None = None,
) -> list[Coord]:
    if allow_soft_collisions:
        return find_soft_path(
            warehouse,
            reservations,
            start,
            start_time,
            goals,
            blocked_cells=blocked_cells,
            goal_available_after=goal_available_after,
            max_expansions=soft_max_expansions,
            deadline=deadline,
        )

    return find_path(
        warehouse,
        reservations,
        start,
        start_time,
        goals,
        low_level_algorithm_name(algorithm),
        blocked_cells=blocked_cells,
        goal_available_after=goal_available_after,
        deadline=deadline,
    )


def extend_windowed_agent_progress(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    progress: WindowAgentProgress,
    home: Coord,
    agent_tasks: list[Task],
    station_mode: str,
    algorithm: str,
    blocked_cells: set[Coord],
    *,
    allow_soft_collisions: bool = False,
    soft_max_expansions: int | None = None,
    deadline: float | None = None,
    window_size: int = WHCA_WINDOW_SIZE,
) -> None:
    base_time = len(progress.path) - 1
    window_end = base_time + window_size
    segment = [progress.path[-1]]
    current = segment[-1]
    current_time = base_time

    while current_time < window_end:
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("WHCA* planning exceeded the time budget.")

        if progress.carrying:
            task = agent_tasks[progress.task_index]
            return_goals = {home} if station_mode == "Set" else set(warehouse.stations)
            full_path = window_path_search(
                warehouse,
                reservations,
                current,
                current_time,
                return_goals,
                algorithm,
                blocked_cells,
                allow_soft_collisions=allow_soft_collisions,
                soft_max_expansions=soft_max_expansions,
                deadline=deadline,
            )
            prefix = truncate_path_to_steps(full_path, window_end - current_time)
            segment = merge_segments(segment, prefix)
            current = segment[-1]
            current_time = base_time + len(segment) - 1
            if len(prefix) != len(full_path):
                break

            progress.completion_times[task.task_id] = current_time
            if task.deadline is not None and current_time > task.deadline:
                progress.missed_deadlines.append(task.task_id)
            progress.carrying = False
            progress.task_index += 1
            continue

        if progress.task_index >= len(agent_tasks):
            break

        task = agent_tasks[progress.task_index]
        if current_time < task.release_time:
            wait_target_time = min(window_end, task.release_time)
            wait_path = find_wait_path(
                warehouse,
                reservations,
                current,
                current_time,
                wait_target_time,
                algorithm,
                blocked_cells=blocked_cells,
                deadline=deadline,
            )
            segment = merge_segments(segment, wait_path)
            current = segment[-1]
            current_time = base_time + len(segment) - 1
            if current_time < task.release_time:
                break
            continue

        pickup_goals = warehouse.pickup_positions(task.shelf_index)
        full_path = window_path_search(
            warehouse,
            reservations,
            current,
            current_time,
            pickup_goals,
            algorithm,
            blocked_cells,
            allow_soft_collisions=allow_soft_collisions,
            soft_max_expansions=soft_max_expansions,
            deadline=deadline,
        )
        prefix = truncate_path_to_steps(full_path, window_end - current_time)
        segment = merge_segments(segment, prefix)
        current = segment[-1]
        current_time = base_time + len(segment) - 1
        if len(prefix) != len(full_path):
            break

        progress.pickup_times[task.task_id] = current_time
        progress.carrying = True

    if current_time < window_end:
        wait_path = find_wait_path(
            warehouse,
            reservations,
            current,
            current_time,
            window_end,
            algorithm,
            blocked_cells=blocked_cells,
            deadline=deadline,
        )
        segment = merge_segments(segment, wait_path)

    progress.path = merge_segments(progress.path, segment)


def build_whca_agent_plans_once(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    colors: list[tuple[int, int, int]],
    planning_order: list[int],
    station_mode: str,
    algorithm: str,
    *,
    allow_soft_collisions: bool = False,
    soft_max_expansions: int | None = None,
    deadline: float | None = None,
) -> list[AgentPlan]:
    progress_by_id = {
        agent_id: WindowAgentProgress(path=[homes[agent_id]])
        for agent_id in sorted(tasks_by_agent)
    }
    time_limit = whca_time_limit(warehouse, tasks_by_agent)
    stall_window_limit = whca_stall_window_limit(warehouse)
    stalled_windows = 0

    while not all_window_agents_done(progress_by_id, tasks_by_agent):
        if deadline is not None and time.perf_counter() >= deadline:
            raise RuntimeError("WHCA* planning exceeded the time budget.")

        current_time = len(progress_by_id[planning_order[0]].path) - 1 if planning_order else 0
        if current_time > time_limit:
            raise RuntimeError("WHCA* exceeded the rolling-horizon time limit.")

        signature_before = whca_progress_signature(progress_by_id)
        for agent_id in planning_order:
            progress = progress_by_id[agent_id]
            current_window_end = len(progress_by_id[agent_id].path) - 1 + WHCA_WINDOW_SIZE
            if not progress.carrying and progress.task_index >= len(tasks_by_agent[agent_id]):
                missing_steps = current_window_end - (len(progress.path) - 1)
                if missing_steps > 0:
                    progress.path.extend([progress.path[-1]] * missing_steps)
                continue
            reservations = build_window_reservations(
                progress_by_id,
                current_window_end,
                exclude_agent_id=agent_id,
            )
            extend_windowed_agent_progress(
                warehouse=warehouse,
                reservations=reservations,
                progress=progress_by_id[agent_id],
                home=homes[agent_id],
                agent_tasks=tasks_by_agent[agent_id],
                station_mode=station_mode,
                algorithm=algorithm,
                blocked_cells=set(),
                allow_soft_collisions=allow_soft_collisions,
                soft_max_expansions=soft_max_expansions,
                deadline=deadline,
            )

        current_time = len(progress_by_id[planning_order[0]].path) - 1 if planning_order else current_time
        signature_after = whca_progress_signature(progress_by_id)
        if signature_after != signature_before:
            stalled_windows = 0
            continue

        if whca_waits_for_future_release(progress_by_id, tasks_by_agent, current_time):
            stalled_windows = 0
            continue

        stalled_windows += 1
        if stalled_windows >= stall_window_limit:
            raise RuntimeError(
                "WHCA* stalled without task progress. "
                f"No pickup or delivery progress was made for {stalled_windows} consecutive windows."
            )

    plans = []
    for agent_id in sorted(progress_by_id):
        progress = progress_by_id[agent_id]
        plans.append(
            AgentPlan(
                agent_id=agent_id,
                color=colors[agent_id],
                home=homes[agent_id],
                home_index=warehouse.coord_to_index(homes[agent_id]),
                path=trim_trailing_waits(progress.path),
                tasks=tasks_by_agent[agent_id],
                pickup_times=progress.pickup_times,
                completion_times=progress.completion_times,
                missed_deadlines=progress.missed_deadlines,
            )
        )
    return return_completed_agents_to_stations(warehouse, plans, station_mode, algorithm, deadline=deadline)


def build_agent_plans(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    *,
    stats: PlanningStats | None = None,
    deadline: float | None = None,
) -> list[AgentPlan]:
    _, tasks_by_agent, homes, station_cells, colors = prepare_planning_inputs(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
        deadline=deadline,
    )
    planning_orders = build_planning_orders(
        warehouse,
        tasks_by_agent,
        homes,
        station_mode,
        algorithm,
        deadline=deadline,
    )
    use_whca = is_windowed_algorithm(algorithm)

    last_error: RuntimeError | None = None
    for attempt_index, planning_order in enumerate(planning_orders):
        if attempt_index > 0 and stats is not None:
            stats.note_replan()
        try:
            if use_whca:
                return build_whca_agent_plans_once(
                    warehouse,
                    tasks_by_agent,
                    homes,
                    colors,
                    planning_order,
                    station_mode,
                    algorithm,
                    deadline=deadline,
                )
            return build_agent_plans_once(
                warehouse,
                tasks_by_agent,
                homes,
                station_cells,
                colors,
                planning_order,
                station_mode,
                algorithm,
                allow_soft_collisions=False,
                stats=stats,
                deadline=deadline,
            )
        except RuntimeError as exc:
            last_error = exc

    attempts = len(planning_orders)
    if last_error is None:
        raise RuntimeError(f"Could not find a collision-free plan after {attempts} planning attempts.")
    raise RuntimeError(f"Could not find a collision-free plan after {attempts} planning attempts. Last error: {last_error}")


def build_agent_plans_from_state(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    start_positions: dict[int, Coord],
    carrying_by_agent: dict[int, bool],
    forced_waits: dict[int, int],
    mark_failure_start: set[int],
    absolute_start_time: int,
    colors: list[tuple[int, int, int]],
    station_mode: str,
    algorithm: str,
    deadline: float | None = None,
) -> list[AgentPlan]:
    ordering_positions = {agent_id: start_positions[agent_id] for agent_id in tasks_by_agent}
    planning_orders = build_planning_orders(
        warehouse,
        tasks_by_agent,
        ordering_positions,
        station_mode,
        algorithm,
        deadline=deadline,
    )

    last_error: RuntimeError | None = None
    for planning_order in planning_orders:
        try:
            return build_agent_plans_from_state_once(
                warehouse=warehouse,
                tasks_by_agent=tasks_by_agent,
                homes=homes,
                start_positions=start_positions,
                carrying_by_agent=carrying_by_agent,
                forced_waits=forced_waits,
                mark_failure_start=mark_failure_start,
                absolute_start_time=absolute_start_time,
                colors=colors,
                planning_order=planning_order,
                station_mode=station_mode,
                algorithm=algorithm,
                deadline=deadline,
            )
        except RuntimeError as exc:
            last_error = exc

    attempts = len(planning_orders)
    if last_error is None:
        raise RuntimeError(f"Could not find a collision-free suffix plan after {attempts} planning attempts.")
    raise RuntimeError(
        f"Could not find a collision-free suffix plan after {attempts} planning attempts. Last error: {last_error}"
    )


def build_relaxed_agent_plans(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    *,
    time_budget_seconds: float | None = None,
    soft_max_expansions: int | None = None,
    stats: PlanningStats | None = None,
    deadline: float | None = None,
) -> list[AgentPlan]:
    _, tasks_by_agent, homes, station_cells, colors = prepare_planning_inputs(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
        deadline=deadline,
    )
    planning_orders = build_planning_orders(
        warehouse,
        tasks_by_agent,
        homes,
        station_mode,
        algorithm,
        deadline=deadline,
    )
    use_whca = is_windowed_algorithm(algorithm)
    effective_deadline = deadline
    if effective_deadline is None and time_budget_seconds is not None:
        effective_deadline = time.perf_counter() + max(0.0, time_budget_seconds)

    best_plans: list[AgentPlan] | None = None
    best_score: tuple[int, int, int] | None = None
    last_error: RuntimeError | None = None

    for attempt_index, planning_order in enumerate(planning_orders):
        if attempt_index > 0 and stats is not None:
            stats.note_replan()
        if effective_deadline is not None and time.perf_counter() >= effective_deadline:
            last_error = RuntimeError("Fallback planning exceeded the time budget.")
            break
        try:
            if use_whca:
                plans = build_whca_agent_plans_once(
                    warehouse,
                    tasks_by_agent,
                    homes,
                    colors,
                    planning_order,
                    station_mode,
                    algorithm,
                    allow_soft_collisions=True,
                    soft_max_expansions=soft_max_expansions,
                    deadline=effective_deadline,
                )
            else:
                plans = build_agent_plans_once(
                    warehouse,
                    tasks_by_agent,
                    homes,
                    station_cells,
                    colors,
                    planning_order,
                    station_mode,
                    algorithm,
                    allow_soft_collisions=True,
                    soft_max_expansions=soft_max_expansions,
                    deadline=effective_deadline,
                    stats=stats,
                )
        except RuntimeError as exc:
            last_error = exc
            continue

        collision_count = total_collision_count(plans)
        makespan = max((len(plan.path) for plan in plans), default=1) - 1
        total_path_length = sum(len(plan.path) - 1 for plan in plans)
        score = (collision_count, makespan, total_path_length)
        if best_score is None or score < best_score:
            best_score = score
            best_plans = plans
            if collision_count == 0:
                break

    if best_plans is not None:
        return best_plans

    attempts = len(planning_orders)
    if last_error is None:
        raise RuntimeError(f"Could not build a fallback plan after {attempts} planning attempts.")
    raise RuntimeError(f"Could not build a fallback plan after {attempts} planning attempts. Last error: {last_error}")
