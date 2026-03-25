import argparse
import re
from pathlib import Path

from mapd.loader import (
    expand_scenario_variants,
    layout_path,
    load_layout,
    load_scenario_definition,
    resolve_scenario_variant,
)
from mapd.planner import build_agent_plans
from mapd.renderer import render_frames
from mapd.results_workbook import (
    COMPARISON_HEADERS,
    SUMMARY_HEADERS,
    TASKS_HEADERS,
    assignment_type_label,
    build_comparison_row,
    build_summary_rows,
    build_tasks_rows,
    write_xlsx_workbook,
)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator with GIF export.")
    parser.add_argument(
        "--layout",
        help=(
            "Optional layout path (.json/.txt) or numeric layout id for a single scenario run. "
            "If omitted, the scenario's Layout value is used."
        ),
    )
    parser.add_argument(
        "--scenario",
        default="scenarios/0/0_map0.txt",
        help="Path to the scenario file.",
    )
    parser.add_argument(
        "--scenario-suite",
        help=(
            "Run all scenario variants for a scenario base name (e.g. '2' or 'scenarios/2'). "
            "Overrides --scenario and --output."
        ),
    )
    parser.add_argument("--output", default="gifs/simulation.gif", help="Path to the output GIF.")
    parser.add_argument("--suite-output-dir", default="gifs", help="Output directory for scenario suite GIFs.")
    parser.add_argument("--results-dir", default="results", help="Directory for scenario suite Excel workbooks.")
    parser.add_argument("--cell-size", type=int, default=48, help="Rendered size of a single cell in pixels.")
    parser.add_argument("--frame-duration", type=int, default=250, help="GIF frame duration in milliseconds.")
    parser.add_argument("--mode", choices=["Set", "Available"], help="Override the scenario Mode for a single run.")
    parser.add_argument(
        "--station",
        choices=["Set", "Available"],
        help="Override the scenario Station mode for a single run.",
    )
    parser.add_argument(
        "--set",
        dest="mode_station_flags",
        action="append_const",
        const="Set",
        help="Shorthand flag: first use fills Mode, second use fills Station.",
    )
    parser.add_argument(
        "--available",
        dest="mode_station_flags",
        action="append_const",
        const="Available",
        help="Shorthand flag: first use fills Mode, second use fills Station.",
    )
    strategy_group = parser.add_mutually_exclusive_group()
    strategy_group.add_argument(
        "--strategy",
        dest="strategy_override",
        choices=["FCFS", "Robin", "GreedyCost", "None"],
        help="Override the scenario strategy for a single run.",
    )
    strategy_group.add_argument("--fcfs", dest="strategy_override", action="store_const", const="FCFS")
    strategy_group.add_argument("--robin", dest="strategy_override", action="store_const", const="Robin")
    strategy_group.add_argument("--greedycost", dest="strategy_override", action="store_const", const="GreedyCost")
    strategy_group.add_argument("--none", dest="strategy_override", action="store_const", const="None")
    algorithm_group = parser.add_mutually_exclusive_group()
    algorithm_group.add_argument(
        "--algorithm",
        dest="algorithm_override",
        choices=["A*", "SIPP", "BFS"],
        help="Override the scenario pathfinding algorithm for a single run.",
    )
    algorithm_group.add_argument("--astar", dest="algorithm_override", action="store_const", const="A*")
    algorithm_group.add_argument("--sipp", dest="algorithm_override", action="store_const", const="SIPP")
    algorithm_group.add_argument("--bfs", dest="algorithm_override", action="store_const", const="BFS")
    parser.add_argument(
        "--debug-frames-dir",
        help=(
            "Export every rendered frame for a single scenario as PNG files in this directory. "
            "Existing frame_*.png files are removed first."
        ),
    )
    parser.add_argument("--no-gif", action="store_true", help="Skip GIF rendering and only compute results.")
    args = parser.parse_args()
    if args.scenario_suite and args.debug_frames_dir:
        parser.error("--debug-frames-dir can only be used with a single --scenario run.")
    if args.scenario_suite and args.layout:
        parser.error("--layout cannot be used with --scenario-suite because each scenario selects its own layout.")
    mode_station_flags = list(args.mode_station_flags or [])
    missing_slots = int(args.mode is None) + int(args.station is None)
    if len(mode_station_flags) > missing_slots:
        parser.error("Too many --set/--available shorthand flags for the requested Mode/Station overrides.")
    if args.mode is None and mode_station_flags:
        args.mode = mode_station_flags.pop(0)
    if args.station is None and mode_station_flags:
        args.station = mode_station_flags.pop(0)
    if args.scenario_suite and any(
        value is not None for value in (args.mode, args.station, args.strategy_override, args.algorithm_override)
    ):
        parser.error("Variant override flags can only be used with a single --scenario run.")
    return args


