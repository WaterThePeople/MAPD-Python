from __future__ import annotations

import random

from .constants import (
    DEFAULT_HOTSPOT_SHELF_SHARE,
    DEFAULT_HOTSPOT_TASK_SHARE,
    DEFAULT_WAVE_ZONE,
    SIZE_PROFILES,
    SPATIAL_DISTRIBUTIONS,
)
from .definitions import LayoutContext
from .spatial import build_spatial_weights, normalized_weights


def expected_service_steps_for_agent(
    layout: LayoutContext,
    agent_id: int,
    normalized_weights_by_shelf: dict[int, float],
) -> float:
    expected_steps = 0.0
    delivery_distance_by_shelf = {
        descriptor.shelf_index: descriptor.delivery_distance
        for descriptor in layout.shelf_descriptors
    }
    for shelf_index, weight in normalized_weights_by_shelf.items():
        distance_to_pickup = layout.distances_by_agent[agent_id][shelf_index]
        if distance_to_pickup is None:
            continue
        expected_steps += weight * (distance_to_pickup + delivery_distance_by_shelf[shelf_index])
    return expected_steps


def estimate_layout_capacity_steps_per_task(layout: LayoutContext) -> float:
    worst_expected_steps = 0.0
    for spatial_distribution in SPATIAL_DISTRIBUTIONS:
        weights_by_shelf = build_spatial_weights(
            layout.warehouse,
            layout.shelf_descriptors,
            spatial_distribution,
            random.Random(0),
            hotspot_shelf_share=DEFAULT_HOTSPOT_SHELF_SHARE,
            hotspot_task_share=DEFAULT_HOTSPOT_TASK_SHARE,
            wave_zone=DEFAULT_WAVE_ZONE,
            wave_radius=SIZE_PROFILES[layout.size_key].default_wave_radius,
        )
        normalized = normalized_weights(weights_by_shelf)
        distribution_worst_agent = max(
            expected_service_steps_for_agent(layout, agent_id, normalized)
            for agent_id in range(len(layout.distances_by_agent))
        )
        worst_expected_steps = max(worst_expected_steps, distribution_worst_agent)
    if worst_expected_steps <= 0:
        raise ValueError("Failed to estimate a positive task service cost for the selected layout.")
    return worst_expected_steps


def estimate_batch_capacity_steps_per_task(layout_contexts: dict[int, LayoutContext]) -> float:
    if not layout_contexts:
        raise ValueError("At least one layout is required to estimate capacity.")
    return max(estimate_layout_capacity_steps_per_task(layout) for layout in layout_contexts.values())
