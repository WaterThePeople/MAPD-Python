import argparse
from pathlib import Path
from typing import Iterable

from mapd.loader import load_layout, load_scenario
from mapd.planner import build_agent_plans
from mapd.renderer import render_frames

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator with GIF export.")
    parser.add_argument("--layout", default="maps/layout.txt", help="Path to the warehouse layout file.")
    parser.add_argument("--scenario", default="maps/scenarios/0_set_set.txt", help="Path to the scenario file.")
    parser.add_argument(
        "--scenario-suite",
        help=(
            "Run all 4 variants for a scenario base name (e.g. '2' or 'maps/scenarios/2'). "
            "Overrides --scenario and --output."
        ),
    )
    parser.add_argument("--output", default="gifs/simulation.gif", help="Path to the output GIF.")
    parser.add_argument("--suite-output-dir", default="gifs", help="Output directory for scenario suite GIFs.")
    parser.add_argument("--results-dir", default="results", help="Directory for scenario suite results file.")
    parser.add_argument("--cell-size", type=int, default=48, help="Rendered size of a single cell in pixels.")
    parser.add_argument("--frame-duration", type=int, default=250, help="GIF frame duration in milliseconds.")
    return parser.parse_args()


def summary_lines(plans, makespan: int, output_path: Path, station_mode: str) -> list[str]:
    lines = []
    lines.append(f"[done] Generated GIF: {output_path}")
    lines.append(f"[done] Makespan: {makespan} steps")
    lines.append("")
    station_label = "station" if station_mode == "Set" else "start station"
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
        lines.append(
            f"Agent {plan.agent_id}: {station_label} {plan.home_index}, "
            f"path length {len(plan.path) - 1}, tasks [{task_description}]"
        )

    late_tasks = []
    for plan in plans:
        if plan.missed_deadlines:
            for task_id in plan.missed_deadlines:
                late_tasks.append((plan.agent_id, task_id))
    if late_tasks:
        lines.append("")
        lines.append("[warn] Missed deadlines:")
        for agent_id, task_id in late_tasks:
            lines.append(f"  agent {agent_id}, task {task_id}")
    return lines


def print_summary(plans, makespan: int, output_path: Path, station_mode: str) -> None:
    for line in summary_lines(plans, makespan, output_path, station_mode):
        print(line)


def derive_suite_paths(suite_arg: str) -> tuple[str, list[Path]]:
    suffixes = ["_set_set", "_set_available", "_available_set", "_available_available"]
    suite_path = Path(suite_arg)

    if suite_path.suffix:
        stem = suite_path.stem
        base = stem
        for suffix in suffixes:
            if stem.endswith(suffix):
                base = stem[: -len(suffix)]
                break
        scenarios_dir = suite_path.parent
    elif suite_path.parent != Path("."):
        scenarios_dir = suite_path.parent
        base = suite_path.name
    else:
        scenarios_dir = Path("maps/scenarios")
        base = suite_arg

    paths = [scenarios_dir / f"{base}{suffix}.txt" for suffix in suffixes]
    return base, paths


def run_simulation(
    warehouse,
    scenario_path: Path,
    output_path: Path,
    cell_size: int,
    frame_duration: int,
    progress: bool,
) -> tuple[int, list, str, str]:
    agent_count, tasks, mode, station_mode = load_scenario(scenario_path)
    plans = build_agent_plans(warehouse, agent_count, tasks, mode, station_mode)
    makespan = render_frames(
        warehouse=warehouse,
        plans=plans,
        output_path=output_path,
        cell_size=cell_size,
        frame_duration_ms=frame_duration,
        progress=progress,
    )
    return makespan, plans, mode, station_mode


def write_suite_results(
    results_path: Path,
    suite_name: str,
    sections: Iterable[tuple[str, list[str]]],
) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append(f"Scenario suite: {suite_name}")
    lines.append("")
    for title, summary in sections:
        lines.append(f"== {title} ==")
        lines.extend(summary)
        lines.append("")
    results_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()

    print(f"[1/4] Loading layout from {args.layout}")
    warehouse = load_layout(Path(args.layout))
    print(f"[1/4] Layout loaded: {warehouse.width}x{warehouse.height}")

    if args.scenario_suite:
        suite_name, scenario_paths = derive_suite_paths(args.scenario_suite)
        missing = [str(path) for path in scenario_paths if not path.exists()]
        if missing:
            raise FileNotFoundError("Missing scenario files: " + ", ".join(missing))

        sections = []
        output_dir = Path(args.suite_output_dir)
        for scenario_path in scenario_paths:
            scenario_label = scenario_path.stem
            output_path = output_dir / f"{scenario_label}.gif"
            print(f"[2/4] Loading scenario from {scenario_path}")
            makespan, plans, mode, station_mode = run_simulation(
                warehouse,
                scenario_path,
                output_path,
                args.cell_size,
                args.frame_duration,
                progress=True,
            )
            summary = summary_lines(plans, makespan, output_path, station_mode)
            sections.append((scenario_label, summary))

        results_path = Path(args.results_dir) / f"{suite_name}_results.txt"
        write_suite_results(results_path, suite_name, sections)
        print(f"[done] Wrote suite results: {results_path}")
        return

    print(f"[2/4] Loading scenario from {args.scenario}")
    agent_count, tasks, mode, station_mode = load_scenario(Path(args.scenario))
    print(
        f"[2/4] Scenario loaded: {agent_count} agents, {len(tasks)} tasks, "
        f"mode={mode}, station={station_mode}"
    )

    print("[3/4] Planning collision-free routes")
    plans = build_agent_plans(warehouse, agent_count, tasks, mode, station_mode)
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

    print_summary(plans, makespan, Path(args.output), station_mode)


if __name__ == "__main__":
    main()
