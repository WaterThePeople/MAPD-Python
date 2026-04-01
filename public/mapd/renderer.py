import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mapd.collisions import frame_collision_info, frame_agent_positions
from mapd.models import AgentPlan, Coord
from mapd.warehouse import WarehouseMap


HEX_ASPECT_RATIO = 0.8660254
HEX_VERTICAL_STEP_RATIO = 0.75
HEX_GAP = 3
TRIANGLE_HEIGHT_RATIO = 0.8660254
TRIANGLE_GAP = 1
MAX_RENDER_WIDTH = 1440
MAX_RENDER_HEIGHT = 900


def scaled_stroke_width(size: float, divisor: float, minimum: int = 1, maximum: int | None = None) -> int:
    width = max(minimum, int(round(size / divisor)))
    if maximum is not None:
        width = min(width, maximum)
    return width


def draw_cross(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    color: tuple[int, int, int],
) -> None:
    left, top, right, bottom = bounds
    size = min(right - left, bottom - top)
    if size <= 2:
        return
    max_padding = max(1, int(size / 2) - 1)
    padding = min(max(1, int(size // 5)), max_padding)
    stroke_width = scaled_stroke_width(size, 7)
    draw.line(
        (left + padding, top + padding, right - padding, bottom - padding),
        fill=color,
        width=stroke_width,
    )
    draw.line(
        (left + padding, bottom - padding, right - padding, top + padding),
        fill=color,
        width=stroke_width,
    )


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    text: str,
    fill: tuple[int, int, int],
    font,
) -> None:
    left, top, right, bottom = bounds
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = left + (right - left - text_width) / 2
    text_y = top + (bottom - top - text_height) / 2 - 1
    draw.text((text_x, text_y), text, fill=fill, font=font)


def draw_header_stats(
    draw: ImageDraw.ImageDraw,
    board_width: int,
    header_height: int,
    time: int,
    done_count: int,
    total_tasks: int,
    missed_count: int,
    collision_count: int,
    font,
) -> None:
    segments = [
        (f"Time: {time}", (30, 30, 30)),
        (f"Done: {done_count}/{total_tasks}", (30, 30, 30)),
        (f"Missed deadlines: {missed_count}", (180, 40, 40) if missed_count else (30, 30, 30)),
        (f"Collisions: {collision_count}", (180, 40, 40) if collision_count else (30, 30, 30)),
    ]

    x = 8
    y = max(6, int((header_height - 12) / 2) - 1)
    separator = "   |   "

    for index, (text, color) in enumerate(segments):
        draw.text((x, y), text, fill=color, font=font)
        bbox = draw.textbbox((x, y), text, font=font)
        x = bbox[2]
        if index < len(segments) - 1:
            draw.text((x, y), separator, fill=(120, 120, 120), font=font)
            separator_bbox = draw.textbbox((x, y), separator, font=font)
            x = separator_bbox[2]

        if x >= board_width - 8:
            break


def draw_agent(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    color: tuple[int, int, int],
    label: str | None,
    font,
) -> None:
    left, top, right, bottom = bounds
    size = min(right - left, bottom - top)
    if size <= 2:
        return
    max_padding = max(1, int(size / 2) - 1)
    padding = min(max(1, int(size // 6)), max_padding)
    circle_bounds = (left + padding, top + padding, right - padding, bottom - padding)
    draw.ellipse(circle_bounds, fill=color, outline=(20, 20, 20), width=scaled_stroke_width(size, 10))
    if label and size >= 14:
        draw_centered_text(draw, (left, top, right, bottom), label, (255, 255, 255), font)


def clear_debug_frames(debug_frames_dir: Path) -> None:
    debug_frames_dir.mkdir(parents=True, exist_ok=True)
    for existing_frame in debug_frames_dir.glob("frame_*.png"):
        existing_frame.unlink()


def build_task_maps(
    warehouse: WarehouseMap,
    plans: list[AgentPlan],
) -> tuple[dict[int, int], dict[int, int], dict[int, int | None], dict[Coord, list]]:
    package_pickups = {}
    package_release_times = {}
    package_deadlines = {}
    tasks_by_coord = defaultdict(list)

    for plan in plans:
        for task in plan.tasks:
            package_pickups[task.task_id] = plan.pickup_times[task.task_id]
            package_release_times[task.task_id] = task.release_time
            package_deadlines[task.task_id] = task.deadline
            tasks_by_coord[warehouse.shelf_index_to_coord(task.shelf_index)].append(task)

    return package_pickups, package_release_times, package_deadlines, tasks_by_coord


def carried_task_id(plan: AgentPlan, time: int) -> str | None:
    for task in plan.tasks:
        pickup_time = plan.pickup_times.get(task.task_id)
        completion_time = plan.completion_times.get(task.task_id)
        if pickup_time is None or completion_time is None:
            continue
        if pickup_time <= time < completion_time:
            return str(task.task_id)
    return None


def scale_polygon(
    polygon: list[tuple[float, float]],
    factor: float,
) -> list[tuple[float, float]]:
    center_x = sum(point[0] for point in polygon) / len(polygon)
    center_y = sum(point[1] for point in polygon) / len(polygon)
    return [
        (
            center_x + (point_x - center_x) * factor,
            center_y + (point_y - center_y) * factor,
        )
        for point_x, point_y in polygon
    ]


def draw_polygon_outline(
    draw: ImageDraw.ImageDraw,
    polygon: list[tuple[float, float]],
    color: tuple[int, int, int],
    width: int = 1,
) -> None:
    draw.line([*polygon, polygon[0]], fill=color, width=width)


def square_bounds(coord: Coord, cell_size: int, header_height: int) -> tuple[float, float, float, float]:
    row, col = coord
    left = col * cell_size
    top = row * cell_size + header_height
    return left, top, left + cell_size, top + cell_size


def hex_metrics(cell_size: int) -> dict[str, float]:
    hex_width = float(cell_size)
    hex_height = hex_width / HEX_ASPECT_RATIO
    step_x = hex_width + HEX_GAP
    row_offset = step_x / 2
    step_y = hex_height * HEX_VERTICAL_STEP_RATIO + HEX_GAP
    return {
        "hex_width": hex_width,
        "hex_height": hex_height,
        "step_x": step_x,
        "row_offset": row_offset,
        "step_y": step_y,
        "padding": float(HEX_GAP),
    }


def hex_bounds(coord: Coord, cell_size: int, header_height: int) -> tuple[float, float, float, float]:
    row, col = coord
    metrics = hex_metrics(cell_size)
    left = metrics["padding"] + col * metrics["step_x"] + (row % 2) * metrics["row_offset"]
    top = header_height + metrics["padding"] + row * metrics["step_y"]
    return left, top, left + metrics["hex_width"], top + metrics["hex_height"]


def hex_polygon(coord: Coord, cell_size: int, header_height: int) -> list[tuple[float, float]]:
    left, top, right, bottom = hex_bounds(coord, cell_size, header_height)
    width = right - left
    height = bottom - top
    return [
        (left + width * 0.5, top),
        (left + width * 0.933, top + height * 0.25),
        (left + width * 0.933, top + height * 0.75),
        (left + width * 0.5, bottom),
        (left + width * 0.067, top + height * 0.75),
        (left + width * 0.067, top + height * 0.25),
    ]


def triangle_metrics(cell_size: int) -> dict[str, float]:
    triangle_side = float(cell_size)
    triangle_height = triangle_side * TRIANGLE_HEIGHT_RATIO
    step_x = triangle_side / 2
    step_y = triangle_height
    return {
        "triangle_side": triangle_side,
        "triangle_height": triangle_height,
        "step_x": step_x,
        "step_y": step_y,
        "padding": float(TRIANGLE_GAP),
    }


def triangle_bounds(coord: Coord, cell_size: int, header_height: int) -> tuple[float, float, float, float]:
    row, col = coord
    metrics = triangle_metrics(cell_size)
    left = metrics["padding"] + col * metrics["step_x"]
    top = header_height + metrics["padding"] + row * metrics["step_y"]
    return left, top, left + metrics["triangle_side"], top + metrics["triangle_height"]


def triangle_points_up(coord: Coord) -> bool:
    row, col = coord
    return (row + col) % 2 == 0


def triangle_polygon(coord: Coord, cell_size: int, header_height: int) -> list[tuple[float, float]]:
    left, top, right, bottom = triangle_bounds(coord, cell_size, header_height)
    width = right - left
    if triangle_points_up(coord):
        return [
            (left + width * 0.5, top),
            (left, bottom),
            (right, bottom),
        ]
    return [
        (left, top),
        (right, top),
        (left + width * 0.5, bottom),
    ]


def triangle_overlay_bounds(coord: Coord, cell_size: int, header_height: int) -> tuple[float, float, float, float]:
    left, top, right, bottom = triangle_bounds(coord, cell_size, header_height)
    height = bottom - top
    center_x = (left + right) / 2
    center_y = top + (height * 2 / 3 if triangle_points_up(coord) else height / 3)
    radius = height * 0.28
    return center_x - radius, center_y - radius, center_x + radius, center_y + radius


def cell_bounds(warehouse: WarehouseMap, coord: Coord, cell_size: int, header_height: int) -> tuple[float, float, float, float]:
    if warehouse.layout_type == "hexagon":
        return hex_bounds(coord, cell_size, header_height)
    if warehouse.layout_type == "triangle":
        return triangle_overlay_bounds(coord, cell_size, header_height)
    return square_bounds(coord, cell_size, header_height)


def render_dimensions(warehouse: WarehouseMap, cell_size: int) -> tuple[int, int, int]:
    if warehouse.layout_type == "hexagon":
        metrics = hex_metrics(cell_size)
        board_width = (
            metrics["padding"] * 2
            + (warehouse.width - 1) * metrics["step_x"]
            + metrics["hex_width"]
            + metrics["row_offset"]
        )
        board_height = (
            metrics["padding"] * 2
            + (warehouse.height - 1) * metrics["step_y"]
            + metrics["hex_height"]
        )
        header_height = max(36, cell_size)
        return int(math.ceil(board_width)), int(math.ceil(board_height)), header_height
    if warehouse.layout_type == "triangle":
        metrics = triangle_metrics(cell_size)
        board_width = metrics["padding"] * 2 + metrics["triangle_side"] + (warehouse.width - 1) * metrics["step_x"]
        board_height = metrics["padding"] * 2 + warehouse.height * metrics["triangle_height"]
        header_height = max(36, cell_size)
        return int(math.ceil(board_width)), int(math.ceil(board_height)), header_height

    board_width = warehouse.width * cell_size
    board_height = warehouse.height * cell_size
    header_height = max(36, cell_size)
    return board_width, board_height, header_height


def fitted_cell_size(
    warehouse: WarehouseMap,
    requested_cell_size: int,
    max_width: int = MAX_RENDER_WIDTH,
    max_height: int = MAX_RENDER_HEIGHT,
) -> int:
    cell_size = max(1, requested_cell_size)
    while cell_size > 1:
        board_width, board_height, header_height = render_dimensions(warehouse, cell_size)
        if board_width <= max_width and board_height + header_height <= max_height:
            return cell_size
        cell_size -= 1
    return 1


def draw_square_cell(
    draw: ImageDraw.ImageDraw,
    cell: str,
    coord: Coord,
    cell_size: int,
    header_height: int,
    grid_color: tuple[int, int, int],
    station_outline: tuple[int, int, int],
) -> None:
    left, top, right, bottom = square_bounds(coord, cell_size, header_height)

    if cell == "#":
        draw.rectangle((left, top, right, bottom), fill=(0, 0, 0), outline=(35, 35, 35), width=1)
    elif cell == "S":
        draw.rectangle((left, top, right, bottom), fill=(255, 255, 255), outline=grid_color, width=1)
        inset = max(1, int(round(cell_size / 10)))
        draw.rectangle(
            (left + inset, top + inset, right - inset, bottom - inset),
            fill=(255, 255, 255),
            outline=station_outline,
            width=scaled_stroke_width(cell_size, 12, maximum=3),
        )
    else:
        draw.rectangle((left, top, right, bottom), fill=(255, 255, 255), outline=grid_color, width=1)


def draw_hex_cell(
    draw: ImageDraw.ImageDraw,
    cell: str,
    coord: Coord,
    cell_size: int,
    header_height: int,
    grid_color: tuple[int, int, int],
    station_outline: tuple[int, int, int],
) -> None:
    polygon = hex_polygon(coord, cell_size, header_height)

    if cell == "#":
        draw.polygon(polygon, fill=(0, 0, 0))
        draw_polygon_outline(draw, polygon, (35, 35, 35), width=1)
    elif cell == "S":
        draw.polygon(polygon, fill=(255, 255, 255))
        draw_polygon_outline(draw, polygon, grid_color, width=1)
        draw_polygon_outline(
            draw,
            scale_polygon(polygon, 0.82),
            station_outline,
            width=scaled_stroke_width(cell_size, 12, maximum=3),
        )
    else:
        draw.polygon(polygon, fill=(255, 255, 255))
        draw_polygon_outline(draw, polygon, grid_color, width=1)


def draw_triangle_cell(
    draw: ImageDraw.ImageDraw,
    cell: str,
    coord: Coord,
    cell_size: int,
    header_height: int,
    grid_color: tuple[int, int, int],
    station_outline: tuple[int, int, int],
) -> None:
    polygon = triangle_polygon(coord, cell_size, header_height)

    if cell == "#":
        draw.polygon(polygon, fill=(0, 0, 0))
        draw_polygon_outline(draw, polygon, (35, 35, 35), width=1)
    elif cell == "S":
        draw.polygon(polygon, fill=(255, 255, 255))
        draw_polygon_outline(draw, polygon, grid_color, width=1)
        draw_polygon_outline(
            draw,
            scale_polygon(polygon, 0.8),
            station_outline,
            width=scaled_stroke_width(cell_size, 12, maximum=3),
        )
    else:
        draw.polygon(polygon, fill=(255, 255, 255))
        draw_polygon_outline(draw, polygon, grid_color, width=1)


def draw_cell(
    draw: ImageDraw.ImageDraw,
    warehouse: WarehouseMap,
    coord: Coord,
    cell_size: int,
    header_height: int,
    grid_color: tuple[int, int, int],
    station_outline: tuple[int, int, int],
) -> None:
    cell = warehouse.rows[coord[0]][coord[1]]
    if warehouse.layout_type == "hexagon":
        draw_hex_cell(draw, cell, coord, cell_size, header_height, grid_color, station_outline)
        return
    if warehouse.layout_type == "triangle":
        draw_triangle_cell(draw, cell, coord, cell_size, header_height, grid_color, station_outline)
        return
    draw_square_cell(draw, cell, coord, cell_size, header_height, grid_color, station_outline)


def render_frames(
    warehouse: WarehouseMap,
    plans: list[AgentPlan],
    output_path: Path | None,
    cell_size: int,
    frame_duration_ms: int,
    progress: bool = False,
    debug_frames_dir: Path | None = None,
) -> int:
    if output_path is None and debug_frames_dir is None:
        raise ValueError("render_frames requires an output GIF path or a debug frames directory.")

    cell_size = fitted_cell_size(warehouse, cell_size)
    font = ImageFont.load_default()
    max_time = max(len(plan.path) for plan in plans) - 1 if plans else 0
    total_frames = max_time + 1
    package_pickups, package_release_times, package_deadlines, tasks_by_coord = build_task_maps(warehouse, plans)
    frame_number_width = max(4, len(str(total_frames - 1)))
    cumulative_collisions = 0

    if debug_frames_dir is not None:
        clear_debug_frames(debug_frames_dir)

    frames = [] if output_path is not None else None
    board_width, board_height, header_height = render_dimensions(warehouse, cell_size)
    total_height = board_height + header_height
    station_outline = (32, 160, 64)
    grid_color = (215, 215, 215)
    header_bg = (245, 245, 245)
    header_border = (200, 200, 200)

    for time in range(total_frames):
        if progress:
            print(f"\r[gif] Rendered frames: {time + 1}/{total_frames}", end="", flush=True)

        image = Image.new("RGB", (board_width, total_height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        agent_positions = frame_agent_positions(plans, time)
        collision_info = frame_collision_info(plans, time)
        collision_coords = collision_info.coords
        cumulative_collisions += collision_info.pair_count

        draw.rectangle((0, 0, board_width, header_height), fill=header_bg, outline=header_border, width=1)

        done_count = 0
        missed_count = 0
        total_tasks = len(package_pickups)

        for plan in plans:
            for task in plan.tasks:
                completion_time = plan.completion_times.get(task.task_id, 0)
                if time >= completion_time:
                    done_count += 1
                if task.deadline is not None and time > task.deadline and completion_time > task.deadline:
                    missed_count += 1

        draw_header_stats(
            draw,
            board_width,
            header_height,
            time,
            done_count,
            total_tasks,
            missed_count,
            cumulative_collisions,
            font,
        )

        for row in range(warehouse.height):
            for col in range(warehouse.width):
                coord = (row, col)
                draw_cell(draw, warehouse, coord, cell_size, header_height, grid_color, station_outline)

                for task in tasks_by_coord.get(coord, []):
                    if package_release_times[task.task_id] <= time < package_pickups[task.task_id]:
                        color = plans[task.agent_id].color
                        deadline = package_deadlines[task.task_id]
                        if deadline is not None and time > deadline:
                            color = (220, 20, 20)
                        draw_cross(draw, cell_bounds(warehouse, coord, cell_size, header_height), color)

        for plan in plans:
            position = agent_positions[plan.agent_id]
            label = carried_task_id(plan, time)
            draw_agent(
                draw,
                cell_bounds(warehouse, position, cell_size, header_height),
                plan.color,
                label,
                font,
            )

        for coord in collision_coords:
            draw_cross(draw, cell_bounds(warehouse, coord, cell_size, header_height), (220, 20, 20))

        if debug_frames_dir is not None:
            frame_path = debug_frames_dir / f"frame_{time:0{frame_number_width}d}.png"
            image.save(frame_path)

        if frames is not None:
            frames.append(image)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=frame_duration_ms,
            loop=0,
            optimize=False,
        )

    if progress:
        print()

    return max_time
