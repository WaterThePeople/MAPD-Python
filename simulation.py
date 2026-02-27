class Simulation:
    def __init__(self, environment, agents, tasks):
        self.environment = environment
        self.agents = agents
        self.tasks = tasks
        self.time = 0

    def step(self):
        for agent in self.agents:
            agent.step()
        self.time += 1

    def all_finished(self):
        return all(len(agent.path) == 0 for agent in self.agents)