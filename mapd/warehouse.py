from __future__ import annotations

from mapd.models import Coord


class WarehouseMap:
    def __init__(self, rows: list[str]) -> None:
        if not rows:
            raise ValueError("Layout is empty.")

        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("All layout rows must have the same width.")

        allowed = {"E", "S", "#"}
        invalid = {cell for row in rows for cell in row if cell not in allowed}
        if invalid:
            raise ValueError(f"Unsupported layout symbols: {sorted(invalid)}")

        self.rows = rows
        self.height = len(rows)
        self.width = width

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
        row, col = coord
        result: list[Coord] = []
        for next_coord in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if next_coord in self.traversable:
                result.append(next_coord)
        return result

    def pickup_positions(self, location_index: int) -> set[Coord]:
        location = self.index_to_coord(location_index)
        if location in self.traversable:
            return {location}
        if location in self.shelves:
            positions = {coord for coord in self.neighbors(location)}
            if not positions:
                raise ValueError(f"Shelf {location_index} has no accessible pickup position.")
            return positions
        raise ValueError(f"Task location {location_index} is not a valid map position.")
