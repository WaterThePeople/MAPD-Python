class WarehouseGraph:
    def __init__(self):
        self.nodes = {}      # node_id -> (x, y)
        self.adj = {}        # node_id -> [neighbors]

    def generate_grid_graph(self, width, height):
        node_id = 1
        for y in range(height):
            for x in range(width):
                self.nodes[node_id] = (x, y)
                self.adj[node_id] = []
                node_id += 1

        for id1, (x1, y1) in self.nodes.items():
            for id2, (x2, y2) in self.nodes.items():
                if id1 != id2:
                    if abs(x1 - x2) + abs(y1 - y2) == 1:
                        self.adj[id1].append(id2)

    def get_neighbors(self, node_id):
        return self.adj.get(node_id, [])

    def get_position(self, node_id):
        return self.nodes[node_id]