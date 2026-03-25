import json
import re
from pathlib import Path

from mapd.algorithms import normalize_algorithm_name
from mapd.models import ScenarioDefinition, ScenarioVariant, Task
from mapd.warehouse import WarehouseMap


def normalize_layout_type(value: str | None) -> str:
    normalized = "square" if value is None else value.strip().lower()
    if normalized not in WarehouseMap.SUPPORTED_LAYOUT_TYPES:
        raise ValueError(
            f"Unsupported layout type: {value}. Expected one of {sorted(WarehouseMap.SUPPORTED_LAYOUT_TYPES)}."
        )
    return normalized


def detect_layout_type(path: Path, default: str | None = "square") -> str:
    for part in reversed(path.parts):
        lowered = part.lower()
        if lowered in WarehouseMap.SUPPORTED_LAYOUT_TYPES:
            return lowered

    if path.suffix.lower() == ".json":
        raw_layout = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw_layout, dict):
            type_value = raw_layout.get("type")
            if isinstance(type_value, str):
                return normalize_layout_type(type_value)

    return normalize_layout_type(default)


def load_layout(path: Path) -> WarehouseMap:
    if path.suffix.lower() == ".json":
        return _load_layout_from_json(path)

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(stripped)
    return WarehouseMap(rows, layout_type=detect_layout_type(path))


def layout_path(layout_id: int, layout_type: str = "square", layouts_root: Path | None = None) -> Path:
    normalized_layout_type = normalize_layout_type(layout_type)
    root = Path("layouts") if layouts_root is None else layouts_root
    layout_dir = root / normalized_layout_type / str(layout_id)
    for suffix in (".json", ".txt"):
        candidate = layout_dir / f"{layout_id}{suffix}"
        if candidate.exists():
            return candidate

    if layout_dir.is_dir():
        layout_files = sorted(layout_dir.glob("*.json"))
        if len(layout_files) == 1:
            return layout_files[0]

        text_files = sorted(layout_dir.glob("*.txt"))
        if len(text_files) == 1:
            return text_files[0]

    legacy_layout_dir = root / str(layout_id)
    for suffix in (".json", ".txt"):
        candidate = legacy_layout_dir / f"{layout_id}{suffix}"
        if candidate.exists():
            return candidate

    if legacy_layout_dir.is_dir():
        layout_files = sorted(legacy_layout_dir.glob("*.json"))
        if len(layout_files) == 1:
            return layout_files[0]

        text_files = sorted(legacy_layout_dir.glob("*.txt"))
        if len(text_files) == 1:
            return text_files[0]

    raise FileNotFoundError(f"Layout {layout_id} of type '{normalized_layout_type}' not found under {root}.")


def _load_layout_from_json(path: Path) -> WarehouseMap:
    raw_layout = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_layout, dict):
        raise ValueError(f"Layout JSON must be an object: {path}")

    layout_type_from_path = None
    for part in reversed(path.parts):
        lowered = part.lower()
        if lowered in WarehouseMap.SUPPORTED_LAYOUT_TYPES:
            layout_type_from_path = lowered
            break

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

    type_value = raw_layout.get("type")
    if type_value is None and layout_type_from_path is not None:
        layout_type = layout_type_from_path
    else:
        layout_type = normalize_layout_type(type_value)

    if layout_type_from_path is not None and layout_type != layout_type_from_path:
        raise ValueError(
            f"Layout type mismatch for {path}: JSON declares '{layout_type}', directory implies '{layout_type_from_path}'."
        )

    grid = [["E" for _ in range(width)] for _ in range(height)]
    _fill_areas(grid, shelves, width, height, "#", "shelves", path)
    _fill_areas(grid, stations, width, height, "S", "stations", path)
    rows = ["".join(row) for row in grid]
    return WarehouseMap(rows, layout_type=layout_type)


def _fill_areas(
    grid: list[list[str]],
    areas: list[object],
    width: int,
    height: int,
    symbol: str,
    label: str,
    path: Path,
) -> None:
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


def load_scenario_definition(path: Path) -> ScenarioDefinition:
    text = path.read_text(encoding="utf-8")
    agents_match = re.search(r"Agents:\s*(\d+)", text)
    tasks_match = re.search(r"Tasks:\s*(\d+)", text)
    mode_match = re.search(r"Mode:\s*([^\r\n]+)", text)
    station_match = re.search(r"Station:\s*([^\r\n]+)", text)
    strategy_match = re.search(r"Strategy:\s*([^\r\n]+)", text)
    algorithm_match = re.search(r"Algorithm:\s*([^\r\n]+)", text)
    layout_match = re.search(r"Layout:\s*(\d+)", text)
    if not agents_match or not tasks_match or not mode_match or not station_match or not strategy_match:
        raise ValueError(
            "Scenario file must contain 'Agents: N', 'Tasks: N', 'Mode: ...', "
            "'Station: ...' and 'Strategy: ...'."
        )

    agent_count = int(agents_match.group(1))
    expected_task_count = int(tasks_match.group(1))
    modes = _parse_choices(mode_match.group(1), _normalize_mode)
    station_modes = _parse_choices(station_match.group(1), _normalize_station_mode)
    strategies = _parse_choices(strategy_match.group(1), _normalize_strategy)
    if algorithm_match is not None:
        algorithms = _parse_choices(algorithm_match.group(1), normalize_algorithm_name)
    else:
        algorithms = ["BFS"]

    if layout_match is not None:
        layout_id = int(layout_match.group(1))
    else:
        filename_match = re.search(r"_map(\d+)(?=\.txt$)", path.name)
        layout_id = int(filename_match.group(1)) if filename_match is not None else 0

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
                location_index=numbers[2],
                release_time=release_time,
                deadline=deadline,
            )
        )

    if len(tasks) != expected_task_count:
        raise ValueError(f"Scenario declares {expected_task_count} tasks but contains {len(tasks)}.")

    return ScenarioDefinition(
        agent_count=agent_count,
        tasks=tasks,
        layout_id=layout_id,
        modes=modes,
        station_modes=station_modes,
        strategies=strategies,
        algorithms=algorithms,
    )


def expand_scenario_variants(definition: ScenarioDefinition) -> list[ScenarioVariant]:
    variants: list[ScenarioVariant] = []
    for mode in definition.modes:
        strategies = ["None"] if mode == "Set" else definition.strategies
        for station_mode in definition.station_modes:
            for algorithm in definition.algorithms:
                for strategy in strategies:
                    variant = ScenarioVariant(
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
    mode: str | None = None,
    station_mode: str | None = None,
    strategy: str | None = None,
    algorithm: str | None = None,
) -> ScenarioVariant:
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
            mode=resolved_mode,
            station_mode=resolved_station,
            strategy="None",
            algorithm=resolved_algorithm,
        )

    resolved_strategy = _normalize_strategy(strategy) if strategy is not None else definition.strategies[0]
    if resolved_strategy not in definition.strategies:
        raise ValueError(f"Strategy '{resolved_strategy}' is not allowed by this scenario.")

    return ScenarioVariant(
        mode=resolved_mode,
        station_mode=resolved_station,
        strategy=resolved_strategy,
        algorithm=resolved_algorithm,
    )
