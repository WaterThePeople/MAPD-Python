from mapd.models import Coord


class WarehouseMap:
    SUPPORTED_LAYOUT_TYPES = {"square", "hexagon"}

    def __init__(self, rows: list[str], *, layout_type: str = "square") -> None:
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

    @property
    def cell_count(self) -> int:
        return self.width * self.height

    def index_to_coord(self, index: int) -> Coord:
        if index < 0 or index >= self.cell_count:
            raise ValueError(f"Cell index {index} is outside the layout.")
        return divmod(index, self.width)

    def coord_to_index(self, coord: Coord) -> int:
        row, col = coord
        return row * self.width + col

    def neighbors(self, coord: Coord) -> list[Coord]:
        if self.layout_type == "hexagon":
            return self._hex_neighbors(coord)

        row, col = coord
        result = []
        candidates = [
            (row - 1, col),
            (row + 1, col),
            (row, col - 1),
            (row, col + 1),
        ]

        for next_coord in candidates:
            if next_coord in self.traversable:
                result.append(next_coord)
        return result

    def distance(self, src: Coord, dst: Coord) -> int:
        if self.layout_type == "hexagon":
            return self._hex_distance(src, dst)
        return abs(src[0] - dst[0]) + abs(src[1] - dst[1])

    def pickup_positions(self, location_index: int) -> set[Coord]:
        location = self.index_to_coord(location_index)
        if location in self.traversable:
            return {location}

        if location in self.shelves:
            positions = set()
            for coord in self.neighbors(location):
                positions.add(coord)
            if not positions:
                raise ValueError(f"Shelf {location_index} has no accessible pickup position.")
            return positions

        raise ValueError(f"Task location {location_index} is not a valid map position.")

    def _hex_neighbors(self, coord: Coord) -> list[Coord]:
        row, col = coord
        if row % 2 == 0:
            candidates = [
                (row, col - 1),
                (row, col + 1),
                (row - 1, col - 1),
                (row - 1, col),
                (row + 1, col - 1),
                (row + 1, col),
            ]
        else:
            candidates = [
                (row, col - 1),
                (row, col + 1),
                (row - 1, col),
                (row - 1, col + 1),
                (row + 1, col),
                (row + 1, col + 1),
            ]

        return [next_coord for next_coord in candidates if next_coord in self.traversable]

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
