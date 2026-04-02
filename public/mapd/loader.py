import json
import re
from pathlib import Path

from mapd.algorithms import normalize_algorithm_name
from mapd.models import ScenarioDefinition, ScenarioMetadata, ScenarioVariant, Task
from mapd.paths import LAYOUTS_ROOT
from mapd.warehouse import WarehouseMap

SUPPORTED_LAYOUT_SIZES = {"small", "medium", "large"}
SUPPORTED_INFLUX_TYPES = {"random": "Random", "poisson": "Poisson", "burst": "Burst"}
SUPPORTED_SPATIAL_DISTRIBUTIONS = {
    "uniform": "Uniform",
    "hotspot": "Hotspot",
    "wave": "Wave",
}


def normalize_layout_type(value: str | None) -> str:
    normalized = "square" if value is None else value.strip().lower()
    if normalized not in WarehouseMap.SUPPORTED_LAYOUT_TYPES:
        raise ValueError(
            f"Unsupported layout type: {value}. Expected one of {sorted(WarehouseMap.SUPPORTED_LAYOUT_TYPES)}."
        )
    return normalized


def normalize_layout_size(value: str | None) -> str:
    if value is None:
        raise ValueError("Layout size cannot be empty.")

    normalized = value.strip().lower()
    if normalized not in SUPPORTED_LAYOUT_SIZES:
        raise ValueError(
            f"Unsupported layout size: {value}. Expected one of {sorted(SUPPORTED_LAYOUT_SIZES)}."
        )
    return normalized


def detect_layout_type(path: Path, default: str | None = "square") -> str:
    for part in reversed(path.parts):
        lowered = part.lower()
        if lowered in WarehouseMap.SUPPORTED_LAYOUT_TYPES:
            return lowered
    return normalize_layout_type(default)


def load_layout(path: Path, layout_type: str = "square") -> WarehouseMap:
    resolved_layout_type = detect_layout_type(path, default=layout_type)
    if path.suffix.lower() == ".json":
        return _load_layout_from_json(path, resolved_layout_type)

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(stripped)
    return WarehouseMap(rows, layout_type=resolved_layout_type)


def _find_layout_in_directory(root: Path, layout_id: int) -> Path | None:
    if not root.is_dir():
        return None

    for suffix in (".json", ".txt"):
        candidate = root / f"{layout_id}{suffix}"
        if candidate.exists():
            return candidate

    nested_layout_dir = root / str(layout_id)
    for suffix in (".json", ".txt"):
        candidate = nested_layout_dir / f"{layout_id}{suffix}"
        if candidate.exists():
            return candidate

    if nested_layout_dir.is_dir():
        layout_files = sorted(nested_layout_dir.glob("*.json"))
        if len(layout_files) == 1:
            return layout_files[0]

        text_files = sorted(nested_layout_dir.glob("*.txt"))
        if len(text_files) == 1:
            return text_files[0]

    return None


def layout_path(
    layout_id: int,
    layout_type: str | None = None,
    layout_size: str | None = None,
    layouts_root: Path | None = None,
) -> Path:
    normalized_layout_type = normalize_layout_type(layout_type) if layout_type is not None else None
    normalized_layout_size = normalize_layout_size(layout_size) if layout_size is not None else None
    root = LAYOUTS_ROOT if layouts_root is None else layouts_root

    if normalized_layout_size is not None:
        sized_match = _find_layout_in_directory(root / normalized_layout_size, layout_id)
        if sized_match is not None:
            return sized_match

    for suffix in (".json", ".txt"):
        candidate = root / f"{layout_id}{suffix}"
        if candidate.exists():
            return candidate

    legacy_layout_match = _find_layout_in_directory(root, layout_id)
    if legacy_layout_match is not None:
        return legacy_layout_match

    if normalized_layout_type is not None:
        typed_layout_dir = root / normalized_layout_type / str(layout_id)
        for suffix in (".json", ".txt"):
            candidate = typed_layout_dir / f"{layout_id}{suffix}"
            if candidate.exists():
                return candidate

        if typed_layout_dir.is_dir():
            layout_files = sorted(typed_layout_dir.glob("*.json"))
            if len(layout_files) == 1:
                return layout_files[0]

            text_files = sorted(typed_layout_dir.glob("*.txt"))
            if len(text_files) == 1:
                return text_files[0]

    if normalized_layout_size is None:
        size_matches = []
        for supported_size in sorted(SUPPORTED_LAYOUT_SIZES):
            sized_match = _find_layout_in_directory(root / supported_size, layout_id)
            if sized_match is not None:
                size_matches.append(sized_match)

        if len(size_matches) == 1:
            return size_matches[0]
        if len(size_matches) > 1:
            raise FileNotFoundError(
                f"Layout {layout_id} exists in multiple size directories under {root}. "
                "Specify the scenario Size field or pass an explicit layout path."
            )

    raise FileNotFoundError(f"Layout {layout_id} not found under {root}.")


