class WarehouseGraph:

    def __init__(self, width, height):

        self.width = width
        self.height = height

        self.nodes = {}
        self.obstacles = set()
        self.adj = {}

        self._generate_grid()

    def _generate_grid(self):

        node_id = 1

        for y in range(self.height):
            for x in range(self.width):

                self.nodes[node_id] = (x, y)
                self.adj[node_id] = []

                node_id += 1

        for node_id, (x, y) in self.nodes.items():

            neighbors = [
                (x+1, y),
                (x-1, y),
                (x, y+1),
                (x, y-1)
            ]

            for nx, ny in neighbors:

                if 0 <= nx < self.width and 0 <= ny < self.height:

                    neighbor_id = ny * self.width + nx + 1
                    self.adj[node_id].append(neighbor_id)

    def add_obstacle(self, node_id):
        self.obstacles.add(node_id)

    def get_neighbors(self, node_id):

        neighbors = []

        for n in self.adj[node_id]:
            if n not in self.obstacles:
                neighbors.append(n)

        return neighbors

    def get_position(self, node_id):
        return self.nodes[node_id]

    def generate_rmfs_layout(self):
        for y in range(2, self.height-2, 4):
            for x in range(2, self.width-2, 7):

                for dy in range(2):
                    for dx in range(5):

                        nx = x + dx
                        ny = y + dy

                        node = ny * self.width + nx + 1
                        self.add_obstacle(node)