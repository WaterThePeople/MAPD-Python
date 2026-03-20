import re
from pathlib import Path

from mapd.algorithms import normalize_algorithm_name
from mapd.models import ScenarioDefinition, ScenarioVariant, Task
from mapd.warehouse import WarehouseMap


def load_layout(path: Path) -> WarehouseMap:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(stripped)
    return WarehouseMap(rows)


def layout_path(layout_id: int, layouts_root: Path | None = None) -> Path:
    root = Path("layouts") if layouts_root is None else layouts_root
    candidate = root / str(layout_id) / f"{layout_id}.txt"
    if candidate.exists():
        return candidate

    layout_dir = root / str(layout_id)
    if layout_dir.is_dir():
        text_files = sorted(layout_dir.glob("*.txt"))
        if len(text_files) == 1:
            return text_files[0]

    raise FileNotFoundError(f"Layout {layout_id} not found under {root}.")


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
