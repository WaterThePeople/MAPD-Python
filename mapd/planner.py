import colorsys
from collections import defaultdict

from mapd.algorithms import get_algorithm, normalize_algorithm_name
from mapd.algorithms.base import SearchProblem
from mapd.models import AgentPlan, Coord, Task
from mapd.strategy import get_strategy
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


def manhattan_distance(src: Coord, dst: Coord) -> int:
    return abs(src[0] - dst[0]) + abs(src[1] - dst[1])


def goal_heuristic(coord: Coord, goals: set[Coord]) -> int:
    if not goals:
        return 0
    return min(manhattan_distance(coord, goal) for goal in goals)


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
        heuristic=lambda state: goal_heuristic(state[0], goals),
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
        heuristic=lambda coord: goal_heuristic(coord, goals),
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

    for agent_id in range(agent_count):
        availability[agent_id] = 0

    ordered_tasks = sorted(tasks, key=lambda task: (task.release_time, task.task_id))

    def travel_times(agent_id: int, task: Task) -> tuple[int, int, int, int]:
        cache_key = (agent_id, task.location_index)
        if cache_key not in distance_cache:
            pickup_goals = warehouse.pickup_positions(task.location_index)
            distance_cache[cache_key] = shortest_distance(warehouse, homes[agent_id], pickup_goals, set(), algorithm)

        distance_to_pickup = distance_cache[cache_key]
        if station_mode == "Available":
            if task.location_index not in return_cache:
                pickup_goals = warehouse.pickup_positions(task.location_index)
                return_cache[task.location_index] = min(
                    shortest_distance(warehouse, pickup, station_goals, set(), algorithm) for pickup in pickup_goals
                )
            return_distance = return_cache[task.location_index]
        else:
            return_distance = distance_to_pickup

        start_time = max(availability[agent_id], task.release_time)
        arrival_time = start_time + distance_to_pickup
        finish_time = start_time + distance_to_pickup + return_distance
        return start_time, arrival_time, finish_time, distance_to_pickup

    for task in ordered_tasks:
        agent_id = strategy_impl.select_agent(task, agent_count, availability, travel_times)

        start_time, _, finish_time, _ = travel_times(agent_id, task)
        assigned_tasks.append(
            Task(
                task_id=task.task_id,
                agent_id=agent_id,
                location_index=task.location_index,
                release_time=task.release_time,
                deadline=task.deadline,
            )
        )
        availability[agent_id] = finish_time

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

            cache_key = (agent_id, task.location_index)
            if cache_key not in distance_cache:
                pickup_goals = warehouse.pickup_positions(task.location_index)
                distance_cache[cache_key] = shortest_distance(warehouse, home, pickup_goals, set(), algorithm)

            current_time += distance_cache[cache_key] * 2

        estimates[agent_id] = current_time

    return estimates


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

        pickup_goals = warehouse.pickup_positions(task.location_index)
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


def build_agent_plans(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> list[AgentPlan]:
    if mode == "Available":
        tasks = assign_available_tasks(warehouse, agent_count, tasks, station_mode, strategy, algorithm)

    for task in tasks:
        if task.agent_id < 0 or task.agent_id >= agent_count:
            raise ValueError(f"Task {task.task_id} references unknown agent {task.agent_id}.")

    tasks_by_agent = {}
    for agent_id in range(agent_count):
        tasks_by_agent[agent_id] = []

    for task in tasks:
        tasks_by_agent[task.agent_id].append(task)

    homes = assign_home_stations(warehouse, agent_count)
    station_cells = set(warehouse.stations)
    colors = build_color_palette(agent_count)
    planning_order = list(range(agent_count))
    if station_mode == "Set":
        finish_estimates = estimate_finish_times(warehouse, tasks_by_agent, homes, algorithm)
        planning_order.sort(key=lambda agent_id: (finish_estimates[agent_id], agent_id))

    plans_by_id = {}

    for agent_id in planning_order:
        home = homes[agent_id]
        pending_agent_ids = set(range(agent_count)) - set(plans_by_id) - {agent_id}
        blocked_cells = set()

        while True:
            reservations = build_reservations(
                homes,
                tasks_by_agent,
                plans_by_id,
                pending_agent_ids,
                station_cells,
            )
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
            blocked_cells.add(blocker_plan.path[-1])

        plans_by_id[agent_id] = plan

    return [plans_by_id[agent_id] for agent_id in range(agent_count)]
