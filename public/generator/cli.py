from __future__ import annotations

import argparse
import random
from pathlib import Path

from .capacity import estimate_batch_capacity_steps_per_task
from .constants import SIZE_PROFILES
from .definitions import BatchConfig, LayoutContext
from .layouts import build_layout_context
from .scenarios import generate_batch, save_batch
from mapd.paths import SCENARIOS_ROOT


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0.")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("Value must be >= 0.")
    return parsed


def parse_layout_ids(value: str) -> tuple[int, ...]:
    layout_ids: list[int] = []
    for item in value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        if not stripped.isdigit():
            raise argparse.ArgumentTypeError(
                "Layouts must be a comma-separated list of non-negative integers, for example: 0,1,2"
            )
        layout_id = int(stripped)
        if layout_id not in layout_ids:
            layout_ids.append(layout_id)

    if not layout_ids:
        raise argparse.ArgumentTypeError("At least one layout id must be provided.")
    return tuple(layout_ids)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a batch of MAPD scenarios for every combination of "
            "Influx={Random,Gaussian,Burst} and SpatialDistribution={Uniform,Hotspot,Wave}."
        )
    )
    parser.add_argument("agents", type=positive_int, help="Number of agents.")
    parser.add_argument("task_count", type=positive_int, help="Number of tasks to distribute across the scenario.")
    parser.add_argument(
        "layouts",
        type=parse_layout_ids,
        help="Comma-separated layout ids, for example 0 or 0,1,2.",
    )
    parser.add_argument("--seed", type=non_negative_int, help="Shared batch seed. Generated automatically when omitted.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=SCENARIOS_ROOT,
        help="Directory where the scenario batch folder will be created.",
    )
    return parser


def resolve_seed(seed: int | None) -> int:
    if seed is not None:
        return seed
    return random.SystemRandom().randrange(1, 1_000_000_000)


def determine_size_key(agents: int) -> str:
    for size_key, profile in SIZE_PROFILES.items():
        if profile.min_agents <= agents <= profile.max_agents:
            return size_key
    raise ValueError(
        "Agent count is outside supported ranges: "
        + ", ".join(
            f"{profile.label}={profile.min_agents}-{profile.max_agents}"
            for profile in SIZE_PROFILES.values()
        )
        + "."
    )


def resolve_batch_config(
    args: argparse.Namespace,
    layout_contexts: dict[int, LayoutContext],
) -> BatchConfig:
    size_key = determine_size_key(args.agents)
    seed = resolve_seed(args.seed)
    capacity_steps_per_task = estimate_batch_capacity_steps_per_task(layout_contexts)
    config = BatchConfig(
        agents=args.agents,
        task_count=args.task_count,
        layout_ids=args.layouts,
        capacity_steps_per_task=capacity_steps_per_task,
        seed=seed,
        output_root=args.output_root,
        size_key=size_key,
    )

    if config.task_count < 1:
        raise ValueError("TaskCount must be at least 1.")
    return config


def build_layout_contexts(size_key: str, agents: int, layout_ids: tuple[int, ...]) -> dict[int, LayoutContext]:
    return {
        layout_id: build_layout_context(size_key, agents, layout_id)
        for layout_id in layout_ids
    }


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        size_key = determine_size_key(args.agents)
        layout_contexts = build_layout_contexts(size_key, args.agents, args.layouts)
        batch = resolve_batch_config(args, layout_contexts)
        scenarios = generate_batch(batch, layout_contexts)
        save_batch(scenarios)
    except ValueError as exc:
        parser.exit(2, f"Error: {exc}\n")

    print(
        f"Generated {len(scenarios)} scenarios in '{batch.scenario_directory}' "
        f"for layouts {', '.join(str(layout_id) for layout_id in batch.layout_ids)}."
    )
