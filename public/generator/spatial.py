from __future__ import annotations

import math
import random

from mapd.models import Coord
from mapd.warehouse import WarehouseMap

from .definitions import ShelfDescriptor


def normalize_wave_zone(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "center": "center",
        "centre": "center",
        "random": "random",
        "randomedge": "randomedge",
        "random_edge": "randomedge",
        "edge": "randomedge",
        "north": "north",
        "south": "south",
        "west": "west",
        "east": "east",
    }
    return aliases.get(normalized, normalized)


def choose_wave_anchor(
    wave_zone: str,
    warehouse: WarehouseMap,
    shelf_descriptors: list[ShelfDescriptor],
    rng: random.Random,
) -> Coord:
    zone = normalize_wave_zone(wave_zone)
    if zone == "center":
        return (warehouse.height // 2, warehouse.width // 2)
    if zone == "random":
        descriptor = rng.choice(shelf_descriptors)
        return descriptor.coord
    if zone == "randomedge":
        edge_shelves = [
            descriptor
            for descriptor in shelf_descriptors
            if min(
                descriptor.coord[0],
                descriptor.coord[1],
                warehouse.height - 1 - descriptor.coord[0],
                warehouse.width - 1 - descriptor.coord[1],
            )
            <= max(1, min(warehouse.height, warehouse.width) // 6)
        ]
        descriptor = rng.choice(edge_shelves or shelf_descriptors)
        return descriptor.coord
    if zone == "north":
        return (0, warehouse.width // 2)
    if zone == "south":
        return (warehouse.height - 1, warehouse.width // 2)
    if zone == "west":
        return (warehouse.height // 2, 0)
    if zone == "east":
        return (warehouse.height // 2, warehouse.width - 1)
    raise ValueError(
        f"Unsupported wave zone '{wave_zone}'. "
        "Use Center, Random, RandomEdge, North, South, West or East."
    )


def build_spatial_weights(
    warehouse: WarehouseMap,
    shelf_descriptors: list[ShelfDescriptor],
    spatial_distribution: str,
    rng: random.Random,
    *,
    hotspot_shelf_share: float,
    hotspot_task_share: float,
    wave_zone: str,
    wave_radius: int,
) -> dict[int, float]:
    if spatial_distribution == "Uniform":
        return {descriptor.shelf_index: 1.0 for descriptor in shelf_descriptors}

    if spatial_distribution == "Hotspot":
        ordered = sorted(shelf_descriptors, key=lambda descriptor: (descriptor.station_distance, descriptor.shelf_index))
        hotspot_count = max(1, int(round(len(ordered) * hotspot_shelf_share)))
        hotspot_indexes = {descriptor.shelf_index for descriptor in ordered[:hotspot_count]}
        non_hotspot_count = max(1, len(ordered) - len(hotspot_indexes))
        hotspot_weight = hotspot_task_share / len(hotspot_indexes)
        non_hotspot_weight = (
            (1.0 - hotspot_task_share) / non_hotspot_count if len(ordered) > len(hotspot_indexes) else hotspot_weight
        )
        return {
            descriptor.shelf_index: hotspot_weight if descriptor.shelf_index in hotspot_indexes else non_hotspot_weight
            for descriptor in shelf_descriptors
        }

    anchor = choose_wave_anchor(wave_zone, warehouse, shelf_descriptors, rng)
    sigma = max(1.0, wave_radius / 2)
    weights = {}
    for descriptor in shelf_descriptors:
        distance = warehouse.distance(descriptor.coord, anchor)
        weights[descriptor.shelf_index] = 0.15 + math.exp(-((distance**2) / (2 * sigma * sigma)))
    return weights


def weighted_choice(indexes: list[int], weights_by_index: dict[int, float], rng: random.Random) -> int:
    weights = [weights_by_index[index] for index in indexes]
    return rng.choices(indexes, weights=weights, k=1)[0]


def normalized_weights(weights_by_shelf: dict[int, float]) -> dict[int, float]:
    total_weight = sum(weights_by_shelf.values())
    if total_weight <= 0:
        raise ValueError("Spatial weights must sum to a positive value.")
    return {shelf_index: weight / total_weight for shelf_index, weight in weights_by_shelf.items()}
