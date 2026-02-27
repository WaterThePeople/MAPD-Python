import numpy as np


class Environment:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.grid = np.zeros((height, width))

    def add_obstacle(self, x, y):
        self.grid[y, x] = 1

    def is_free(self, x, y):
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y, x] == 0
        return False

    def get_neighbors(self, x, y):
        moves = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        neighbors = []

        for dx, dy in moves:
            nx, ny = x + dx, y + dy
            if self.is_free(nx, ny):
                neighbors.append((nx, ny))

        return neighbors