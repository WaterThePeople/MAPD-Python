class ReservationTable:
    def __init__(self):
        self.vertex_reservations = {}  # (x,y,t)
        self.edge_reservations = {}    # ((x1,y1),(x2,y2),t)

    def reserve(self, path, start_time=0):
        for t in range(len(path)):
            x, y = path[t]
            self.vertex_reservations[(x, y, start_time + t)] = True

            if t > 0:
                prev = path[t - 1]
                self.edge_reservations[(prev, (x, y), start_time + t)] = True

    def is_reserved(self, current, next_pos, t):

        # vertex conflict
        if (next_pos[0], next_pos[1], t) in self.vertex_reservations:
            return True

        # edge conflict (swap)
        if (next_pos, current, t) in self.edge_reservations:
            return True

        return False