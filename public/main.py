import argparse
import re
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from mapd.collisions import total_collision_count
from mapd.feasibility import ImpossibleVariantError, ensure_variant_possible
from mapd.loader import (
    expand_scenario_variants,
    layout_path,
    load_layout,
    load_scenario_definition,
    normalize_layout_type,
    resolve_scenario_variant,
)
from mapd.models import (
    PlanningLimitExceeded,
    PlanningStats,
    ScenarioDefinition,
    ScenarioVariant,
    VariantExecutionResult,
)
from mapd.planner import build_agent_plans, build_relaxed_agent_plans
from mapd.report_metrics import (
    algorithm_label,
    distance_step_sum,
    format_duration,
    layout_size_label,
    missed_deadline_count,
    missed_deadline_time_sum,
    mode_label,
    status_label,
    strategy_label,
    throughput,
    wait_step_count,
)
from mapd.renderer import render_frames
from mapd.results_workbook import (
    COMPARISON_HEADERS,
    build_comparison_row,
    write_xlsx_workbook,
)
from mapd.paths import DEBUGGING_ROOT, GIFS_ROOT, LAYOUTS_ROOT, PROJECT_ROOT, RESULTS_ROOT, SCENARIOS_ROOT
DEFAULT_LAYOUT_TYPE = "square"
DEFAULT_SCENARIO = "0.txt"
DEFAULT_SCENARIO_SUITE = "0"
DEFAULT_OUTPUT = "simulation.gif"
DEFAULT_SUITE_OUTPUT_DIR = GIFS_ROOT
DEFAULT_RESULTS_DIR = RESULTS_ROOT
DEFAULT_CELL_SIZE = 48
DEFAULT_FRAME_DURATION = 250
DEFAULT_MODE = "Set"
DEFAULT_STATION = "Set"
DEFAULT_STRATEGY = "None"
DEFAULT_ALGORITHM = "BFS"
DEFAULT_RENDER_GIF = False
DEFAULT_DEBUGGING = False
DEFAULT_FALLBACK_GIF = False
DEFAULT_DEBUG_FRAMES_ROOT = DEBUGGING_ROOT
AUTOMATIC_RELAXED_ORDER_LIMIT = 10
FALLBACK_GIF_TIME_BUDGET_SECONDS = 20.0
FALLBACK_GIF_SOFT_MAX_EXPANSIONS = 100_000

STATUS_SOLVED = "Solved"
STATUS_NO_SOLUTION = "No solution"
STATUS_IMPOSSIBLE = "Impossible"

LAYOUT_TYPE_CHOICES = ["square", "hexagon", "triangle"]
MODE_CHOICES = ["Set", "Available"]
STRATEGY_CHOICES = ["FCFS", "Robin", "GreedyCost", "None"]
ALGORITHM_CHOICES = ["WHCA*", "SIPP", "BFS"]
SUITE_ONLY_ALLOWED_FLAGS = {
    "--gif",
    "--jobs",
    "--layout",
    "--type",
    "--mode",
    "--station",
    "--strategy",
    "--algorithm",
    "--suite-output-dir",
    "--results-dir",
    "--cell-size",
    "--frame-duration",
}


@dataclass(frozen=True)
class SuiteVariantTask:
    index: int
    total_variants: int
    scenario_label: str
    definition: ScenarioDefinition
    variant: ScenarioVariant
    resolved_layout_id: int
    resolved_layout_path: str
    resolved_layout_type: str
    output_path: str | None
    cell_size: int
    frame_duration: int
    render_gif: bool


@dataclass(frozen=True)
class SuiteVariantOutcome:
    index: int
    header: str
    summary: str
    comparison_row: list[object]


