class Task:
    def __init__(self, pickup, delivery):
        self.pickup = pickup
        self.delivery = delivery
        self.picked_up = False
        self.delivered = False