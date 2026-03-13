import argparse
from pathlib import Path
from typing import Iterable

from mapd.loader import load_layout, load_scenario
from mapd.planner import build_agent_plans
from mapd.renderer import render_frames

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator with GIF export.")
    parser.add_argument("--layout", default="maps/layout.txt", help="Path to the warehouse layout file.")
    parser.add_argument(
        "--scenario",
        default="maps/scenarios/0/0_set_set_none.txt",
        help="Path to the scenario file.",
    )
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
    parser.add_argument("--no-gif", action="store_true", help="Skip GIF rendering and only compute results.")
    return parser.parse_args()


def summary_lines(
    plans,
    makespan: int,
    output_path: Path | None,
    station_mode: str,
    gif_rendered: bool,
) -> list[str]:
    lines = []
    if gif_rendered:
        lines.append(f"[done] Generated GIF: {output_path}")
    else:
        lines.append("[done] GIF rendering skipped")
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


def print_summary(plans, makespan: int, output_path: Path | None, station_mode: str, gif_rendered: bool) -> None:
    for line in summary_lines(plans, makespan, output_path, station_mode, gif_rendered):
        print(line)


def derive_suite_paths(suite_arg: str) -> tuple[str, list[Path]]:
    suite_path = Path(suite_arg)
    if suite_path.is_dir():
        scenarios_dir = suite_path
    else:
        candidate_dir = Path("maps/scenarios") / suite_arg
        if candidate_dir.is_dir():
            scenarios_dir = candidate_dir
        elif suite_path.is_file():
            scenarios_dir = suite_path.parent
        else:
            raise FileNotFoundError(f"Suite directory not found: {suite_arg}")

    suite_name = scenarios_dir.name
    paths = sorted(scenarios_dir.glob("*.txt"))
    return suite_name, paths


def run_simulation(
    warehouse,
    scenario_path: Path,
    output_path: Path | None,
    cell_size: int,
    frame_duration: int,
    progress: bool,
    render_gif: bool,
) -> tuple[int, list, str, str, str]:
    agent_count, tasks, mode, station_mode, strategy = load_scenario(scenario_path)
    plans = build_agent_plans(warehouse, agent_count, tasks, mode, station_mode, strategy)
    if render_gif:
        if output_path is None:
            raise ValueError("Output path must be provided when rendering GIFs.")
        makespan = render_frames(
            warehouse=warehouse,
            plans=plans,
            output_path=output_path,
            cell_size=cell_size,
            frame_duration_ms=frame_duration,
            progress=progress,
        )
    else:
        makespan = max((len(plan.path) for plan in plans), default=1) - 1
    return makespan, plans, mode, station_mode, strategy


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
            output_path = None if args.no_gif else output_dir / f"{scenario_label}.gif"
            print(f"[2/4] Loading scenario from {scenario_path}")
            makespan, plans, mode, station_mode, strategy = run_simulation(
                warehouse,
                scenario_path,
                output_path,
                args.cell_size,
                args.frame_duration,
                progress=True,
                render_gif=not args.no_gif,
            )
            summary = summary_lines(plans, makespan, output_path, station_mode, not args.no_gif)
            summary.insert(0, f"[done] Strategy: {strategy}")
            sections.append((scenario_label, summary))

        results_path = Path(args.results_dir) / f"{suite_name}_results.txt"
        write_suite_results(results_path, suite_name, sections)
        print(f"[done] Wrote suite results: {results_path}")
        return

    print(f"[2/4] Loading scenario from {args.scenario}")
    agent_count, tasks, mode, station_mode, strategy = load_scenario(Path(args.scenario))
    print(
        f"[2/4] Scenario loaded: {agent_count} agents, {len(tasks)} tasks, "
        f"mode={mode}, station={station_mode}, strategy={strategy}"
    )

    print("[3/4] Planning collision-free routes")
    plans = build_agent_plans(warehouse, agent_count, tasks, mode, station_mode, strategy)
    print("[3/4] Route planning finished")

    if args.no_gif:
        print("[4/4] Skipping GIF rendering")
        makespan = max((len(plan.path) for plan in plans), default=1) - 1
        output_path = None
    else:
        print(f"[4/4] Rendering GIF to {args.output}")
        output_path = Path(args.output)
        makespan = render_frames(
            warehouse=warehouse,
            plans=plans,
            output_path=output_path,
            cell_size=args.cell_size,
            frame_duration_ms=args.frame_duration,
            progress=True,
        )

    print_summary(plans, makespan, output_path, station_mode, not args.no_gif)


if __name__ == "__main__":
    main()