def _load_layout_from_json(path: Path, layout_type: str) -> WarehouseMap:
    raw_layout = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_layout, dict):
        raise ValueError(f"Layout JSON must be an object: {path}")

    width = raw_layout.get("width")
    height = raw_layout.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ValueError(f"Layout JSON must define integer width and height: {path}")
    if width <= 0 or height <= 0:
        raise ValueError(f"Layout width and height must be positive: {path}")

    stations = raw_layout.get("stations")
    shelves = raw_layout.get("shelves")
    if not isinstance(stations, list) or not isinstance(shelves, list):
        raise ValueError(f"Layout JSON must define 'stations' and 'shelves' arrays: {path}")

    grid = [["E" for _ in range(width)] for _ in range(height)]
    shelf_slots = _fill_areas(grid, shelves, width, height, "#", "shelves", path)
    _fill_areas(grid, stations, width, height, "S", "stations", path)
    rows = ["".join(row) for row in grid]
    return WarehouseMap(rows, layout_type=normalize_layout_type(layout_type), shelf_slots=shelf_slots)


def _fill_areas(
    grid: list[list[str]],
    areas: list[object],
    width: int,
    height: int,
    symbol: str,
    label: str,
    path: Path,
) -> list[tuple[int, int]]:
    coords: list[tuple[int, int]] = []
    for index, area in enumerate(areas):
        from_x, from_y, to_x, to_y = _parse_area(area, width, height, label, index, path)
        for y in range(from_y, to_y + 1):
            for x in range(from_x, to_x + 1):
                current = grid[y][x]
                if current != "E" and current != symbol:
                    raise ValueError(
                        f"Layout area overlap in {path}: {label}[{index}] collides at x={x}, y={y}."
                    )
                grid[y][x] = symbol
                coords.append((y, x))
    return coords


def _parse_area(
    area: object,
    width: int,
    height: int,
    label: str,
    index: int,
    path: Path,
) -> tuple[int, int, int, int]:
    if not isinstance(area, dict):
        raise ValueError(f"Layout {label}[{index}] must be an object in {path}.")

    from_coord = area.get("from")
    to_coord = area.get("to")
    if not _is_valid_coord_pair(from_coord) or not _is_valid_coord_pair(to_coord):
        raise ValueError(f"Layout {label}[{index}] must define integer 'from' and 'to' pairs in {path}.")

    from_x, from_y = from_coord
    to_x, to_y = to_coord
    inside_bounds = (
        from_x >= 0
        and from_y >= 0
        and to_x < width
        and to_y < height
        and from_x <= to_x
        and from_y <= to_y
    )
    if not inside_bounds:
        raise ValueError(f"Layout {label}[{index}] is outside bounds in {path}.")

    return from_x, from_y, to_x, to_y


def _is_valid_coord_pair(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    )


def _parse_choice_items(raw_value: str) -> list[str]:
    value = raw_value.strip()
    if value.startswith("[") and value.endswith("]"):
        items = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        if not items:
            raise ValueError(f"Scenario option list cannot be empty: {raw_value}")
        return items
    return [value]


def _normalize_mode(value: str) -> str:
    key = value.strip().lower()
    if key == "set":
        return "Set"
    if key == "available":
        return "Available"
    raise ValueError(f"Unsupported scenario mode: {value}")


def _normalize_station_mode(value: str) -> str:
    key = value.strip().lower()
    if key == "set":
        return "Set"
    if key == "available":
        return "Available"
    raise ValueError(f"Unsupported station mode: {value}")


def _normalize_strategy(value: str) -> str:
    strategy_map = {
        "fcfs": "FCFS",
        "greedy": "GreedyCost",
        "greedycost": "GreedyCost",
        "robin": "Robin",
        "none": "None",
    }
    key = value.strip().lower()
    if key not in strategy_map:
        raise ValueError(f"Unsupported strategy: {value}")
    return strategy_map[key]


