from environment import Environment
from agent import Agent
from task import Task
from simulation import Simulation
from reservation_table import ReservationTable
from multi_agent_planner import space_time_a_star
from visualization import Visualizer


def main():
    env = Environment(15, 15)

    for i in range(3, 12):
        env.add_obstacle(i, 7)
    agents = [
        Agent((1, 1), 0),
        Agent((1, 3), 1),
        Agent((1, 5), 2)
    ]

    tasks = [
        Task((2, 12), (13, 2)),
        Task((2, 10), (13, 4)),
        Task((2, 8), (13, 6))
    ]

    for agent, task in zip(agents, tasks):
        agent.task = task

    reservation_table = ReservationTable()

    for agent, task in zip(agents, tasks):

        path1 = space_time_a_star(
            env,
            agent.position,
            task.pickup,
            reservation_table
        )

        reservation_table.reserve(path1)

        path2 = space_time_a_star(
            env,
            task.pickup,
            task.delivery,
            reservation_table,
            start_time=len(path1)
        )

        reservation_table.reserve(path2, start_time=len(path1))

        agent.path = path1 + path2

    sim = Simulation(env, agents, tasks)

    vis = Visualizer(sim)
    vis.save_gif("multi_agent_simulation.gif", fps=3)


if __name__ == "__main__":
    main()