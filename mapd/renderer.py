from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mapd.models import AgentPlan
from mapd.warehouse import WarehouseMap


def draw_cross(draw: ImageDraw.ImageDraw, left: int, top: int, cell_size: int, color: tuple[int, int, int]) -> None:
    padding = max(6, cell_size // 5)
    right = left + cell_size
    bottom = top + cell_size
    draw.line((left + padding, top + padding, right - padding, bottom - padding), fill=color, width=3)
    draw.line((left + padding, bottom - padding, right - padding, top + padding), fill=color, width=3)


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
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


def draw_station_label(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    cell_size: int,
    label: str,
    font,
) -> None:
    padding = max(2, cell_size // 10)
    draw_centered_text(
        draw,
        (left + padding, top + padding, left + cell_size - padding, top + cell_size - padding),
        label,
        (20, 110, 40),
        font,
    )


def draw_agent(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    cell_size: int,
    color: tuple[int, int, int],
    label: str,
    font,
) -> None:
    padding = max(6, cell_size // 6)
    circle_bounds = (left + padding, top + padding, left + cell_size - padding, top + cell_size - padding)
    draw.ellipse(circle_bounds, fill=color, outline=(20, 20, 20), width=2)
    draw_centered_text(draw, (left, top, left + cell_size, top + cell_size), label, (255, 255, 255), font)


def progress_points(total_frames: int) -> set[int]:
    if total_frames <= 1:
        return {0}

    points = {0, total_frames - 1}
    for percent in range(10, 100, 10):
        points.add(round((total_frames - 1) * percent / 100))
    return points


def build_task_maps(plans: list[AgentPlan]) -> tuple[dict[int, int], dict[int, int], dict[int, list]]:
    package_pickups = {}
    package_release_times = {}
    tasks_by_location = defaultdict(list)

    for plan in plans:
        for task in plan.tasks:
            package_pickups[task.task_id] = plan.pickup_times[task.task_id]
            package_release_times[task.task_id] = task.release_time
            tasks_by_location[task.location_index].append(task)

    return package_pickups, package_release_times, tasks_by_location


def render_frames(
    warehouse: WarehouseMap,
    plans: list[AgentPlan],
    output_path: Path,
    cell_size: int,
    frame_duration_ms: int,
    progress: bool = False,
) -> int:
    font = ImageFont.load_default()
    station_labels = {plan.home: str(plan.agent_id) for plan in plans}
    max_time = max(len(plan.path) for plan in plans) - 1 if plans else 0
    total_frames = max_time + 1
    progress_marks = progress_points(total_frames)
    package_pickups, package_release_times, tasks_by_location = build_task_maps(plans)

    frames = []
    image_width = warehouse.width * cell_size
    image_height = warehouse.height * cell_size
    station_outline = (32, 160, 64)
    grid_color = (215, 215, 215)

    for time in range(total_frames):
        if progress and time in progress_marks:
            percent = int(round((time / max(total_frames - 1, 1)) * 100))
            print(f"  [render] frame {time + 1}/{total_frames} ({percent}%)")

        image = Image.new("RGB", (image_width, image_height), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        for row in range(warehouse.height):
            for col in range(warehouse.width):
                coord = (row, col)
                left = col * cell_size
                top = row * cell_size
                right = left + cell_size
                bottom = top + cell_size
                cell = warehouse.rows[row][col]

                if cell == "#":
                    draw.rectangle((left, top, right, bottom), fill=(0, 0, 0), outline=(35, 35, 35), width=1)
                elif cell == "S":
                    draw.rectangle((left, top, right, bottom), fill=(255, 255, 255), outline=grid_color, width=1)
                    inset = max(3, cell_size // 10)
                    draw.rectangle(
                        (left + inset, top + inset, right - inset, bottom - inset),
                        fill=(255, 255, 255),
                        outline=station_outline,
                        width=3,
                    )
                    label = station_labels.get(coord)
                    if label is not None:
                        draw_station_label(draw, left, top, cell_size, label, font)
                else:
                    draw.rectangle((left, top, right, bottom), fill=(255, 255, 255), outline=grid_color, width=1)

                for task in tasks_by_location.get(warehouse.coord_to_index(coord), []):
                    if package_release_times[task.task_id] <= time < package_pickups[task.task_id]:
                        draw_cross(draw, left, top, cell_size, plans[task.agent_id].color)

        for plan in plans:
            position = plan.path[time] if time < len(plan.path) else plan.path[-1]
            left = position[1] * cell_size
            top = position[0] * cell_size
            draw_agent(draw, left, top, cell_size, plan.color, str(plan.agent_id), font)

        frames.append(image)

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
        print(f"  [render] saved {total_frames} frames")

    return max_time
