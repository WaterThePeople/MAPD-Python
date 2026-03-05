class ReservationTable:

    def __init__(self):

        self.vertex = set()
        self.edge = set()

    def reserve(self, path, start_time=0):

        for t in range(len(path)):

            node = path[t]

            self.vertex.add((node, start_time + t))

            if t > 0:

                prev = path[t-1]

                self.edge.add((prev, node, start_time + t))

        goal = path[-1]

        for t in range(start_time + len(path), start_time + len(path) + 500):

            self.vertex.add((goal, t))

    def reserve_start(self, node):

        self.vertex.add((node, 0))

    def is_reserved(self, current, next_node, t):

        if (next_node, t) in self.vertex:
            return True

        if (next_node, current, t) in self.edge:
            return True

        return False