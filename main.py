from warehouse_graph import WarehouseGraph
from agent import Agent
from reservation_table import ReservationTable
from multi_agent_planner import space_time_a_star
from simulation import Simulation
from benchmark_loader import load_instance
from visualization import Visualizer


def main():

    print("=== START SYMULACJI MAPD ===")

    graph = WarehouseGraph()
    graph.generate_grid_graph(30, 30)

    print("Graf wygenerowany.")
    print("Liczba węzłów:", len(graph.nodes))

    num_agents, tasks = load_instance("maps/AC0010.txt")

    print("Wczytano instancję.")
    print("Liczba agentów:", num_agents)
    print("Liczba zadań:", len(tasks))

    agents = [
        Agent(start_node=i + 1, agent_id=i)
        for i in range(num_agents)
    ]

    reservation_table = ReservationTable()

    print("\n=== PLANOWANIE ŚCIEŻEK ===")

    for agent, task in zip(agents, tasks[:num_agents]):

        print(f"\nAgent {agent.id}:")
        print(f"  Start: {agent.node}")
        print(f"  Pickup: {task.pickup}")
        print(f"  Delivery: {task.delivery}")

        path1 = space_time_a_star(
            graph,
            agent.node,
            task.pickup,
            reservation_table
        )

        if not path1:
            print("Nie znaleziono ścieżki do pickup!")
            continue

        reservation_table.reserve(path1)

        path2 = space_time_a_star(
            graph,
            task.pickup,
            task.delivery,
            reservation_table,
            start_time=len(path1)
        )

        if not path2:
            print("Nie znaleziono ścieżki do delivery!")
            continue

        reservation_table.reserve(path2, start_time=len(path1))

        agent.path = path1 + path2
        agent.task = task

        print(f"Ścieżka zaplanowana.")
        print(f"Długość do pickup: {len(path1)}")
        print(f"Długość do delivery: {len(path2)}")
        print(f"Łączna długość: {len(agent.path)}")

        if path2[-1] != task.delivery:
            path2.append(task.delivery)
        agent.path = path1 + path2

    print("\n=== START SYMULACJI RUCHU ===")

    sim = Simulation(graph, agents, tasks)
    vis = Visualizer(sim)
    vis.save_gif("AC0010_simulation.gif", fps=3)


if __name__ == "__main__":
    main()