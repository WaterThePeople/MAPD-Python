from __future__ import annotations

import argparse
from pathlib import Path

from mapd.loader import load_layout, load_scenario
from mapd.planner import build_agent_plans
from mapd.renderer import render_frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator with GIF export.")
    parser.add_argument("--layout", default="maps/layout.txt", help="Path to the warehouse layout file.")
    parser.add_argument("--scenario", default="maps/scenario.txt", help="Path to the scenario file.")
    parser.add_argument("--output", default="simulation.gif", help="Path to the output GIF.")
    parser.add_argument("--cell-size", type=int, default=48, help="Rendered size of a single cell in pixels.")
    parser.add_argument("--frame-duration", type=int, default=250, help="GIF frame duration in milliseconds.")
    return parser.parse_args()


def print_summary(plans, makespan: int, output_path: Path) -> None:
    print(f"[done] Generated GIF: {output_path}")
    print(f"[done] Makespan: {makespan} steps")
    print()
    for plan in plans:
        task_description = ", ".join(f"{task.task_id}@{task.location_index}" for task in plan.tasks) or "no tasks"
        print(
            f"Agent {plan.agent_id}: station {plan.home_index}, "
            f"path length {len(plan.path) - 1}, tasks [{task_description}]"
        )


def main() -> None:
    args = parse_args()

    print(f"[1/4] Loading layout from {args.layout}")
    warehouse = load_layout(Path(args.layout))
    print(f"[1/4] Layout loaded: {warehouse.width}x{warehouse.height}")

    print(f"[2/4] Loading scenario from {args.scenario}")
    agent_count, tasks = load_scenario(Path(args.scenario))
    print(f"[2/4] Scenario loaded: {agent_count} agents, {len(tasks)} tasks")

    print("[3/4] Planning collision-free routes")
    plans = build_agent_plans(warehouse, agent_count, tasks)
    print("[3/4] Route planning finished")

    print(f"[4/4] Rendering GIF to {args.output}")
    makespan = render_frames(
        warehouse=warehouse,
        plans=plans,
        output_path=Path(args.output),
        cell_size=args.cell_size,
        frame_duration_ms=args.frame_duration,
        progress=True,
    )

    print_summary(plans, makespan, Path(args.output))


if __name__ == "__main__":
    main()
