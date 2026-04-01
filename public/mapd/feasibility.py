from __future__ import annotations

from collections import deque

from mapd.models import Task
from mapd.warehouse import WarehouseMap


class ImpossibleVariantError(RuntimeError):
    pass


def impossible_variant_reason(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
) -> str | None:
    if len(warehouse.stations) < agent_count:
        return f"Map contains only {len(warehouse.stations)} stations, but scenario requires {agent_count} agents."

    homes = sorted(warehouse.stations, key=warehouse.coord_to_index)[:agent_count]
    component_by_coord = connected_components(warehouse)
    home_components = {agent_id: component_by_coord[coord] for agent_id, coord in enumerate(homes)}
    reachable_home_components = set(home_components.values())

    for task in tasks:
        if mode == "Set" and (task.agent_id < 0 or task.agent_id >= agent_count):
            return f"Task {task.task_id} references unknown agent {task.agent_id}."

        try:
            pickup_positions = warehouse.pickup_positions(task.shelf_index)
        except ValueError as exc:
            return f"Task {task.task_id} cannot be serviced: {exc}"

        pickup_components = {component_by_coord[position] for position in pickup_positions}
        if mode == "Set":
            required_component = home_components[task.agent_id]
            if required_component not in pickup_components:
                return (
                    f"Task {task.task_id} on shelf {task.shelf_index} is unreachable "
                    f"from agent {task.agent_id}'s home station."
                )
        elif not pickup_components & reachable_home_components:
            return (
                f"Task {task.task_id} on shelf {task.shelf_index} is unreachable "
                "from every agent home station."
            )

    return None


def ensure_variant_possible(
    warehouse: WarehouseMap,
    agent_count: int,
    tasks: list[Task],
    mode: str,
) -> None:
    reason = impossible_variant_reason(warehouse, agent_count, tasks, mode)
    if reason is not None:
        raise ImpossibleVariantError(reason)


def connected_components(warehouse: WarehouseMap) -> dict[tuple[int, int], int]:
    component_by_coord: dict[tuple[int, int], int] = {}
    component_id = 0

    for start in warehouse.traversable:
        if start in component_by_coord:
            continue

        queue = deque([start])
        component_by_coord[start] = component_id
        while queue:
            current = queue.popleft()
            for next_coord in warehouse.neighbors(current):
                if next_coord not in component_by_coord:
                    component_by_coord[next_coord] = component_id
                    queue.append(next_coord)

        component_id += 1

    return component_by_coord
