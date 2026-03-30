import colorsys
import heapq
import time
from collections import defaultdict

from mapd.algorithms import get_algorithm, normalize_algorithm_name
from mapd.algorithms.base import SearchProblem, reconstruct_path
from mapd.collisions import total_collision_count
from mapd.models import AgentPlan, Coord, Task
from mapd.strategy import get_strategy
from mapd.warehouse import WarehouseMap

SOFT_COLLISION_PENALTY = 1000
MAX_PLANNING_ORDER_ATTEMPTS = 12


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


def build_color_palette(count: int) -> list[tuple[int, int, int]]:
    palette = []
    for idx in range(count):
        hue = idx / count
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.75, 0.92)
        palette.append((int(red * 255), int(green * 255), int(blue * 255)))
    return palette


def goal_heuristic(warehouse: WarehouseMap, coord: Coord, goals: set[Coord]) -> int:
    if not goals:
        return 0
    return min(warehouse.distance(coord, goal) for goal in goals)


def descending_coord_priority(warehouse: WarehouseMap, coord: Coord) -> int:
    # On this warehouse family, preferring later-index cells on equal f-costs
    # keeps the heuristic search from over-occupying the upper station lanes.
    return -warehouse.coord_to_index(coord)


def find_path(
    warehouse: WarehouseMap,
    reservations: ReservationTable,
    start: Coord,
    start_time: int,
    goals: set[Coord],
    algorithm: str,
    blocked_cells: set[Coord] | None = None,
    goal_available_after: dict[Coord, int] | None = None,
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
) -> list[Coord]:
    if blocked_cells is None:
        blocked_cells = set()

    start = path[-1]
    start_time = len(path) - 1
    if start_time >= target_time:
        return path

    can_wait = True
    for time in range(start_time + 1, target_time + 1):
        if reservations.is_vertex_reserved(start, time):
            can_wait = False
            break

    if can_wait:
        while len(path) - 1 < target_time:
            path.append(path[-1])
        return path

    search_algorithm = "A*" if normalize_algorithm_name(algorithm) == "SIPP" else algorithm
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
    )

    try:
        states = search.search(problem)
    except RuntimeError as exc:
        raise RuntimeError(
            f"Could not find a collision-free waiting path from {start} at time {start_time} "
            f"to time {target_time} using {search.name}."
        ) from exc

    wait_path = [coord for coord, _ in states]
    return merge_segments(path, wait_path)


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
) -> int:
    if not goals:
        raise RuntimeError("Could not find a static path because no goal cells were available.")

    search_algorithm = "A*" if normalize_algorithm_name(algorithm) == "SIPP" else algorithm
    search = get_algorithm(search_algorithm)
    problem = SearchProblem(
        start=start,
        is_goal=lambda coord: coord in goals,
        neighbors=lambda coord: [next_coord for next_coord in warehouse.neighbors(coord) if next_coord not in blocked_cells],
        heuristic=lambda coord: goal_heuristic(warehouse, coord, goals),
        tie_breaker=lambda coord: descending_coord_priority(warehouse, coord),
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
            distance_cache[cache_key] = shortest_distance(warehouse, homes[agent_id], pickup_goals, set(), algorithm)

        distance_to_pickup = distance_cache[cache_key]
        if station_mode == "Available":
            if task.shelf_index not in return_cache:
                pickup_goals = warehouse.pickup_positions(task.shelf_index)
                return_cache[task.shelf_index] = min(
                    shortest_distance(warehouse, pickup, station_goals, set(), algorithm) for pickup in pickup_goals
                )
            return_distance = return_cache[task.shelf_index]
        else:
            return_distance = distance_to_pickup

        start_time = max(availability[agent_id], task.release_time, decision_time)
        arrival_time = start_time + distance_to_pickup
        finish_time = start_time + distance_to_pickup + return_distance
        return start_time, arrival_time, finish_time, distance_to_pickup

    while next_task_index < len(ordered_tasks) or pending_tasks:
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


def estimate_finish_times(
    warehouse: WarehouseMap,
    tasks_by_agent: dict[int, list[Task]],
    homes: dict[int, Coord],
    algorithm: str,
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
                distance_cache[cache_key] = shortest_distance(warehouse, home, pickup_goals, set(), algorithm)

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
) -> list[list[int]]:
    agent_ids = sorted(tasks_by_agent)
    finish_estimates = estimate_finish_times(warehouse, tasks_by_agent, homes, algorithm)
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

    return unique_orders(candidate_orders)[:MAX_PLANNING_ORDER_ATTEMPTS]


def prepare_planning_inputs(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> tuple[list[Task], dict[int, list[Task]], dict[int, Coord], set[Coord], list[tuple[int, int, int]]]:
    resolved_tasks = tasks
    if mode == "Available":
        resolved_tasks = assign_available_tasks(warehouse, agent_count, tasks, station_mode, strategy, algorithm)

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
        )
    except RuntimeError:
        return False
    blocker_plan.path = merge_segments(updated_path, back_to_station)
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
            blocked_cells.add(blocker_coord)

        plans_by_id[agent_id] = plan

    return [plans_by_id[agent_id] for agent_id in sorted(plans_by_id)]


def build_agent_plans(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> list[AgentPlan]:
    _, tasks_by_agent, homes, station_cells, colors = prepare_planning_inputs(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
    )
    planning_orders = build_planning_orders(warehouse, tasks_by_agent, homes, station_mode, algorithm)

    last_error: RuntimeError | None = None
    for planning_order in planning_orders:
        try:
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
            )
        except RuntimeError as exc:
            last_error = exc

    attempts = len(planning_orders)
    if last_error is None:
        raise RuntimeError(f"Could not find a collision-free plan after {attempts} planning attempts.")
    raise RuntimeError(f"Could not find a collision-free plan after {attempts} planning attempts. Last error: {last_error}")


def build_relaxed_agent_plans(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    *,
    max_order_attempts: int | None = None,
    time_budget_seconds: float | None = None,
    soft_max_expansions: int | None = None,
) -> list[AgentPlan]:
    _, tasks_by_agent, homes, station_cells, colors = prepare_planning_inputs(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
    )
    planning_orders = build_planning_orders(warehouse, tasks_by_agent, homes, station_mode, algorithm)
    if max_order_attempts is not None:
        planning_orders = planning_orders[: max(1, max_order_attempts)]
    deadline = None
    if time_budget_seconds is not None:
        deadline = time.perf_counter() + max(0.0, time_budget_seconds)

    best_plans: list[AgentPlan] | None = None
    best_score: tuple[int, int, int] | None = None
    last_error: RuntimeError | None = None

    for planning_order in planning_orders:
        if deadline is not None and time.perf_counter() >= deadline:
            last_error = RuntimeError("Fallback planning exceeded the time budget.")
            break
        try:
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
                deadline=deadline,
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
