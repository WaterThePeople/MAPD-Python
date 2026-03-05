from __future__ import annotations

import re
from pathlib import Path

from mapd.models import Task
from mapd.warehouse import WarehouseMap


def load_layout(path: Path) -> WarehouseMap:
    rows = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return WarehouseMap(rows)


def load_scenario(path: Path) -> tuple[int, list[Task]]:
    text = path.read_text(encoding="utf-8")
    agents_match = re.search(r"Agents:\s*(\d+)", text)
    tasks_match = re.search(r"Tasks:\s*(\d+)", text)
    if not agents_match or not tasks_match:
        raise ValueError("Scenario file must contain 'Agents: N' and 'Tasks: N'.")

    agent_count = int(agents_match.group(1))
    expected_task_count = int(tasks_match.group(1))

    tasks: list[Task] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped[0].isdigit():
            continue
        numbers = [int(part) for part in stripped.split()]
        if len(numbers) != 3:
            raise ValueError(f"Invalid scenario line: {line}")
        tasks.append(Task(task_id=numbers[0], agent_id=numbers[1], location_index=numbers[2]))

    if len(tasks) != expected_task_count:
        raise ValueError(f"Scenario declares {expected_task_count} tasks but contains {len(tasks)}.")

    return agent_count, tasks
