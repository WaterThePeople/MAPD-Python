from mapd.models import Coord


class WarehouseMap:
    SUPPORTED_LAYOUT_TYPES = {"square", "hexagon", "triangle"}

    def __init__(
        self,
        rows: list[str],
        *,
        layout_type: str = "square",
        shelf_slots: list[Coord] | None = None,
    ) -> None:
        if not rows:
            raise ValueError("Layout is empty.")

        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("All layout rows must have the same width.")

        if layout_type not in self.SUPPORTED_LAYOUT_TYPES:
            raise ValueError(f"Unsupported layout type: {layout_type}")

        allowed = {"E", "S", "#"}
        invalid = set()
        for row in rows:
            for cell in row:
                if cell not in allowed:
                    invalid.add(cell)

        if invalid:
            raise ValueError(f"Unsupported layout symbols: {sorted(invalid)}")

        self.rows = rows
        self.height = len(rows)
        self.width = width
        self.layout_type = layout_type

        self.traversable: set[Coord] = set()
        self.stations: list[Coord] = []
        self.shelves: set[Coord] = set()

        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                coord = (row_idx, col_idx)
                if cell in {"E", "S"}:
                    self.traversable.add(coord)
                if cell == "S":
                    self.stations.append(coord)
                if cell == "#":
                    self.shelves.add(coord)

        if shelf_slots is None:
            self.shelf_slots = sorted(self.shelves, key=self.coord_to_index)
        else:
            self.shelf_slots = shelf_slots[:]

        if len(self.shelf_slots) != len(self.shelves):
            raise ValueError("Shelf slot definitions do not match the shelf cells present in the layout.")

        if set(self.shelf_slots) != self.shelves:
            raise ValueError("Shelf slot ordering must reference every shelf cell exactly once.")

        self._shelf_index_by_coord = {coord: index for index, coord in enumerate(self.shelf_slots)}

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    @property
    def shelf_count(self) -> int:
        return len(self.shelf_slots)

    def index_to_coord(self, index: int) -> Coord:
        if index < 0 or index >= self.cell_count:
            raise ValueError(f"Cell index {index} is outside the layout.")
        return divmod(index, self.width)

    def coord_to_index(self, coord: Coord) -> int:
        row, col = coord
        return row * self.width + col

    def shelf_index_to_coord(self, shelf_index: int) -> Coord:
        if shelf_index < 0 or shelf_index >= self.shelf_count:
            raise ValueError(f"Shelf index {shelf_index} is outside the layout.")
        return self.shelf_slots[shelf_index]

    def coord_to_shelf_index(self, coord: Coord) -> int:
        if coord not in self._shelf_index_by_coord:
            raise ValueError(f"Coordinate {coord} is not a shelf cell.")
        return self._shelf_index_by_coord[coord]

    def neighbors(self, coord: Coord) -> list[Coord]:
        return [next_coord for next_coord in self._candidate_neighbors(coord) if next_coord in self.traversable]

    def distance(self, src: Coord, dst: Coord) -> int:
        if self.layout_type == "hexagon":
            return self._hex_distance(src, dst)
        if self.layout_type == "triangle":
            return self._triangle_distance(src, dst)
        return abs(src[0] - dst[0]) + abs(src[1] - dst[1])

    def pickup_positions(self, shelf_index: int) -> set[Coord]:
        shelf_coord = self.shelf_index_to_coord(shelf_index)
        positions = set(self.neighbors(shelf_coord))
        if not positions:
            positions = self._component_pickup_positions(shelf_coord)
        if not positions:
            raise ValueError(f"Shelf {shelf_index} has no accessible pickup position.")
        return positions

    def _candidate_neighbors(self, coord: Coord) -> list[Coord]:
        if self.layout_type == "hexagon":
            return self._hex_candidates(coord)
        if self.layout_type == "triangle":
            return self._triangle_candidates(coord)
        return self._square_candidates(coord)

    def _component_pickup_positions(self, start: Coord) -> set[Coord]:
        component_depth = {start: 0}
        queue = [start]
        positions = set()
        best_depth = None

        while queue:
            current = queue.pop(0)
            depth = component_depth[current]
            direct_positions = {next_coord for next_coord in self._candidate_neighbors(current) if next_coord in self.traversable}
            if direct_positions:
                if best_depth is None:
                    best_depth = depth
                if depth == best_depth:
                    positions.update(direct_positions)
                continue

            if best_depth is not None and depth >= best_depth:
                continue

            for next_coord in self._candidate_neighbors(current):
                if next_coord in self.shelves and next_coord not in component_depth:
                    component_depth[next_coord] = depth + 1
                    queue.append(next_coord)

        if positions:
            return positions

        component = set(component_depth)
        positions = set()
        for shelf_coord in component:
            for next_coord in self._candidate_neighbors(shelf_coord):
                if next_coord in self.traversable:
                    positions.add(next_coord)
        return positions

    def _square_candidates(self, coord: Coord) -> list[Coord]:
        row, col = coord
        return [
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ]

    def _hex_candidates(self, coord: Coord) -> list[Coord]:
        row, col = coord
        if row % 2 == 0:
            return [
                (row, col - 1),
                (row, col + 1),
                (row - 1, col - 1),
                (row - 1, col),
                (row + 1, col - 1),
                (row + 1, col),
            ]
        return [
            (row, col - 1),
            (row, col + 1),
            (row - 1, col),
            (row - 1, col + 1),
            (row + 1, col),
            (row + 1, col + 1),
        ]

    def _hex_distance(self, src: Coord, dst: Coord) -> int:
        src_x, src_y, src_z = self._hex_cube(src)
        dst_x, dst_y, dst_z = self._hex_cube(dst)
        return max(abs(src_x - dst_x), abs(src_y - dst_y), abs(src_z - dst_z))

    def _hex_cube(self, coord: Coord) -> tuple[int, int, int]:
        row, col = coord
        x = col - ((row - (row & 1)) // 2)
        z = row
        y = -x - z
        return x, y, z

    def _triangle_candidates(self, coord: Coord) -> list[Coord]:
        row, col = coord
        if self._triangle_points_up(coord):
            return [
                (row, col - 1),
                (row, col + 1),
                (row + 1, col),
            ]
        return [
            (row, col - 1),
            (row, col + 1),
            (row - 1, col),
        ]

    def _triangle_distance(self, src: Coord, dst: Coord) -> int:
        # Every move changes either the row or the column by exactly one,
        # so Manhattan distance stays an admissible lower bound on the
        # orientation-constrained triangular grid.
        return abs(src[0] - dst[0]) + abs(src[1] - dst[1])

    def _triangle_points_up(self, coord: Coord) -> bool:
        row, col = coord
        return (row + col) % 2 == 0
