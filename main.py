import argparse
from pathlib import Path

from mapd.loader import load_layout, load_scenario
from mapd.planner import build_agent_plans
from mapd.renderer import render_frames

scenarios = ["maps/scenarios/0.txt","maps/scenarios/1.txt","maps/scenarios/2.txt","maps/scenarios/3.txt"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator with GIF export.")
    parser.add_argument("--layout", default="maps/layout.txt", help="Path to the warehouse layout file.")
    parser.add_argument("--scenario", default=scenarios[0], help="Path to the scenario file.")
    parser.add_argument("--output", default="gifs/simulation.gif", help="Path to the output GIF.")
    parser.add_argument("--cell-size", type=int, default=48, help="Rendered size of a single cell in pixels.")
    parser.add_argument("--frame-duration", type=int, default=250, help="GIF frame duration in milliseconds.")
    return parser.parse_args()


def print_summary(plans, makespan: int, output_path: Path) -> None:
    print(f"[done] Generated GIF: {output_path}")
    print(f"[done] Makespan: {makespan} steps")
    print()
    for plan in plans:
        task_parts = []
        for task in plan.tasks:
            deadline = "none" if task.deadline is None else str(task.deadline)
            completion = plan.completion_times.get(task.task_id)
            late = ""
            if task.deadline is not None and completion is not None and completion > task.deadline:
                late = f",late={completion - task.deadline}"
            task_parts.append(
                f"{task.task_id}@{task.location_index}[t={task.release_time},d={deadline}{late}]"
            )

        task_description = ", ".join(task_parts) or "no tasks"
        print(
            f"Agent {plan.agent_id}: station {plan.home_index}, "
            f"path length {len(plan.path) - 1}, tasks [{task_description}]"
        )

    late_tasks = []
    for plan in plans:
        if plan.missed_deadlines:
            for task_id in plan.missed_deadlines:
                late_tasks.append((plan.agent_id, task_id))
    if late_tasks:
        print()
        print("[warn] Missed deadlines:")
        for agent_id, task_id in late_tasks:
            print(f"  agent {agent_id}, task {task_id}")


def main() -> None:
    args = parse_args()

    print(f"[1/4] Loading layout from {args.layout}")
    warehouse = load_layout(Path(args.layout))
    print(f"[1/4] Layout loaded: {warehouse.width}x{warehouse.height}")

    print(f"[2/4] Loading scenario from {args.scenario}")
    agent_count, tasks, mode = load_scenario(Path(args.scenario))
    print(f"[2/4] Scenario loaded: {agent_count} agents, {len(tasks)} tasks, mode={mode}")

    print("[3/4] Planning collision-free routes")
    plans = build_agent_plans(warehouse, agent_count, tasks, mode)
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