def summary_lines(
    plans,
    makespan: int,
    output_path: Path | None,
    station_mode: str,
    gif_rendered: bool,
    debug_frames_dir: Path | None = None,
) -> list[str]:
    lines = []
    if gif_rendered:
        lines.append(f"[done] Generated GIF: {output_path}")
    else:
        lines.append("[done] GIF rendering skipped")
    if debug_frames_dir is not None:
        lines.append(f"[done] Exported {makespan + 1} debug frames: {debug_frames_dir}")
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


def print_summary(
    plans,
    makespan: int,
    output_path: Path | None,
    station_mode: str,
    gif_rendered: bool,
    debug_frames_dir: Path | None = None,
) -> None:
    for line in summary_lines(plans, makespan, output_path, station_mode, gif_rendered, debug_frames_dir):
        print(line)


def derive_suite_paths(suite_arg: str) -> tuple[str, list[Path]]:
    suite_path = Path(suite_arg)
    if suite_path.is_dir():
        scenarios_dir = suite_path
    else:
        candidate_dir = Path("scenarios") / suite_arg
        if candidate_dir.is_dir():
            scenarios_dir = candidate_dir
        elif suite_path.is_file():
            scenarios_dir = suite_path.parent
        else:
            raise FileNotFoundError(f"Suite directory not found: {suite_arg}")

    suite_name = scenarios_dir.name
    all_paths = sorted(scenarios_dir.glob("*.txt"))
    mapped_paths = [path for path in all_paths if re.fullmatch(rf"{re.escape(suite_name)}_map\d+\.txt", path.name, re.IGNORECASE)]
    paths = mapped_paths or all_paths
    return suite_name, paths


def variant_filename_token(value: str) -> str:
    token_map = {
        "Set": "set",
        "Available": "available",
        "FCFS": "fcfs",
        "Robin": "robin",
        "GreedyCost": "greedycost",
        "None": "none",
        "A*": "astar",
        "SIPP": "sipp",
        "BFS": "bfs",
    }
    return token_map[value]


def variant_label(scenario_label: str, mode: str, station_mode: str, strategy: str, algorithm: str) -> str:
    return (
        f"{scenario_label}_{variant_filename_token(mode)}_{variant_filename_token(station_mode)}_"
        f"{variant_filename_token(strategy)}_{variant_filename_token(algorithm)}"
    )


def resolve_layout_reference(layout_arg: str | None, scenario_layout_id: int) -> tuple[int, Path]:
    if layout_arg is None:
        return scenario_layout_id, layout_path(scenario_layout_id)

    if layout_arg.isdigit():
        override_layout_id = int(layout_arg)
        return override_layout_id, layout_path(override_layout_id)

    override_path = Path(layout_arg)
    if not override_path.exists():
        raise FileNotFoundError(f"Layout override not found: {layout_arg}")

    layout_id_match = re.search(r"(\d+)(?=\.(?:json|txt)$)", override_path.name, re.IGNORECASE)
    layout_id = int(layout_id_match.group(1)) if layout_id_match else scenario_layout_id
    return layout_id, override_path


def run_simulation(
    warehouse,
    agent_count: int,
    tasks,
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    output_path: Path | None,
    cell_size: int,
    frame_duration: int,
    progress: bool,
    render_gif: bool,
    debug_frames_dir: Path | None = None,
) -> tuple[int, list]:
    plans = build_agent_plans(warehouse, agent_count, tasks, mode, station_mode, strategy, algorithm)
    if render_gif or debug_frames_dir is not None:
        if render_gif and output_path is None:
            raise ValueError("Output path must be provided when rendering GIFs.")
        makespan = render_frames(
            warehouse=warehouse,
            plans=plans,
            output_path=output_path if render_gif else None,
            cell_size=cell_size,
            frame_duration_ms=frame_duration,
            progress=progress,
            debug_frames_dir=debug_frames_dir,
        )
    else:
        makespan = max((len(plan.path) for plan in plans), default=1) - 1
    return makespan, plans


