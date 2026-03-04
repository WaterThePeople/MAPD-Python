class Agent:
    def __init__(self, start_node, agent_id):
        self.node = start_node
        self.path = []
        self.id = agent_id
        self.task = None

    def step(self):
        if self.path:
            self.node = self.path.pop(0)

        if self.task:
            if not self.task.picked_up and self.node == self.task.pickup:
                self.task.picked_up = True

            if self.task.picked_up and not self.task.delivered and self.node == self.task.delivery:
                self.task.delivered = True