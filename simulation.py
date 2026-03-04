class Simulation:
    def __init__(self, graph, agents, tasks):
        self.graph = graph
        self.agents = agents
        self.tasks = tasks
        self.time = 0

    def step(self):
        print(f"Czas: {self.time}")

        for agent in self.agents:
             print(f"  Agent {agent.id} @ {agent.node}, path left: {len(agent.path)}")

        for agent in self.agents:
            agent.step()

        self.time += 1

    def all_finished(self):
        return all(len(agent.path) == 0 for agent in self.agents)