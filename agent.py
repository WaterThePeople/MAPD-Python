class Agent:
    def __init__(self, start_pos, agent_id):
        self.position = start_pos
        self.path = []
        self.id = agent_id
        self.task = None

    def step(self):
        if self.path:
            self.position = self.path.pop(0)

        if self.task:
            if not self.task.picked_up and self.position == self.task.pickup:
                self.task.picked_up = True
            if self.task.picked_up and not self.task.delivered and self.position == self.task.delivery:
                self.task.delivered = True