def _parse_choices(raw_value: str, normalizer) -> list[str]:
    values = []
    for item in _parse_choice_items(raw_value):
        normalized = normalizer(item)
        if normalized not in values:
            values.append(normalized)
    return values


def _parse_layout_ids(raw_value: str) -> list[int]:
    layout_ids: list[int] = []
    for item in _parse_choice_items(raw_value):
        if not item.isdigit():
            raise ValueError(f"Unsupported layout id: {item}")
        layout_id = int(item)
        if layout_id not in layout_ids:
            layout_ids.append(layout_id)
    if not layout_ids:
        raise ValueError("Scenario must define at least one layout id.")
    return layout_ids


def _parse_layout_size(raw_value: str) -> str:
    size_items = _parse_choice_items(raw_value)
    if len(size_items) != 1:
        raise ValueError("Scenario must define exactly one layout size.")
    return normalize_layout_size(size_items[0])


def _find_header_value(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*([^\r\n]+)", text)
    if match is None:
        return None
    return match.group(1).strip()


def _parse_optional_text_header(text: str, label: str) -> str | None:
    value = _find_header_value(text, label)
    if value is None:
        return None
    return value.strip()


def _parse_optional_int_header(
    text: str,
    label: str,
    *,
    min_value: int | None = None,
) -> int | None:
    raw_value = _find_header_value(text, label)
    if raw_value is None:
        return None

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Scenario header '{label}' must be an integer.") from exc

    if min_value is not None and value < min_value:
        raise ValueError(f"Scenario header '{label}' must be >= {min_value}.")
    return value


def _parse_optional_float_header(
    text: str,
    label: str,
    *,
    min_value: float | None = None,
) -> float | None:
    raw_value = _find_header_value(text, label)
    if raw_value is None:
        return None

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"Scenario header '{label}' must be a number.") from exc

    if min_value is not None and value < min_value:
        raise ValueError(f"Scenario header '{label}' must be >= {min_value}.")
    return value


def _parse_optional_float_headers(
    text: str,
    labels: tuple[str, ...],
    *,
    min_value: float | None = None,
) -> float | None:
    for label in labels:
        value = _parse_optional_float_header(text, label, min_value=min_value)
        if value is not None:
            return value
    return None


def _normalize_influx(value: str) -> str:
    key = value.strip().lower()
    if key not in SUPPORTED_INFLUX_TYPES:
        raise ValueError(f"Unsupported influx type: {value}")
    return SUPPORTED_INFLUX_TYPES[key]


def _normalize_spatial_distribution(value: str) -> str:
    key = value.strip().lower()
    if key not in SUPPORTED_SPATIAL_DISTRIBUTIONS:
        raise ValueError(f"Unsupported spatial distribution: {value}")
    return SUPPORTED_SPATIAL_DISTRIBUTIONS[key]


def _parse_scenario_metadata(text: str) -> ScenarioMetadata:
    metadata = ScenarioMetadata(
        scenario_id=_parse_optional_text_header(text, "ID"),
        seed=_parse_optional_int_header(text, "Seed", min_value=0),
        load_factor=_parse_optional_float_header(text, "LoadFactor", min_value=0.0),
        capacity_model=_parse_optional_text_header(text, "CapacityModel"),
        capacity_reserve=_parse_optional_float_header(text, "CapacityReserve", min_value=0.0),
        capacity_steps_per_task=_parse_optional_float_header(text, "CapacityStepsPerTask", min_value=0.0),
        max_open_tasks_on_shelves=_parse_optional_int_header(text, "MaxOpenTasksOnShelves", min_value=1),
        set_assignment_policy=_parse_optional_text_header(text, "SetAssignmentPolicy"),
        influx=(
            _normalize_influx(_find_header_value(text, "Influx"))
            if _find_header_value(text, "Influx") is not None
            else None
        ),
        lambda_value=_parse_optional_float_headers(text, ("Lambda", "LambdaPerHour"), min_value=0.0),
        burst_amount=_parse_optional_int_header(text, "BurstAmount", min_value=0),
        burst_start_step=_parse_optional_int_header(text, "BurstStartStep", min_value=0),
        burst_duration_steps=_parse_optional_int_header(text, "BurstDurationSteps", min_value=0),
        burst_amplitude=_parse_optional_float_header(text, "BurstAmplitude", min_value=0.0),
        spatial_distribution=(
            _normalize_spatial_distribution(_find_header_value(text, "SpatialDistribution"))
            if _find_header_value(text, "SpatialDistribution") is not None
            else None
        ),
        hotspot_shelf_share=_parse_optional_float_header(text, "HotspotShelfShare", min_value=0.0),
        hotspot_task_share=_parse_optional_float_header(text, "HotspotTaskShare", min_value=0.0),
        wave_zone=_parse_optional_text_header(text, "WaveZone"),
        wave_radius=_parse_optional_int_header(text, "WaveRadius", min_value=0),
        deadline_slack_policy=_parse_optional_text_header(text, "DeadlineSlackPolicy"),
        deadline_slack=_parse_optional_float_header(text, "DeadlineSlack", min_value=0.0),
        max_replans=_parse_optional_int_header(text, "MaxReplans", min_value=0),
    )
    _validate_scenario_metadata(metadata)
    return metadata


