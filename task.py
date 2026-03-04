class Task:
    def __init__(self, pickup_node, delivery_node,
                 release_time=0, due_time=0, priority=0):

        self.pickup = pickup_node
        self.delivery = delivery_node

        self.release_time = release_time
        self.due_time = due_time
        self.priority = priority

        self.picked_up = False
        self.delivered = False