_WORKER_WAREHOUSE_CACHE: dict[tuple[str, str], object] = {}


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
            "Run every variant from a scenario file or from every scenario in a folder, for example '--suite 2'. "
            f"If the value is omitted, '{DEFAULT_SCENARIO_SUITE}' is used."
        ),
    )
    parser.add_argument(
        "--layout",
        help="Layout id, file name or path for a single scenario run. By default the layout from the scenario is used.",
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
        help=(
            "Preferred size of a single cell in pixels. Large layouts are automatically scaled down "
            f"to fit the frame. Default: {DEFAULT_CELL_SIZE}"
        ),
    )
    parser.add_argument(
        "--frame-duration",
        type=int,
        default=DEFAULT_FRAME_DURATION,
        help=f"GIF frame duration in milliseconds. Default: {DEFAULT_FRAME_DURATION}",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        help="Suite only. Number of worker processes, capped at 10%% of all variants. Default: auto when --gif is off, otherwise 1.",
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
    parser.add_argument(
        "--fallback-gif",
        action="store_true",
        default=DEFAULT_FALLBACK_GIF,
        help=(
            "Single scenario only. When no collision-free solution is found and --gif is enabled, "
            "render a best-effort GIF that still prefers collision-free detours and uses collisions only as a last resort."
        ),
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
    if args.jobs is not None and args.jobs <= 0:
        parser.error("--jobs must be a positive integer.")
    if args.fallback_gif and not args.gif:
        parser.error("--fallback-gif requires --gif.")

    if args.suite is None:
        return

    if "--layout" in explicit_flags and infer_layout_id(args.layout) is None:
        parser.error("--layout in suite mode must be a layout id or a file name that contains one.")

    unsupported = sorted(
        flag
        for flag in explicit_flags
        if flag.startswith("--") and flag not in SUITE_ONLY_ALLOWED_FLAGS and flag != "--suite"
    )
    if unsupported:
        parser.error("--suite cannot be combined with: " + ", ".join(unsupported))


def run_relaxed_simulation(
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
    *,
    max_order_attempts: int = AUTOMATIC_RELAXED_ORDER_LIMIT,
    time_budget_seconds: float = FALLBACK_GIF_TIME_BUDGET_SECONDS,
    soft_max_expansions: int = FALLBACK_GIF_SOFT_MAX_EXPANSIONS,
    stats: PlanningStats | None = None,
) -> tuple[int, list]:
    plans = build_relaxed_agent_plans(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
        max_order_attempts=max_order_attempts,
        time_budget_seconds=time_budget_seconds,
        soft_max_expansions=soft_max_expansions,
        stats=stats,
    )
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


def resolve_scenario_path(scenario_arg: str) -> Path:
    requested = Path(scenario_arg)
    candidates = []
    root = SCENARIOS_ROOT

    if requested.is_file():
        return requested
    project_relative = PROJECT_ROOT / requested
    if project_relative.is_file():
        return project_relative

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


def resolve_scenario_directory_path(directory_arg: str) -> Path:
    requested = Path(directory_arg)
    candidates = [
        requested,
        PROJECT_ROOT / requested,
        SCENARIOS_ROOT / requested,
    ]

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    matches = sorted(path for path in SCENARIOS_ROOT.rglob(requested.name) if path.is_dir())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"Scenario directory not found: {directory_arg}")
    raise ValueError(
        f"Scenario directory name is ambiguous: {directory_arg} -> {', '.join(str(path) for path in matches)}"
    )


def derive_suite_paths(suite_arg: str) -> tuple[str, list[Path]]:
    try:
        scenario_directory = resolve_scenario_directory_path(suite_arg)
    except FileNotFoundError:
        scenario_path = resolve_scenario_path(suite_arg)
        return scenario_path.stem, [scenario_path]

    scenario_paths = sorted(scenario_directory.rglob("*.txt"))
    if not scenario_paths:
        raise FileNotFoundError(f"No scenario files found in directory: {suite_arg}")
    return scenario_directory.name, scenario_paths


def safe_results_name(value: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", value).strip()
    sanitized = sanitized.rstrip(". ")
    return sanitized or "results"


def variant_filename_token(value: str) -> str:
    token_map = {
        "Set": "set",
        "Available": "available",
        "FCFS": "fcfs",
        "Robin": "robin",
        "GreedyCost": "greedycost",
        "None": "none",
        "A*": "astar",
        "WHCA*": "whca",
        "SIPP": "sipp",
        "BFS": "bfs",
    }
    return token_map[value]


def variant_label(
    scenario_label: str,
    layout_size: str | None,
    layout_id: int,
    layout_type: str,
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> str:
    size_prefix = "" if layout_size is None else f"{layout_size}_"
    return (
        f"{size_prefix}{layout_type}_layout{layout_id}_{scenario_label}_"
        f"{variant_filename_token(mode)}_{variant_filename_token(station_mode)}_"
        f"{variant_filename_token(strategy)}_{variant_filename_token(algorithm)}"
    )


def resolve_layout_override_path(layout_arg: str) -> Path:
    requested = Path(layout_arg)
    candidates = []

    if requested.is_file():
        return requested
    project_relative = PROJECT_ROOT / requested
    if project_relative.is_file():
        return project_relative

    candidates.append(LAYOUTS_ROOT / requested)
    if requested.suffix.lower() not in {".json", ".txt"}:
        candidates.append(LAYOUTS_ROOT / f"{layout_arg}.json")
        candidates.append(LAYOUTS_ROOT / f"{layout_arg}.txt")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"Layout override not found: {layout_arg}")


def infer_layout_id(layout_arg: str | None) -> int | None:
    if layout_arg is None:
        return None

    stripped_layout_arg = layout_arg.strip()
    if stripped_layout_arg.isdigit():
        return int(stripped_layout_arg)

    layout_id_match = re.search(r"(\d+)(?=\.(?:json|txt)$)", Path(stripped_layout_arg).name, re.IGNORECASE)
    if layout_id_match is not None:
        return int(layout_id_match.group(1))

    return None


def resolve_layout_reference(
    layout_arg: str | None,
    scenario_layout_id: int,
    layout_type: str,
    layout_size: str | None = None,
) -> tuple[int, Path, str]:
    normalized_layout_type = normalize_layout_type(layout_type)
    if layout_arg is None:
        return (
            scenario_layout_id,
            layout_path(scenario_layout_id, normalized_layout_type, layout_size=layout_size),
            normalized_layout_type,
        )

    stripped_layout_arg = layout_arg.strip()
    if stripped_layout_arg.isdigit():
        override_layout_id = int(stripped_layout_arg)
        return (
            override_layout_id,
            layout_path(override_layout_id, normalized_layout_type, layout_size=layout_size),
            normalized_layout_type,
        )

    override_path = resolve_layout_override_path(stripped_layout_arg)
    layout_id = infer_layout_id(override_path.name) or scenario_layout_id
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
    stats: PlanningStats | None = None,
) -> tuple[int, list]:
    plans = build_agent_plans(
        warehouse,
        agent_count,
        tasks,
        mode,
        station_mode,
        strategy,
        algorithm,
        stats=stats,
    )
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


def execute_variant(
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
    *,
    max_replans: int | None = None,
) -> VariantExecutionResult:
    stats = PlanningStats(max_replans=max_replans)
    started_at = time.perf_counter()

    try:
        ensure_variant_possible(warehouse, agent_count, tasks, mode)
    except ImpossibleVariantError as exc:
        return VariantExecutionResult(
            status=STATUS_IMPOSSIBLE,
            details=str(exc),
            makespan=None,
            plans=None,
            collisions=None,
            replans=stats.replans,
            simulation_time_seconds=time.perf_counter() - started_at,
        )

    try:
        makespan, plans = run_simulation(
            warehouse,
            agent_count,
            tasks,
            mode,
            station_mode,
            strategy,
            algorithm,
            output_path,
            cell_size,
            frame_duration,
            progress,
            render_gif,
            debug_frames_dir,
            stats=stats,
        )
    except PlanningLimitExceeded as exc:
        return VariantExecutionResult(
            status=STATUS_NO_SOLUTION,
            details=str(exc),
            makespan=None,
            plans=None,
            collisions=None,
            replans=stats.replans,
            simulation_time_seconds=time.perf_counter() - started_at,
        )
    except RuntimeError as exc:
        try:
            stats.note_replan()
            relaxed_makespan, relaxed_plans = run_relaxed_simulation(
                warehouse,
                agent_count,
                tasks,
                mode,
                station_mode,
                strategy,
                algorithm,
                output_path,
                cell_size,
                frame_duration,
                progress,
                render_gif,
                debug_frames_dir,
                stats=stats,
            )
        except PlanningLimitExceeded as relaxed_limit_exc:
            return VariantExecutionResult(
                status=STATUS_NO_SOLUTION,
                details=f"{exc} {relaxed_limit_exc}",
                makespan=None,
                plans=None,
                collisions=None,
                replans=stats.replans,
                simulation_time_seconds=time.perf_counter() - started_at,
            )
        except RuntimeError as relaxed_exc:
            return VariantExecutionResult(
                status=STATUS_NO_SOLUTION,
                details=f"{exc} Best-effort simulation failed: {relaxed_exc}",
                makespan=None,
                plans=None,
                collisions=None,
                replans=stats.replans,
                simulation_time_seconds=time.perf_counter() - started_at,
            )

        return VariantExecutionResult(
            status=STATUS_NO_SOLUTION,
            details=str(exc),
            makespan=relaxed_makespan,
            plans=relaxed_plans,
            collisions=total_collision_count(relaxed_plans),
            replans=stats.replans,
            simulation_time_seconds=time.perf_counter() - started_at,
        )

    return VariantExecutionResult(
        status=STATUS_SOLVED,
        details=None,
        makespan=makespan,
        plans=plans,
        collisions=total_collision_count(plans),
        replans=stats.replans,
        simulation_time_seconds=time.perf_counter() - started_at,
    )


def variant_description(
    scenario_name: str,
    layout_id: int,
    layout_size: str | None,
    layout_type: str,
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
) -> str:
    strategy_value = strategy_label(strategy) or "-"
    return (
        f"scenario={scenario_name} "
        f"layout={layout_id} "
        f"size={layout_size_label(layout_size)} "
        f"type={layout_type} "
        f"mode={mode_label(mode)} "
        f"station={mode_label(station_mode)} "
        f"strategy={strategy_value} "
        f"algorithm={algorithm_label(algorithm)}"
    )


def variant_result_summary(
    result: VariantExecutionResult,
    task_count: int,
    station_cells: set[tuple[int, int]],
    output_path: Path | None,
    debug_frames_dir: Path | None,
) -> str:
    parts = [
        f"status={status_label(result.status)}",
        f"simulation time={format_duration(result.simulation_time_seconds)}",
        f"replans={result.replans}",
    ]

    throughput_value = throughput(task_count, result.makespan)
    if throughput_value is not None:
        parts.append(f"throughput={throughput_value}")
    if result.makespan is not None:
        parts.append(f"makespan={result.makespan}")
    if result.collisions is not None:
        parts.append(f"collisions={result.collisions}")

    missed = missed_deadline_count(result.plans)
    missed_time = missed_deadline_time_sum(result.plans)
    waits = wait_step_count(result.plans, station_cells)
    distance = distance_step_sum(result.plans)
    if missed is not None:
        parts.append(f"missed deadlines={missed}")
    if missed_time is not None:
        parts.append(f"missed deadline time={missed_time}")
    if waits is not None:
        parts.append(f"waits={waits}")
    if distance is not None:
        parts.append(f"sum of distances={distance}")
    if result.plans is not None and output_path is not None:
        parts.append(f"gif={output_path}")
    if result.plans is not None and debug_frames_dir is not None:
        parts.append(f"frames={debug_frames_dir}")
    if result.details is not None:
        parts.append(f"details={result.details}")

    return ", ".join(parts)


def default_layout_argument() -> str | None:
    return None


def default_scenario_argument() -> str:
    scenario_files = sorted(SCENARIOS_ROOT.rglob("*.txt"))
    if len(scenario_files) == 1:
        return str(scenario_files[0].relative_to(SCENARIOS_ROOT))
    return DEFAULT_SCENARIO


def scenario_name(definition: ScenarioDefinition, scenario_path: Path) -> str:
    return definition.metadata.scenario_id or scenario_path.stem


def validate_scenario_metadata(
    definition: ScenarioDefinition,
    warehouse,
) -> None:
    max_open_tasks = definition.metadata.max_open_tasks_on_shelves
    if max_open_tasks is not None and max_open_tasks > warehouse.shelf_count:
        raise ValueError(
            f"Scenario allows {max_open_tasks} open shelf tasks, but layout contains only {warehouse.shelf_count} shelves."
        )

    invalid_task = next(
        (task for task in definition.tasks if task.shelf_index < 0 or task.shelf_index >= warehouse.shelf_count),
        None,
    )
    if invalid_task is not None:
        raise ValueError(
            f"Scenario task {invalid_task.task_id} references shelf index {invalid_task.shelf_index}, "
            f"but the selected layout exposes only {warehouse.shelf_count} shelves. "
            "This scenario was likely generated for an older warehouse profile and should be regenerated."
        )


def build_debug_frames_dir(scenario_path: Path) -> Path:
    return DEFAULT_DEBUG_FRAMES_ROOT / scenario_path.stem


def build_single_gif_output_path(output_arg: str | None) -> Path:
    if output_arg is None:
        return DEFAULT_SUITE_OUTPUT_DIR / DEFAULT_OUTPUT
    requested = Path(output_arg)
    if requested.is_absolute() or requested.parent != Path("."):
        return requested
    return Path(DEFAULT_SUITE_OUTPUT_DIR) / requested


def variant_matches_suite_filters(
    variant: ScenarioVariant,
    args: argparse.Namespace,
    explicit_flags: set[str],
) -> bool:
    if "--layout" in explicit_flags:
        requested_layout_id = infer_layout_id(args.layout)
        if requested_layout_id is not None and variant.layout_id != requested_layout_id:
            return False
    if "--type" in explicit_flags and variant.layout_type != args.layout_type:
        return False
    if "--mode" in explicit_flags and variant.mode != args.mode:
        return False
    if "--station" in explicit_flags and variant.station_mode != args.station:
        return False
    if "--algorithm" in explicit_flags and variant.algorithm != args.algorithm:
        return False
    if "--strategy" in explicit_flags and variant.strategy != args.strategy:
        return False
    return True


def filter_suite_variants(
    variants: list[ScenarioVariant],
    args: argparse.Namespace,
    explicit_flags: set[str],
) -> list[ScenarioVariant]:
    if not any(
        flag in explicit_flags
        for flag in ("--layout", "--type", "--mode", "--station", "--strategy", "--algorithm")
    ):
        return variants
    return [variant for variant in variants if variant_matches_suite_filters(variant, args, explicit_flags)]


def resolve_suite_jobs(jobs: int | None, render_gif: bool, total_variants: int) -> int:
    max_worker_limit = max(1, total_variants // 10)
    if jobs is not None:
        return min(max_worker_limit, jobs)
    if render_gif:
        return 1

    cpu_count = os.cpu_count() or 1
    return min(max_worker_limit, max(1, cpu_count - 1))


def load_worker_warehouse(layout_path: str, layout_type: str):
    cache_key = (layout_path, layout_type)
    cached = _WORKER_WAREHOUSE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    warehouse = load_layout(Path(layout_path), layout_type)
    _WORKER_WAREHOUSE_CACHE[cache_key] = warehouse
    return warehouse


def build_suite_variant_task(
    index: int,
    total_variants: int,
    scenario_path: Path,
    definition: ScenarioDefinition,
    variant: ScenarioVariant,
    output_dir: Path,
    *,
    render_gif: bool,
    cell_size: int,
    frame_duration: int,
) -> SuiteVariantTask:
    scenario_label = scenario_name(definition, scenario_path)
    resolved_layout_id, resolved_layout_path, resolved_layout_type = resolve_layout_reference(
        None,
        variant.layout_id,
        variant.layout_type,
        definition.layout_size,
    )
    current_label = variant_label(
        scenario_label,
        definition.layout_size,
        resolved_layout_id,
        variant.layout_type,
        variant.mode,
        variant.station_mode,
        variant.strategy,
        variant.algorithm,
    )
    output_path = str(output_dir / f"{current_label}.gif") if render_gif else None
    return SuiteVariantTask(
        index=index,
        total_variants=total_variants,
        scenario_label=scenario_label,
        definition=definition,
        variant=variant,
        resolved_layout_id=resolved_layout_id,
        resolved_layout_path=str(resolved_layout_path),
        resolved_layout_type=resolved_layout_type,
        output_path=output_path,
        cell_size=cell_size,
        frame_duration=frame_duration,
        render_gif=render_gif,
    )


def run_suite_variant_task(task: SuiteVariantTask) -> SuiteVariantOutcome:
    warehouse = load_worker_warehouse(task.resolved_layout_path, task.resolved_layout_type)
    validate_scenario_metadata(task.definition, warehouse)
    output_path = Path(task.output_path) if task.output_path is not None else None
    header = (
        f"[{task.index}/{task.total_variants}] "
        + variant_description(
            task.scenario_label,
            task.resolved_layout_id,
            task.definition.layout_size,
            task.variant.layout_type,
            task.variant.mode,
            task.variant.station_mode,
            task.variant.strategy,
            task.variant.algorithm,
        )
    )

    result = execute_variant(
        warehouse,
        task.definition.agent_count,
        task.definition.tasks,
        task.variant.mode,
        task.variant.station_mode,
        task.variant.strategy,
        task.variant.algorithm,
        output_path,
        task.cell_size,
        task.frame_duration,
        progress=task.render_gif,
        render_gif=task.render_gif,
        max_replans=task.definition.metadata.max_replans,
    )
    station_cells = set(warehouse.stations)
    return SuiteVariantOutcome(
        index=task.index,
        header=header,
        summary=variant_result_summary(
            result,
            len(task.definition.tasks),
            station_cells,
            output_path,
            None,
        ),
        comparison_row=build_comparison_row(
            task.scenario_label,
            task.resolved_layout_id,
            task.definition.layout_size,
            task.variant.layout_type,
            task.definition.agent_count,
            len(task.definition.tasks),
            task.definition.metadata,
            task.variant.mode,
            task.variant.station_mode,
            task.variant.strategy,
            task.variant.algorithm,
            result,
            station_cells,
        ),
    )


def execute_suite_tasks(tasks: list[SuiteVariantTask], worker_count: int) -> list[SuiteVariantOutcome]:
    outcomes: list[SuiteVariantOutcome | None] = [None] * len(tasks)
    if worker_count <= 1:
        for task in tasks:
            outcome = run_suite_variant_task(task)
            outcomes[outcome.index - 1] = outcome
            print(outcome.header)
            print("  " + outcome.summary)
        return [outcome for outcome in outcomes if outcome is not None]

    print(f"[suite] Running {len(tasks)} variants with {worker_count} workers.")
    executor: ProcessPoolExecutor | None = None
    try:
        executor = ProcessPoolExecutor(max_workers=worker_count)
        task_iter = iter(tasks)
        max_in_flight = min(len(tasks), max(worker_count, worker_count * 2))
        future_to_task = {}

        for _ in range(max_in_flight):
            try:
                task = next(task_iter)
            except StopIteration:
                break
            future_to_task[executor.submit(run_suite_variant_task, task)] = task

        while future_to_task:
            completed, _ = wait(tuple(future_to_task), return_when=FIRST_COMPLETED)
            for future in completed:
                task = future_to_task.pop(future)
                try:
                    outcome = future.result()
                except Exception as exc:
                    for pending in tuple(future_to_task):
                        pending.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    executor = None
                    raise RuntimeError(
                        "Suite worker failed for "
                        + variant_description(
                            task.scenario_label,
                            task.resolved_layout_id,
                            task.definition.layout_size,
                            task.variant.layout_type,
                            task.variant.mode,
                            task.variant.station_mode,
                            task.variant.strategy,
                            task.variant.algorithm,
                        )
                    ) from exc

                outcomes[outcome.index - 1] = outcome
                print(outcome.header)
                print("  " + outcome.summary)

                try:
                    next_task = next(task_iter)
                except StopIteration:
                    continue
                future_to_task[executor.submit(run_suite_variant_task, next_task)] = next_task
    except PermissionError:
        print("[suite] Parallel workers are unavailable in this environment, falling back to serial execution.")
        return execute_suite_tasks(tasks, 1)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    return [outcome for outcome in outcomes if outcome is not None]


def run_suite(args: argparse.Namespace) -> None:
    suite_name, scenario_paths = derive_suite_paths(args.suite)
    missing = [str(path) for path in scenario_paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing scenario files: " + ", ".join(missing))

    comparison_rows = [COMPARISON_HEADERS]
    output_dir = Path(args.suite_output_dir)
    suite_entries = []
    total_variants = 0

    for scenario_path in scenario_paths:
        definition = load_scenario_definition(scenario_path)
        variants = filter_suite_variants(
            expand_scenario_variants(definition),
            args,
            args.explicit_flags,
        )
        suite_entries.append((scenario_path, definition, variants))
        total_variants += len(variants)

    if total_variants == 0:
        raise ValueError("No suite variants matched the selected filters.")

    variant_tasks: list[SuiteVariantTask] = []
    variant_index = 0
    for scenario_path, definition, variants in suite_entries:
        for variant in variants:
            variant_index += 1
            variant_tasks.append(
                build_suite_variant_task(
                    variant_index,
                    total_variants,
                    scenario_path,
                    definition,
                    variant,
                    output_dir,
                    render_gif=args.gif,
                    cell_size=args.cell_size,
                    frame_duration=args.frame_duration,
                )
            )

    worker_count = resolve_suite_jobs(args.jobs, args.gif, total_variants)
    outcomes = execute_suite_tasks(variant_tasks, worker_count)
    comparison_rows.extend(outcome.comparison_row for outcome in outcomes)

    if len(suite_entries) == 1:
        results_name = scenario_name(suite_entries[0][1], suite_entries[0][0])
    else:
        results_name = suite_name
    results_path = Path(args.results_dir) / f"{safe_results_name(results_name)}.xlsx"
    write_xlsx_workbook(results_path, [("Overall Comparison", comparison_rows)])
    print(f"[done] Saved results: {results_path}")


def run_single_scenario(args: argparse.Namespace) -> None:
    explicit_flags = args.explicit_flags
    layout_arg = args.layout if "--layout" in explicit_flags else default_layout_argument()
    scenario_arg = args.scenario or default_scenario_argument()
    layout_type = args.layout_type or DEFAULT_LAYOUT_TYPE
    mode = args.mode or DEFAULT_MODE
    station_mode = args.station or DEFAULT_STATION
    strategy = args.strategy or DEFAULT_STRATEGY
    algorithm = args.algorithm or DEFAULT_ALGORITHM
    render_gif = args.gif if "--gif" in explicit_flags else DEFAULT_RENDER_GIF
    debugging = args.debugging if "--debugging" in explicit_flags else DEFAULT_DEBUGGING

    scenario_path = resolve_scenario_path(scenario_arg)
    definition = load_scenario_definition(scenario_path)
    scenario_label = scenario_name(definition, scenario_path)
    variant = resolve_scenario_variant(
        definition,
        layout_id=infer_layout_id(layout_arg),
        layout_type=layout_type,
        mode=mode,
        station_mode=station_mode,
        strategy=strategy,
        algorithm=algorithm,
    )

    resolved_layout_id, resolved_layout_path, resolved_layout_type = resolve_layout_reference(
        layout_arg,
        variant.layout_id,
        variant.layout_type,
        definition.layout_size,
    )
    warehouse = load_layout(resolved_layout_path, resolved_layout_type)
    validate_scenario_metadata(definition, warehouse)

    output_path = build_single_gif_output_path(args.output) if render_gif else None
    debug_frames_dir = build_debug_frames_dir(scenario_path) if debugging else None
    prefix = "[1/1]"

    print(
        f"{prefix} "
        + variant_description(
            scenario_label,
            resolved_layout_id,
            definition.layout_size,
            variant.layout_type,
            variant.mode,
            variant.station_mode,
            variant.strategy,
            variant.algorithm,
        )
    )

    result = execute_variant(
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
        progress=render_gif,
        render_gif=render_gif,
        debug_frames_dir=debug_frames_dir,
        max_replans=definition.metadata.max_replans,
    )
    print(
        "  "
        + variant_result_summary(
            result,
            len(definition.tasks),
            set(warehouse.stations),
            output_path,
            debug_frames_dir,
        )
    )

    if result.plans is None:
        raise SystemExit(1)


def main() -> None:
    args = parse_args()
    if args.suite is not None:
        run_suite(args)
        return
    run_single_scenario(args)


if __name__ == "__main__":
    main()