def main() -> None:
    args = parse_args()

    if args.scenario_suite:
        suite_name, scenario_paths = derive_suite_paths(args.scenario_suite)
        missing = [str(path) for path in scenario_paths if not path.exists()]
        if missing:
            raise FileNotFoundError("Missing scenario files: " + ", ".join(missing))

        tasks_rows = [TASKS_HEADERS]
        summary_rows = [SUMMARY_HEADERS]
        comparison_rows = [COMPARISON_HEADERS]
        output_dir = Path(args.suite_output_dir)
        for scenario_path in scenario_paths:
            print(f"[2/4] Loading scenario from {scenario_path}")
            definition = load_scenario_definition(scenario_path)
            resolved_layout_id, resolved_layout_path = resolve_layout_reference(None, definition.layout_id)
            warehouse = load_layout(resolved_layout_path)
            print(f"[2/4] Layout loaded from {resolved_layout_path}: {warehouse.width}x{warehouse.height}")
            for variant in expand_scenario_variants(definition):
                scenario_variant_label = variant_label(
                    scenario_path.stem,
                    variant.mode,
                    variant.station_mode,
                    variant.strategy,
                    variant.algorithm,
                )
                output_path = None if args.no_gif else output_dir / f"{scenario_variant_label}.gif"
                print(
                    f"[2/4] Scenario variant: {definition.agent_count} agents, {len(definition.tasks)} tasks, "
                    f"mode={variant.mode}, station={variant.station_mode}, strategy={variant.strategy}, "
                    f"algorithm={variant.algorithm}, layout={resolved_layout_id}"
                )
                makespan, plans = run_simulation(
                    warehouse,
                    definition.agent_count,
                    definition.tasks,
                    variant.mode,
                    variant.station_mode,
                    variant.strategy,
                    variant.algorithm,
                    output_path,
                    args.cell_size,
                    args.frame_duration,
                    progress=True,
                    render_gif=not args.no_gif,
                )
                assignment_type = assignment_type_label(variant.mode, variant.station_mode)
                tasks_rows.extend(
                    build_tasks_rows(
                        suite_name,
                        resolved_layout_id,
                        variant.strategy,
                        variant.algorithm,
                        assignment_type,
                        makespan,
                        plans,
                    )[1:]
                )
                summary_rows.extend(
                    build_summary_rows(
                        suite_name,
                        resolved_layout_id,
                        variant.strategy,
                        variant.algorithm,
                        assignment_type,
                        plans,
                    )[1:]
                )
                comparison_rows.append(
                    build_comparison_row(
                        suite_name,
                        resolved_layout_id,
                        variant.strategy,
                        variant.algorithm,
                        assignment_type,
                        makespan,
                        plans,
                    )
                )

        results_path = Path(args.results_dir) / f"{suite_name}_results.xlsx"
        write_xlsx_workbook(
            results_path,
            [
                ("Tasks", tasks_rows),
                ("Agent Summary", summary_rows),
                ("Overall Comparison", comparison_rows),
            ],
        )
        print(f"[done] Wrote suite results: {results_path}")
        return

    scenario_path = Path(args.scenario)
    print(f"[1/4] Loading scenario from {scenario_path}")
    definition = load_scenario_definition(scenario_path)
    variant = resolve_scenario_variant(
        definition,
        mode=args.mode,
        station_mode=args.station,
        strategy=args.strategy_override,
        algorithm=args.algorithm_override,
    )
    print(
        f"[1/4] Scenario loaded: {definition.agent_count} agents, {len(definition.tasks)} tasks, "
        f"mode={variant.mode}, station={variant.station_mode}, strategy={variant.strategy}, "
        f"algorithm={variant.algorithm}, layout={definition.layout_id}"
    )

    resolved_layout_id, resolved_layout_path = resolve_layout_reference(args.layout, definition.layout_id)
    print(f"[2/4] Loading layout from {resolved_layout_path}")
    warehouse = load_layout(resolved_layout_path)
    print(f"[2/4] Layout loaded: {warehouse.width}x{warehouse.height} (layout={resolved_layout_id})")

    print("[3/4] Planning collision-free routes")
    plans = build_agent_plans(
        warehouse,
        definition.agent_count,
        definition.tasks,
        variant.mode,
        variant.station_mode,
        variant.strategy,
        variant.algorithm,
    )
    print("[3/4] Route planning finished")

    output_path = None if args.no_gif else Path(args.output)
    debug_frames_dir = Path(args.debug_frames_dir) if args.debug_frames_dir else None
    if args.no_gif and debug_frames_dir is None:
        print("[4/4] Skipping GIF rendering")
        makespan = max((len(plan.path) for plan in plans), default=1) - 1
    else:
        if output_path is not None and debug_frames_dir is not None:
            print(f"[4/4] Rendering GIF to {output_path} and exporting frames to {debug_frames_dir}")
        elif output_path is not None:
            print(f"[4/4] Rendering GIF to {output_path}")
        else:
            print(f"[4/4] Exporting frames to {debug_frames_dir}")
        makespan = render_frames(
            warehouse=warehouse,
            plans=plans,
            output_path=output_path,
            cell_size=args.cell_size,
            frame_duration_ms=args.frame_duration,
            progress=True,
            debug_frames_dir=debug_frames_dir,
        )

    print_summary(plans, makespan, output_path, variant.station_mode, not args.no_gif, debug_frames_dir)


if __name__ == "__main__":
    main()
