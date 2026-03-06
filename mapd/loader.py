import re
from pathlib import Path

from mapd.models import Task
from mapd.warehouse import WarehouseMap


def load_layout(path: Path) -> WarehouseMap:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(stripped)
    return WarehouseMap(rows)


def load_scenario(path: Path) -> tuple[int, list[Task]]:
    text = path.read_text(encoding="utf-8")
    agents_match = re.search(r"Agents:\s*(\d+)", text)
    tasks_match = re.search(r"Tasks:\s*(\d+)", text)
    mode_match = re.search(r"Mode:\s*(\w+)", text)
    if not agents_match or not tasks_match or not mode_match:
        raise ValueError("Scenario file must contain 'Agents: N', 'Tasks: N' and 'Mode: Set|Available'.")

    agent_count = int(agents_match.group(1))
    expected_task_count = int(tasks_match.group(1))
    mode = mode_match.group(1)
    if mode not in ("Set", "Available"):
        raise ValueError(f"Unsupported scenario mode: {mode}")

    tasks: list[Task] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit():
            continue
        numbers = [int(part) for part in stripped.split()]
        if len(numbers) not in (3, 4):
            raise ValueError(f"Invalid scenario line: {line}")
        release_time = 0
        if len(numbers) == 4:
            release_time = numbers[3]

        tasks.append(
            Task(
                task_id=numbers[0],
                agent_id=numbers[1],
                location_index=numbers[2],
                release_time=release_time,
            )
        )

    if len(tasks) != expected_task_count:
        raise ValueError(f"Scenario declares {expected_task_count} tasks but contains {len(tasks)}.")

    return agent_count, tasks, mode
