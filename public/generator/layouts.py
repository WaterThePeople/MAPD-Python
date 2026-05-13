from __future__ import annotations

from collections import deque

from mapd.loader import layout_path, load_layout
from mapd.models import Coord
from mapd.warehouse import WarehouseMap

from .constants import SizeProfile, SIZE_PROFILES
from .definitions import LayoutContext, ShelfDescriptor


def assign_home_stations(warehouse: WarehouseMap, agent_count: int) -> list[Coord]:
    ordered_stations = sorted(warehouse.stations, key=warehouse.coord_to_index)
    if len(ordered_stations) < agent_count:
        raise ValueError(
            f"Layout contains only {len(ordered_stations)} stations, but the scenario requires {agent_count} agents."
        )
    return ordered_stations[:agent_count]


def bfs_distances(warehouse: WarehouseMap, starts: list[Coord]) -> dict[Coord, int]:
    distances: dict[Coord, int] = {}
    queue = deque()
    for start in starts:
        distances[start] = 0
        queue.append(start)

    while queue:
        current = queue.popleft()
        current_distance = distances[current]
        for next_coord in warehouse.neighbors(current):
            if next_coord in distances:
                continue
            distances[next_coord] = current_distance + 1
            queue.append(next_coord)

    return distances


def all_home_distances(warehouse: WarehouseMap, homes: list[Coord]) -> list[dict[Coord, int]]:
    return [bfs_distances(warehouse, [home]) for home in homes]


def build_shelf_descriptors(
    warehouse: WarehouseMap,
    station_distances: dict[Coord, int],
    delivery_distances: dict[Coord, int],
) -> list[ShelfDescriptor]:
    descriptors = []
    for shelf_index in range(warehouse.shelf_count):
        coord = warehouse.shelf_index_to_coord(shelf_index)
        pickup_positions = tuple(sorted(warehouse.pickup_positions(shelf_index)))
        station_distance = min(station_distances[pickup] for pickup in pickup_positions)
        reachable_delivery = [delivery_distances[pickup] for pickup in pickup_positions if pickup in delivery_distances]
        if not reachable_delivery:
            raise ValueError(f"Shelf {shelf_index} has no path from pickup position to a delivery area.")
        descriptors.append(
            ShelfDescriptor(
                shelf_index=shelf_index,
                coord=coord,
                pickup_positions=pickup_positions,
                station_distance=station_distance,
                delivery_distance=min(reachable_delivery),
            )
        )
    return descriptors


def distances_from_homes_to_shelves(
    shelf_descriptors: list[ShelfDescriptor],
    home_distances: list[dict[Coord, int]],
) -> list[list[int | None]]:
    distances_by_agent: list[list[int | None]] = []
    for distances in home_distances:
        per_shelf: list[int | None] = []
        for descriptor in shelf_descriptors:
            reachable = [distances[pickup] for pickup in descriptor.pickup_positions if pickup in distances]
            per_shelf.append(min(reachable) if reachable else None)
        distances_by_agent.append(per_shelf)
    return distances_by_agent


def validate_layout(size: SizeProfile, max_open_tasks_on_shelves: int, layout_id: int, warehouse: WarehouseMap) -> None:
    if len(warehouse.stations) != size.station_count:
        raise ValueError(
            f"Layout {layout_id} does not match size {size.label}: expected {size.station_count} stations, "
            f"got {len(warehouse.stations)}."
        )
    if warehouse.shelf_count != size.shelf_count:
        raise ValueError(
            f"Layout {layout_id} does not match size {size.label}: expected {size.shelf_count} shelves, "
            f"got {warehouse.shelf_count}."
        )
    try:
        warehouse.delivery_positions()
    except ValueError as exc:
        raise ValueError(f"Layout {layout_id} does not define an accessible delivery area.") from exc
    if max_open_tasks_on_shelves > warehouse.shelf_count:
        raise ValueError(
            f"Layout {layout_id} cannot support MaxOpenTasksOnShelves={max_open_tasks_on_shelves} "
            f"because it exposes only {warehouse.shelf_count} shelves."
        )


def build_layout_context(size_key: str, agent_count: int, layout_id: int) -> LayoutContext:
    size = SIZE_PROFILES[size_key]
    try:
        layout_file = layout_path(layout_id, layout_type="square", layout_size=size_key)
    except FileNotFoundError as exc:
        raise ValueError(
            f"Layout {layout_id} is not available for size {size.label}. "
            f"Agent count {agent_count} requires the {size.label} warehouse profile."
        ) from exc

    warehouse = load_layout(layout_file, "square")
    validate_layout(size, size.shelf_count, layout_id, warehouse)
    homes = assign_home_stations(warehouse, agent_count)
    station_distances = bfs_distances(warehouse, list(warehouse.stations))
    delivery_distances = bfs_distances(warehouse, list(warehouse.delivery_positions()))
    shelf_descriptors = build_shelf_descriptors(warehouse, station_distances, delivery_distances)
    home_distances = all_home_distances(warehouse, homes)
    distances_by_agent = distances_from_homes_to_shelves(shelf_descriptors, home_distances)
    return LayoutContext(
        size_key=size_key,
        layout_id=layout_id,
        warehouse=warehouse,
        shelf_descriptors=shelf_descriptors,
        distances_by_agent=distances_by_agent,
    )
