from warehouse_graph import WarehouseGraph
from agent import Agent
from reservation_table import ReservationTable
from multi_agent_planner import space_time_a_star
from simulation import Simulation
from benchmark_loader import load_instance
from visualization import Visualizer


def main():

    print("=== START SYMULACJI MAPD ===")

    graph = WarehouseGraph(25, 17)
    graph.generate_rmfs_layout()

    num_agents, tasks = load_instance("maps/AC0010.txt")

    agents = [
        Agent(start_node=i + 1, agent_id=i)
        for i in range(num_agents)
    ]

    reservation_table = ReservationTable()

    for agent in agents:
        reservation_table.reserve_start(agent.node)

    print("\n=== PLANOWANIE ŚCIEŻEK ===")

    for agent, task in zip(agents, tasks[:num_agents]):

        print(f"\nAgent {agent.id}")
        print(f"start {agent.node} -> pickup {task.pickup} -> delivery {task.delivery}")

        path1 = space_time_a_star(
            graph,
            agent.node,
            task.pickup,
            reservation_table
        )

        if not path1:
            print("Brak ścieżki do pickup")
            continue

        path2 = space_time_a_star(
            graph,
            task.pickup,
            task.delivery,
            reservation_table,
            start_time=len(path1) - 1
        )

        if not path2:
            print("Brak ścieżki do delivery")
            continue

        path2 = path2[1:]

        full_path = path1 + path2

        reservation_table.reserve(full_path)

        agent.path = full_path
        agent.task = task

        print("Długość ścieżki:", len(full_path))

    print("\n=== START SYMULACJI ===")

    sim = Simulation(graph, agents, tasks)

    vis = Visualizer(sim)

    vis.save_gif("simulation.gif", fps=3)


if __name__ == "__main__":
    main()