def _validate_scenario_metadata(metadata: ScenarioMetadata) -> None:
    if metadata.hotspot_shelf_share is not None and metadata.hotspot_shelf_share > 1.0:
        raise ValueError("Scenario header 'HotspotShelfShare' must be <= 1.0.")
    if metadata.hotspot_task_share is not None and metadata.hotspot_task_share > 1.0:
        raise ValueError("Scenario header 'HotspotTaskShare' must be <= 1.0.")

    if metadata.influx == "Poisson" and metadata.lambda_value is None:
        raise ValueError("Scenario header 'Lambda' is required when Influx is Poisson.")
    if metadata.influx == "Burst":
        required_burst_headers = {
            "BurstAmount": metadata.burst_amount,
            "BurstStartStep": metadata.burst_start_step,
            "BurstDurationSteps": metadata.burst_duration_steps,
            "BurstAmplitude": metadata.burst_amplitude,
        }
        missing_headers = [label for label, value in required_burst_headers.items() if value is None]
        if missing_headers:
            raise ValueError(
                "Scenario is missing burst configuration headers: " + ", ".join(missing_headers) + "."
            )

    if metadata.spatial_distribution == "Hotspot":
        if metadata.hotspot_shelf_share is None or metadata.hotspot_task_share is None:
            raise ValueError(
                "Scenario headers 'HotspotShelfShare' and 'HotspotTaskShare' are required for Hotspot distribution."
            )
    if metadata.spatial_distribution == "Wave":
        if metadata.wave_zone is None or metadata.wave_radius is None:
            raise ValueError("Scenario headers 'WaveZone' and 'WaveRadius' are required for Wave distribution.")

    if metadata.deadline_slack_policy is not None and metadata.deadline_slack is None:
        raise ValueError("Scenario header 'DeadlineSlack' is required when DeadlineSlackPolicy is provided.")


def load_scenario_definition(path: Path) -> ScenarioDefinition:
    text = path.read_text(encoding="utf-8")
    agents_value = _find_header_value(text, "Agents")
    tasks_value = _find_header_value(text, "Tasks")
    size_value = _find_header_value(text, "Size")
    mode_value = _find_header_value(text, "Mode")
    station_value = _find_header_value(text, "Station")
    strategy_value = _find_header_value(text, "Strategy")
    algorithm_value = _find_header_value(text, "Algorithm")
    type_value = _find_header_value(text, "Type")
    layout_value = _find_header_value(text, "Layout")
    if (
        agents_value is None
        or tasks_value is None
        or mode_value is None
        or station_value is None
        or strategy_value is None
    ):
        raise ValueError(
            "Scenario file must contain 'Agents: N', 'Tasks: N', 'Mode: ...', "
            "'Station: ...' and 'Strategy: ...'."
        )

    agent_count = int(agents_value)
    expected_task_count = int(tasks_value)
    modes = _parse_choices(mode_value, _normalize_mode)
    station_modes = _parse_choices(station_value, _normalize_station_mode)
    strategies = _parse_choices(strategy_value, _normalize_strategy)
    if algorithm_value is not None:
        algorithms = _parse_choices(algorithm_value, normalize_algorithm_name)
    else:
        algorithms = ["BFS"]
    if type_value is not None:
        layout_types = _parse_choices(type_value, normalize_layout_type)
    else:
        layout_types = ["square"]
    layout_size = _parse_layout_size(size_value) if size_value is not None else None

    if layout_value is not None:
        layout_ids = _parse_layout_ids(layout_value)
    else:
        filename_match = re.search(r"(\d+)(?=\.txt$)", path.name)
        fallback_layout_id = int(filename_match.group(1)) if filename_match is not None else 0
        layout_ids = [fallback_layout_id]

    metadata = _parse_scenario_metadata(text)

    tasks: list[Task] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit():
            continue
        numbers = [int(part) for part in stripped.split()]
        if len(numbers) not in (3, 4, 5):
            raise ValueError(f"Invalid scenario line: {line}")
        release_time = 0
        deadline = None
        if len(numbers) >= 4:
            release_time = numbers[3]
        if len(numbers) == 5:
            deadline = numbers[4]

        tasks.append(
            Task(
                task_id=numbers[0],
                agent_id=numbers[1],
                shelf_index=numbers[2],
                release_time=release_time,
                deadline=deadline,
            )
        )

    if len(tasks) != expected_task_count:
        raise ValueError(f"Scenario declares {expected_task_count} tasks but contains {len(tasks)}.")

    return ScenarioDefinition(
        agent_count=agent_count,
        tasks=tasks,
        layout_size=layout_size,
        layout_ids=layout_ids,
        layout_types=layout_types,
        modes=modes,
        station_modes=station_modes,
        strategies=strategies,
        algorithms=algorithms,
        metadata=metadata,
    )


