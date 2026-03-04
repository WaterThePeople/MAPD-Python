class ReservationTable:
    def __init__(self):
        self.vertex_reservations = {}  # (node, t)
        self.edge_reservations = {}    # (node1, node2, t)

    def reserve(self, path, start_time=0):
        for t in range(len(path)):
            node = path[t]
            self.vertex_reservations[(node, start_time + t)] = True

            if t > 0:
                prev = path[t - 1]
                self.edge_reservations[(prev, node, start_time + t)] = True

    def is_reserved(self, current, next_node, t):

        if (next_node, t) in self.vertex_reservations:
            return True

        if (next_node, current, t) in self.edge_reservations:
            return True

        return False