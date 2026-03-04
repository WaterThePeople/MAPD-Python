from task import Task


def load_instance(filename):
    tasks = []

    with open(filename, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    num_agents = int(lines[4])
    num_tasks = int(lines[5])

    task_lines = lines[8:]

    for line in task_lines:
        parts = line.split()

        pickup = int(parts[1])
        delivery = int(parts[2])
        release_time = int(parts[3])
        due_time = int(parts[4])
        priority = int(parts[5])

        tasks.append(
            Task(pickup, delivery,
                 release_time,
                 due_time,
                 priority)
        )

    return num_agents, tasks