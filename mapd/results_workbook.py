from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from mapd.models import AgentPlan


TASKS_HEADERS = [
    "scenario",
    "layout_type",
    "layout",
    "strategy",
    "algorithm",
    "assignment_type",
    "agent_id",
    "start_station",
    "task_id",
    "location",
    "release_t",
    "deadline",
    "lateness",
    "path_length",
    "makespan",
]

SUMMARY_HEADERS = [
    "scenario",
    "layout_type",
    "layout",
    "strategy",
    "algorithm",
    "assignment_type",
    "agent_id",
    "start_station",
    "path_length",
    "num_tasks",
]

COMPARISON_HEADERS = [
    "scenario",
    "layout_type",
    "layout",
    "strategy",
    "algorithm",
    "assignment_type",
    "makespan",
    "missed_deadlines",
    "total_tasks",
]


def assignment_type_label(mode: str, station_mode: str) -> str:
    return f"{mode}/{station_mode}"


def build_tasks_rows(
    scenario_name: str,
    layout_type: str,
    layout_id: int,
    strategy: str,
    algorithm: str,
    assignment_type: str,
    makespan: int,
    plans: list[AgentPlan],
) -> list[list[object]]:
    rows: list[list[object]] = [TASKS_HEADERS]
    for plan in plans:
        path_length = len(plan.path) - 1
        for task in plan.tasks:
            completion = plan.completion_times.get(task.task_id)
            lateness = 0
            if task.deadline is not None and completion is not None:
                lateness = max(0, completion - task.deadline)

            rows.append(
                [
                    scenario_name,
                    layout_type,
                    layout_id,
                    strategy,
                    algorithm,
                    assignment_type,
                    plan.agent_id,
                    plan.home_index,
                    task.task_id,
                    task.location_index,
                    task.release_time,
                    task.deadline,
                    lateness,
                    path_length,
                    makespan,
                ]
            )
    return rows


def build_summary_rows(
    scenario_name: str,
    layout_type: str,
    layout_id: int,
    strategy: str,
    algorithm: str,
    assignment_type: str,
    plans: list[AgentPlan],
) -> list[list[object]]:
    rows: list[list[object]] = [SUMMARY_HEADERS]
    for plan in plans:
        rows.append(
            [
                scenario_name,
                layout_type,
                layout_id,
                strategy,
                algorithm,
                assignment_type,
                plan.agent_id,
                plan.home_index,
                len(plan.path) - 1,
                len(plan.tasks),
            ]
        )
    return rows


def build_comparison_row(
    scenario_name: str,
    layout_type: str,
    layout_id: int,
    strategy: str,
    algorithm: str,
    assignment_type: str,
    makespan: int,
    plans: list[AgentPlan],
) -> list[object]:
    missed_deadlines = sum(len(plan.missed_deadlines) for plan in plans)
    total_tasks = sum(len(plan.tasks) for plan in plans)
    return [scenario_name, layout_type, layout_id, strategy, algorithm, assignment_type, makespan, missed_deadlines, total_tasks]


def column_name(index: int) -> str:
    name = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def workbook_cell(row_idx: int, col_idx: int, value: object) -> str:
    reference = f"{column_name(col_idx)}{row_idx}"
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"><v>{value}</v></c>'

    text = escape(str(value))
    return (
        f'<c r="{reference}" t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is>'
        f"</c>"
    )


def sheet_xml(rows: list[list[object]]) -> str:
    last_column = max((len(row) for row in rows), default=1)
    last_row = max(len(rows), 1)
    dimension = f"A1:{column_name(last_column)}{last_row}"

    row_xml = []
    for row_idx, row in enumerate(rows, start=1):
        cells = "".join(
            workbook_cell(row_idx, col_idx, value)
            for col_idx, value in enumerate(row, start=1)
            if value is not None
        )
        row_xml.append(f'<row r="{row_idx}">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        "<sheetData>"
        + "".join(row_xml)
        + "</sheetData>"
        "</worksheet>"
    )


def content_types_xml(sheet_count: int) -> str:
    worksheet_overrides = "".join(
        (
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + worksheet_overrides
        + "</Types>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
        'Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        (
            f'<sheet name="{escape(name)}" sheetId="{index}" '
            f'r:id="rId{index}"/>'
        )
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + sheets
        + "</sheets>"
        "</workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    relationships = "".join(
        (
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + relationships
        + "</Relationships>"
    )


def app_xml(sheet_names: list[str]) -> str:
    titles = "".join(f"<vt:lpstr>{escape(name)}</vt:lpstr>" for name in sheet_names)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>MAPD Simulator</Application>"
        "<HeadingPairs>"
        '<vt:vector size="2" baseType="variant">'
        "<vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>"
        f"<vt:variant><vt:i4>{len(sheet_names)}</vt:i4></vt:variant>"
        "</vt:vector>"
        "</HeadingPairs>"
        "<TitlesOfParts>"
        f'<vt:vector size="{len(sheet_names)}" baseType="lpstr">{titles}</vt:vector>'
        "</TitlesOfParts>"
        "</Properties>"
    )


def core_xml() -> str:
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>MAPD Results</dc:title>"
        "<dc:creator>Codex</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def write_xlsx_workbook(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = [name for name, _ in sheets]
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
        workbook.writestr("_rels/.rels", root_rels_xml())
        workbook.writestr("xl/workbook.xml", workbook_xml(sheet_names))
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheets)))
        workbook.writestr("docProps/app.xml", app_xml(sheet_names))
        workbook.writestr("docProps/core.xml", core_xml())
        for index, (_, rows) in enumerate(sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))
