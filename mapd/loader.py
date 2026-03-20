import re
from pathlib import Path

from mapd.algorithms import normalize_algorithm_name
from mapd.models import Task
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


def load_scenario(path: Path) -> tuple[int, list[Task], str, str, str, str, int]:
    text = path.read_text(encoding="utf-8")
    agents_match = re.search(r"Agents:\s*(\d+)", text)
    tasks_match = re.search(r"Tasks:\s*(\d+)", text)
    mode_match = re.search(r"Mode:\s*(\w+)", text)
    station_match = re.search(r"Station:\s*(\w+)", text)
    strategy_match = re.search(r"Strategy:\s*(\w+)", text)
    algorithm_match = re.search(r"Algorithm:\s*([^\r\n]+)", text)
    layout_match = re.search(r"Layout:\s*(\d+)", text)
    if not agents_match or not tasks_match or not mode_match or not station_match or not strategy_match:
        raise ValueError(
            "Scenario file must contain 'Agents: N', 'Tasks: N', 'Mode: Set|Available', "
            "'Station: Set|Available' and 'Strategy: FCFS|GreedyCost|Robin|None'."
        )

    agent_count = int(agents_match.group(1))
    expected_task_count = int(tasks_match.group(1))
    mode = mode_match.group(1)
    if mode not in ("Set", "Available"):
        raise ValueError(f"Unsupported scenario mode: {mode}")

    station_mode = station_match.group(1)
    if station_mode not in ("Set", "Available"):
        raise ValueError(f"Unsupported station mode: {station_mode}")

    strategy_raw = strategy_match.group(1).strip().lower()
    strategy_map = {
        "fcfs": "FCFS",
        "greedy": "GreedyCost",
        "greedycost": "GreedyCost",
        "robin": "Robin",
        "none": "None",
    }
    if strategy_raw not in strategy_map:
        raise ValueError(f"Unsupported strategy: {strategy_match.group(1)}")
    strategy = strategy_map[strategy_raw]

    if algorithm_match is not None:
        algorithm = normalize_algorithm_name(algorithm_match.group(1))
    else:
        filename_match = re.search(r"_(bfs|astar|sipp|dijkstra)(?=_map\d+$|$)", path.stem, re.IGNORECASE)
        algorithm = normalize_algorithm_name(filename_match.group(1)) if filename_match is not None else "BFS"

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

    return agent_count, tasks, mode, station_mode, strategy, algorithm, layout_id
