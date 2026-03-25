import argparse
import re
import sys
from pathlib import Path

from mapd.loader import (
    expand_scenario_variants,
    layout_path,
    load_layout,
    load_scenario_definition,
    normalize_layout_type,
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

# Default settings
DEFAULT_LAYOUT = "0.json"
DEFAULT_LAYOUT_TYPE = "square"
DEFAULT_SCENARIO = "0_map0.txt"
DEFAULT_SCENARIO_SUITE = "0"
DEFAULT_OUTPUT = "simulation.gif"
DEFAULT_SUITE_OUTPUT_DIR = "gifs"
DEFAULT_RESULTS_DIR = "results"
DEFAULT_CELL_SIZE = 48
DEFAULT_FRAME_DURATION = 250
DEFAULT_MODE = "Set"
DEFAULT_STATION = "Set"
DEFAULT_STRATEGY = "None"
DEFAULT_ALGORITHM = "BFS"
DEFAULT_RENDER_GIF = False
DEFAULT_DEBUGGING = False
DEFAULT_DEBUG_FRAMES_ROOT = Path("debugging")

LAYOUT_TYPE_CHOICES = ["square", "hexagon"]
MODE_CHOICES = ["Set", "Available"]
STRATEGY_CHOICES = ["FCFS", "Robin", "GreedyCost", "None"]
ALGORITHM_CHOICES = ["A*", "SIPP", "BFS"]
SUITE_ONLY_ALLOWED_FLAGS = {
    "--gif",
    "--suite-output-dir",
    "--results-dir",
    "--cell-size",
    "--frame-duration",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proof-of-concept MAPD simulator.")
    parser.add_argument(
        "--scenario",
        help=f"Scenario file name or path. Default: {DEFAULT_SCENARIO}",
    )
    parser.add_argument(
        "--suite",
        nargs="?",
        const=DEFAULT_SCENARIO_SUITE,
        help=(
            "Run every variant from a scenario suite directory, for example '--suite 2'. "
            f"If the value is omitted, '{DEFAULT_SCENARIO_SUITE}' is used."
        ),
    )
    parser.add_argument(
        "--layout",
        help=f"Layout id, file name or path for a single scenario run. Default: {DEFAULT_LAYOUT}",
    )
    parser.add_argument(
        "--type",
        dest="layout_type",
        choices=LAYOUT_TYPE_CHOICES,
        help=f"Cell geometry for a single scenario run. Default: {DEFAULT_LAYOUT_TYPE}",
    )
    parser.add_argument(
        "--output",
        help=f"GIF path for a single scenario run. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--suite-output-dir",
        default=DEFAULT_SUITE_OUTPUT_DIR,
        help=f"GIF output directory for suite runs. Default: {DEFAULT_SUITE_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--results-dir",
        default=DEFAULT_RESULTS_DIR,
        help=f"Excel output directory for suite runs. Default: {DEFAULT_RESULTS_DIR}",
    )
    parser.add_argument(
        "--cell-size",
        type=int,
        default=DEFAULT_CELL_SIZE,
        help=f"Rendered size of a single cell in pixels. Default: {DEFAULT_CELL_SIZE}",
    )
    parser.add_argument(
        "--frame-duration",
        type=int,
        default=DEFAULT_FRAME_DURATION,
        help=f"GIF frame duration in milliseconds. Default: {DEFAULT_FRAME_DURATION}",
    )
    parser.add_argument(
        "--mode",
        choices=MODE_CHOICES,
        help=f"Task assignment mode for a single scenario run. Default: {DEFAULT_MODE}",
    )
    parser.add_argument(
        "--station",
        choices=MODE_CHOICES,
        help=f"Station assignment mode for a single scenario run. Default: {DEFAULT_STATION}",
    )
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        help=f"Strategy for a single scenario run. Default: {DEFAULT_STRATEGY}",
    )
    parser.add_argument(
        "--algorithm",
        choices=ALGORITHM_CHOICES,
        help=f"Pathfinding algorithm for a single scenario run. Default: {DEFAULT_ALGORITHM}",
    )
    parser.add_argument(
        "--gif",
        action="store_true",
        default=DEFAULT_RENDER_GIF,
        help="Generate a GIF. By default the program only computes results.",
    )
    parser.add_argument(
        "--debugging",
        action="store_true",
        default=DEFAULT_DEBUGGING,
        help="Export debug frames for a single scenario run.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    explicit_flags = {argument.split("=", 1)[0] for argument in raw_args if argument.startswith("--")}
    parser = build_parser()
    args = parser.parse_args(raw_args)
    validate_args(parser, args, explicit_flags)
    args.explicit_flags = explicit_flags
    return args


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace, explicit_flags: set[str]) -> None:
    if args.cell_size <= 0:
        parser.error("--cell-size must be a positive integer.")
    if args.frame_duration <= 0:
        parser.error("--frame-duration must be a positive integer.")

    if args.suite is None:
        return

    unsupported = sorted(
        flag
        for flag in explicit_flags
        if flag.startswith("--") and flag not in SUITE_ONLY_ALLOWED_FLAGS and flag != "--suite"
    )
    if unsupported:
        parser.error("--suite cannot be combined with: " + ", ".join(unsupported))


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


def resolve_scenario_path(scenario_arg: str) -> Path:
    requested = Path(scenario_arg)
    candidates = []
    root = Path("scenarios")

    if requested.is_file():
        return requested

    if requested.suffix:
        candidates.append(root / requested)
        filename = requested.name
    else:
        candidates.append(requested.with_suffix(".txt"))
        candidates.append(root / requested.with_suffix(".txt"))
        filename = requested.with_suffix(".txt").name

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    matches = sorted(root.rglob(filename))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Scenario not found: {scenario_arg}")
    raise ValueError(f"Scenario name is ambiguous: {scenario_arg} -> {', '.join(str(path) for path in matches)}")


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
    mapped_paths = [
        path
        for path in all_paths
        if re.fullmatch(rf"{re.escape(suite_name)}_map\d+\.txt", path.name, re.IGNORECASE)
    ]
    return suite_name, mapped_paths or all_paths


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


def variant_label(
    scenario_label: str,
    layout_type: str,
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> str:
    return (
        f"{layout_type}_{scenario_label}_{variant_filename_token(mode)}_{variant_filename_token(station_mode)}_"
        f"{variant_filename_token(strategy)}_{variant_filename_token(algorithm)}"
    )


def resolve_layout_override_path(layout_arg: str) -> Path:
    requested = Path(layout_arg)
    candidates = []

    if requested.is_file():
        return requested

    candidates.append(Path("layouts") / requested)
    if requested.suffix.lower() not in {".json", ".txt"}:
        candidates.append(Path("layouts") / f"{layout_arg}.json")
        candidates.append(Path("layouts") / f"{layout_arg}.txt")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"Layout override not found: {layout_arg}")


def resolve_layout_reference(
    layout_arg: str | None,
    scenario_layout_id: int,
    layout_type: str,
) -> tuple[int, Path, str]:
    normalized_layout_type = normalize_layout_type(layout_type)
    if layout_arg is None:
        return (
            scenario_layout_id,
            layout_path(scenario_layout_id, normalized_layout_type),
            normalized_layout_type,
        )

    stripped_layout_arg = layout_arg.strip()
    if stripped_layout_arg.isdigit():
        override_layout_id = int(stripped_layout_arg)
        return (
            override_layout_id,
            layout_path(override_layout_id, normalized_layout_type),
            normalized_layout_type,
        )

    override_path = resolve_layout_override_path(stripped_layout_arg)
    layout_id_match = re.search(r"(\d+)(?=\.(?:json|txt)$)", override_path.name, re.IGNORECASE)
    layout_id = int(layout_id_match.group(1)) if layout_id_match else scenario_layout_id
    return layout_id, override_path, normalized_layout_type


def calculate_makespan(plans) -> int:
    return max((len(plan.path) for plan in plans), default=1) - 1


def render_or_measure(
    warehouse,
    plans,
    output_path: Path | None,
    cell_size: int,
    frame_duration: int,
    progress: bool,
    render_gif: bool,
    debug_frames_dir: Path | None = None,
) -> int:
    if not render_gif and debug_frames_dir is None:
        return calculate_makespan(plans)

    return render_frames(
        warehouse=warehouse,
        plans=plans,
        output_path=output_path if render_gif else None,
        cell_size=cell_size,
        frame_duration_ms=frame_duration,
        progress=progress,
        debug_frames_dir=debug_frames_dir,
    )


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
    makespan = render_or_measure(
        warehouse,
        plans,
        output_path,
        cell_size,
        frame_duration,
        progress,
        render_gif,
        debug_frames_dir,
    )
    return makespan, plans


def default_layout_argument(explicit_flags: set[str]) -> str | None:
    if "--layout" in explicit_flags:
        return None
    if "--scenario" in explicit_flags:
        return None
    return DEFAULT_LAYOUT


def build_debug_frames_dir(scenario_path: Path) -> Path:
    return DEFAULT_DEBUG_FRAMES_ROOT / scenario_path.stem


def build_single_gif_output_path(output_arg: str | None) -> Path:
    requested = Path(output_arg or DEFAULT_OUTPUT)
    if requested.is_absolute() or requested.parent != Path("."):
        return requested
    return Path(DEFAULT_SUITE_OUTPUT_DIR) / requested


def run_suite(args: argparse.Namespace) -> None:
    suite_name, scenario_paths = derive_suite_paths(args.suite)
    missing = [str(path) for path in scenario_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing scenario files: " + ", ".join(missing))

    tasks_rows = [TASKS_HEADERS]
    summary_rows = [SUMMARY_HEADERS]
    comparison_rows = [COMPARISON_HEADERS]
    output_dir = Path(args.suite_output_dir)
    warehouse_cache: dict[tuple[int, str], tuple[int, Path, str, object]] = {}

    for scenario_path in scenario_paths:
        print(f"[2/4] Loading scenario from {scenario_path}")
        definition = load_scenario_definition(scenario_path)
        for variant in expand_scenario_variants(definition):
            cache_key = (definition.layout_id, variant.layout_type)
            if cache_key not in warehouse_cache:
                resolved_layout_id, resolved_layout_path, resolved_layout_type = resolve_layout_reference(
                    None,
                    definition.layout_id,
                    variant.layout_type,
                )
                warehouse = load_layout(resolved_layout_path, resolved_layout_type)
                warehouse_cache[cache_key] = (
                    resolved_layout_id,
                    resolved_layout_path,
                    resolved_layout_type,
                    warehouse,
                )
                print(
                    f"[2/4] Layout loaded from {resolved_layout_path}: "
                    f"{warehouse.width}x{warehouse.height} ({resolved_layout_type}/{resolved_layout_id})"
                )

            resolved_layout_id, _, _, warehouse = warehouse_cache[cache_key]
            current_label = variant_label(
                scenario_path.stem,
                variant.layout_type,
                variant.mode,
                variant.station_mode,
                variant.strategy,
                variant.algorithm,
            )
            output_path = output_dir / f"{current_label}.gif" if args.gif else None
            print(
                f"[2/4] Scenario variant: {definition.agent_count} agents, {len(definition.tasks)} tasks, "
                f"mode={variant.mode}, station={variant.station_mode}, strategy={variant.strategy}, "
                f"algorithm={variant.algorithm}, layout={variant.layout_type}/{resolved_layout_id}"
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
                render_gif=args.gif,
            )
            assignment_type = assignment_type_label(variant.mode, variant.station_mode)
            tasks_rows.extend(
                build_tasks_rows(
                    suite_name,
                    variant.layout_type,
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
                    variant.layout_type,
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
                    variant.layout_type,
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


def run_single_scenario(args: argparse.Namespace) -> None:
    explicit_flags = args.explicit_flags
    scenario_arg = args.scenario or DEFAULT_SCENARIO
    layout_arg = args.layout if "--layout" in explicit_flags else default_layout_argument(explicit_flags)
    layout_type = args.layout_type or DEFAULT_LAYOUT_TYPE
    mode = args.mode or DEFAULT_MODE
    station_mode = args.station or DEFAULT_STATION
    strategy = args.strategy or DEFAULT_STRATEGY
    algorithm = args.algorithm or DEFAULT_ALGORITHM
    render_gif = args.gif if "--gif" in explicit_flags else DEFAULT_RENDER_GIF
    debugging = args.debugging if "--debugging" in explicit_flags else DEFAULT_DEBUGGING

    scenario_path = resolve_scenario_path(scenario_arg)
    print(f"[1/4] Loading scenario from {scenario_path}")
    definition = load_scenario_definition(scenario_path)
    variant = resolve_scenario_variant(
        definition,
        layout_type=layout_type,
        mode=mode,
        station_mode=station_mode,
        strategy=strategy,
        algorithm=algorithm,
    )
    print(
        f"[1/4] Scenario loaded: {definition.agent_count} agents, {len(definition.tasks)} tasks, "
        f"mode={variant.mode}, station={variant.station_mode}, strategy={variant.strategy}, "
        f"algorithm={variant.algorithm}, type={variant.layout_type}"
    )

    resolved_layout_id, resolved_layout_path, resolved_layout_type = resolve_layout_reference(
        layout_arg,
        definition.layout_id,
        variant.layout_type,
    )
    print(f"[2/4] Loading layout from {resolved_layout_path}")
    warehouse = load_layout(resolved_layout_path, resolved_layout_type)
    print(
        f"[2/4] Layout loaded: {warehouse.width}x{warehouse.height} "
        f"(layout={resolved_layout_type}/{resolved_layout_id})"
    )

    output_path = build_single_gif_output_path(args.output) if render_gif else None
    debug_frames_dir = build_debug_frames_dir(scenario_path) if debugging else None

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

    if render_gif and debug_frames_dir is not None:
        print(f"[4/4] Rendering GIF to {output_path} and exporting frames to {debug_frames_dir}")
    elif render_gif:
        print(f"[4/4] Rendering GIF to {output_path}")
    elif debug_frames_dir is not None:
        print(f"[4/4] Exporting frames to {debug_frames_dir}")
    else:
        print("[4/4] Skipping GIF rendering")

    makespan = render_or_measure(
        warehouse,
        plans,
        output_path,
        args.cell_size,
        args.frame_duration,
        progress=True,
        render_gif=render_gif,
        debug_frames_dir=debug_frames_dir,
    )
    print_summary(plans, makespan, output_path, variant.station_mode, render_gif, debug_frames_dir)


def main() -> None:
    args = parse_args()
    if args.suite is not None:
        run_suite(args)
        return
    run_single_scenario(args)


if __name__ == "__main__":
    main()
