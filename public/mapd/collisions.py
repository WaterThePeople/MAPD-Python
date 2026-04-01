from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from mapd.models import AgentPlan, Coord


@dataclass(frozen=True)
class FrameCollisionInfo:
    coords: set[Coord]
    pair_count: int


def frame_agent_positions(plans: list[AgentPlan], time: int) -> dict[int, Coord]:
    return {
        plan.agent_id: plan.path[time] if time < len(plan.path) else plan.path[-1]
        for plan in plans
    }


def frame_collision_info(plans: list[AgentPlan], time: int) -> FrameCollisionInfo:
    positions = frame_agent_positions(plans, time)
    occupancy = defaultdict(list)
    for agent_id, coord in positions.items():
        occupancy[coord].append(agent_id)

    collision_coords: set[Coord] = set()
    pair_count = 0

    for coord, agent_ids in occupancy.items():
        if len(agent_ids) > 1:
            collision_coords.add(coord)
            pair_count += len(agent_ids) * (len(agent_ids) - 1) // 2

    if time > 0:
        previous_positions = frame_agent_positions(plans, time - 1)
        agent_ids = sorted(positions)
        for index, first_agent_id in enumerate(agent_ids):
            first_coord = positions[first_agent_id]
            for second_agent_id in agent_ids[index + 1 :]:
                second_coord = positions[second_agent_id]
                if first_coord == second_coord:
                    continue
                if (
                    previous_positions[first_agent_id] == second_coord
                    and previous_positions[second_agent_id] == first_coord
                ):
                    collision_coords.add(first_coord)
                    collision_coords.add(second_coord)
                    pair_count += 1

    return FrameCollisionInfo(coords=collision_coords, pair_count=pair_count)


def total_collision_count(plans: list[AgentPlan]) -> int:
    if not plans:
        return 0
    max_time = max(len(plan.path) for plan in plans) - 1
    return sum(frame_collision_info(plans, time).pair_count for time in range(max_time + 1))