def expand_scenario_variants(definition: ScenarioDefinition) -> list[ScenarioVariant]:
    variants: list[ScenarioVariant] = []
    for layout_id in definition.layout_ids:
        for layout_type in definition.layout_types:
            for mode in definition.modes:
                strategies = ["None"] if mode == "Set" else definition.strategies
                for station_mode in definition.station_modes:
                    for algorithm in definition.algorithms:
                        for strategy in strategies:
                            variant = ScenarioVariant(
                                layout_id=layout_id,
                                layout_type=layout_type,
                                mode=mode,
                                station_mode=station_mode,
                                strategy=strategy,
                                algorithm=algorithm,
                            )
                            if variant not in variants:
                                variants.append(variant)
    return variants


def resolve_scenario_variant(
    definition: ScenarioDefinition,
    *,
    layout_id: int | None = None,
    layout_type: str | None = None,
    mode: str | None = None,
    station_mode: str | None = None,
    strategy: str | None = None,
    algorithm: str | None = None,
) -> ScenarioVariant:
    resolved_layout_id = definition.layout_ids[0] if layout_id is None else layout_id
    if resolved_layout_id not in definition.layout_ids:
        raise ValueError(f"Layout '{resolved_layout_id}' is not allowed by this scenario.")

    resolved_layout_type = normalize_layout_type(layout_type) if layout_type is not None else definition.layout_types[0]
    if resolved_layout_type not in definition.layout_types:
        raise ValueError(f"Layout type '{resolved_layout_type}' is not allowed by this scenario.")

    resolved_mode = _normalize_mode(mode) if mode is not None else definition.modes[0]
    if resolved_mode not in definition.modes:
        raise ValueError(f"Mode '{resolved_mode}' is not allowed by this scenario.")

    resolved_station = (
        _normalize_station_mode(station_mode) if station_mode is not None else definition.station_modes[0]
    )
    if resolved_station not in definition.station_modes:
        raise ValueError(f"Station mode '{resolved_station}' is not allowed by this scenario.")

    resolved_algorithm = normalize_algorithm_name(algorithm) if algorithm is not None else definition.algorithms[0]
    if resolved_algorithm not in definition.algorithms:
        raise ValueError(f"Algorithm '{resolved_algorithm}' is not allowed by this scenario.")

    if resolved_mode == "Set":
        return ScenarioVariant(
            layout_id=resolved_layout_id,
            layout_type=resolved_layout_type,
            mode=resolved_mode,
            station_mode=resolved_station,
            strategy="None",
            algorithm=resolved_algorithm,
        )

    resolved_strategy = _normalize_strategy(strategy) if strategy is not None else definition.strategies[0]
    if resolved_strategy not in definition.strategies:
        raise ValueError(f"Strategy '{resolved_strategy}' is not allowed by this scenario.")

    return ScenarioVariant(
        layout_id=resolved_layout_id,
        layout_type=resolved_layout_type,
        mode=resolved_mode,
        station_mode=resolved_station,
        strategy=resolved_strategy,
        algorithm=resolved_algorithm,
    )
