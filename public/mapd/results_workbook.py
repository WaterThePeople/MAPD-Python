from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from mapd.report_metrics import (
    algorithm_label,
    distance_step_sum,
    failure_model_label,
    layout_size_label,
    missed_deadline_count,
    missed_deadline_time_sum,
    mode_label,
    status_label,
    strategy_label,
    throughput,
    wait_step_count,
)
from mapd.models import Coord, ScenarioMetadata, VariantExecutionResult

COMPARISON_HEADERS = [
    "scenario",
    "seed",
    "layout",
    "size",
    "type",
    "agents",
    "tasks",
    "influx",
    "spatial distribution",
    "deadline slack",
    "mode",
    "station",
    "strategy",
    "algorithm",
    "failure model",
    "failure probability",
    "failure duration min",
    "failure duration max",
    "failure seed",
    "status",
    "throughput",
    "makespan",
    "missed deadlines",
    "missed deadline time",
    "collisions",
    "failures",
    "failure duration",
    "number of waits",
    "replans",
    "sum of distances",
    "simulation time",
]

GEOMETRY_SHEETS = [
    ("Square", "square"),
    ("Hexagon", "hexagon"),
    ("Triangle", "triangle"),
]

INFLUX_SHEETS = [
    ("Random", "random"),
    ("Gaussian", "gaussian"),
    ("Burst", "burst"),
]

SPATIAL_SHEETS = [
    ("Uniform", "uniform"),
    ("Hotspot", "hotspot"),
    ("Wave", "wave"),
]

STATUS_SHEETS = [
    ("Solved", "solved"),
    ("Unsolved", "unsolved"),
]

SORT_PRIORITY = [
    ("missed deadlines", False),
    ("missed deadline time", False),
    ("collisions", False),
    ("makespan", False),
    ("throughput", True),
    ("simulation time", False),
    ("replans", False),
    ("number of waits", False),
    ("sum of distances", False),
    ("failures", False),
    ("failure duration", False),
]

def build_comparison_row(
    scenario_name: str,
    layout_id: int,
    layout_size: str | None,
    layout_type: str,
    agent_count: int,
    task_count: int,
    metadata: ScenarioMetadata,
    mode: str,
    station_mode: str,
    strategy: str,
    algorithm: str,
    failure_model: str,
    result: VariantExecutionResult,
    station_cells: set[Coord],
) -> list[object]:
    plans = result.plans
    return [
        scenario_name,
        metadata.seed,
        layout_id,
        layout_size_label(layout_size),
        layout_type,
        agent_count,
        task_count,
        metadata.influx,
        metadata.spatial_distribution,
        metadata.deadline_slack,
        mode_label(mode),
        mode_label(station_mode),
        strategy_label(strategy),
        algorithm_label(algorithm),
        failure_model_label(failure_model),
        metadata.failure_probability,
        metadata.failure_duration_min,
        metadata.failure_duration_max,
        metadata.failure_seed,
        status_label(result.status),
        throughput(task_count, result.makespan),
        result.makespan,
        missed_deadline_count(plans),
        missed_deadline_time_sum(plans),
        result.collisions,
        result.failure_count,
        result.failure_delay_steps,
        wait_step_count(plans, station_cells),
        result.replans,
        distance_step_sum(plans),
        round(result.simulation_time_seconds, 2),
    ]


def normalize_text(value: object) -> str:
    return str(value).strip().lower()


def metric_value(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def row_cell_value(row: list[object], column_index: int) -> object:
    if column_index >= len(row):
        return None
    return row[column_index]


def filter_rows_by_column_value(
    rows: list[list[object]],
    column_name: str,
    accepted_value: str,
) -> list[list[object]]:
    if not rows:
        return rows

    header = rows[0]
    if column_name not in header:
        return [header]

    column_index = header.index(column_name)
    filtered_rows = [header]
    for row in rows[1:]:
        if column_index >= len(row):
            continue
        cell_value = row[column_index]
        if normalize_text(cell_value) == accepted_value:
            filtered_rows.append(row)
    return filtered_rows


def build_filtered_sheets(
    rows: list[list[object]],
    column_name: str,
    labels: list[tuple[str, str]],
) -> list[tuple[str, list[list[object]]]]:
    return [
        (sheet_name, filter_rows_by_column_value(rows, column_name, accepted_value))
        for sheet_name, accepted_value in labels
    ]


def build_suite_workbook_sheets(rows: list[list[object]]) -> list[tuple[str, list[list[object]]]]:
    sheets: list[tuple[str, list[list[object]]]] = [("Overall Comparison", rows)]
    sheets.extend(build_filtered_sheets(rows, "type", GEOMETRY_SHEETS))
    sheets.extend(build_filtered_sheets(rows, "influx", INFLUX_SHEETS))
    sheets.extend(build_filtered_sheets(rows, "spatial distribution", SPATIAL_SHEETS))
    sheets.extend(build_filtered_sheets(rows, "status", STATUS_SHEETS))
    return sheets


def column_name(index: int) -> str:
    name = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def row_text_lines(row: list[object]) -> int:
    max_lines = 1
    for value in row:
        if value is None:
            continue
        max_lines = max(max_lines, str(value).count("\n") + 1)
    return max_lines


def row_height(row: list[object], row_idx: int) -> float:
    base_height = 22.0 if row_idx == 1 else 20.0
    return base_height * row_text_lines(row)


def column_width(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value)
    longest_line = max((len(line) for line in text.splitlines()), default=0)
    return min(60.0, max(4.0, float(longest_line + 2)))


def column_widths(rows: list[list[object]]) -> list[float]:
    max_columns = max((len(row) for row in rows), default=0)
    widths = [4.0] * max_columns
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], column_width(value))
    return widths


def sort_rows_by_metrics(rows: list[list[object]]) -> list[list[object]]:
    if len(rows) <= 1:
        return rows

    header = rows[0]
    sort_columns = [
        (header.index(column_name), descending)
        for column_name, descending in SORT_PRIORITY
        if column_name in header
    ]
    if not sort_columns:
        return rows

    data_rows = list(enumerate(rows[1:]))

    def metric_sort_key(value: object, descending: bool) -> tuple[int, float]:
        numeric = metric_value(value)
        if numeric is None:
            return (1, 0.0)
        return (0, -numeric if descending else numeric)

    data_rows.sort(
        key=lambda item: (
            *(
                metric_sort_key(row_cell_value(item[1], column_index), descending)
                for column_index, descending in sort_columns
            ),
            item[0],
        )
    )
    return [header, *[row for _, row in data_rows]]


def workbook_cell(row_idx: int, col_idx: int, value: object, style_id: int) -> str:
    reference = f"{column_name(col_idx)}{row_idx}"
    if value is None:
        return ""
    style_attr = f' s="{style_id}"'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'

    text = escape(str(value))
    return (
        f'<c r="{reference}"{style_attr} t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is>'
        f"</c>"
    )


def columns_xml(rows: list[list[object]]) -> str:
    widths = column_widths(rows)
    if not widths:
        return ""

    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width:.2f}" bestFit="1" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    return f"<cols>{columns}</cols>"


def sheet_xml(rows: list[list[object]]) -> str:
    last_column = max((len(row) for row in rows), default=1)
    last_row = max(len(rows), 1)
    dimension = f"A1:{column_name(last_column)}{last_row}"

    row_xml = []
    for row_idx, row in enumerate(rows, start=1):
        style_id = 1 if row_idx == 1 else 0
        cells = "".join(
            workbook_cell(row_idx, col_idx, value, style_id)
            for col_idx, value in enumerate(row, start=1)
            if value is not None
        )
        row_xml.append(f'<row r="{row_idx}" ht="{row_height(row, row_idx):.2f}" customHeight="1">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="20"/>'
        + columns_xml(rows)
        + "<sheetData>"
        + "".join(row_xml)
        + "</sheetData>"
        + "</worksheet>"
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
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
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
    styles_relationship = (
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + relationships
        + styles_relationship
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


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        "</fonts>"
        '<fills count="2">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/>'
        "</xf>"
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/>'
        "</xf>"
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def parse_numeric_cell(value: str) -> object:
    try:
        numeric = float(value)
    except ValueError:
        return value
    if numeric.is_integer():
        return int(numeric)
    return numeric


def read_xlsx_sheet_rows(path: Path, sheet_index: int = 1) -> list[list[object]]:
    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    sheet_path = f"xl/worksheets/sheet{sheet_index}.xml"

    with ZipFile(path, "r") as workbook:
        sheet_xml_bytes = workbook.read(sheet_path)

    root = ET.fromstring(sheet_xml_bytes)
    rows: list[list[object]] = []

    for row_element in root.findall(".//main:sheetData/main:row", namespace):
        row_values: list[object] = []
        for cell in row_element.findall("main:c", namespace):
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                text = cell.findtext("main:is/main:t", default="", namespaces=namespace)
                row_values.append(text)
                continue

            value = cell.findtext("main:v", default="", namespaces=namespace)
            row_values.append(parse_numeric_cell(value))
        rows.append(row_values)

    return rows


def write_xlsx_workbook(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_sheets = [(name, sort_rows_by_metrics(rows)) for name, rows in sheets]
    sheet_names = [name for name, _ in normalized_sheets]
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml(len(normalized_sheets)))
        workbook.writestr("_rels/.rels", root_rels_xml())
        workbook.writestr("xl/workbook.xml", workbook_xml(sheet_names))
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(normalized_sheets)))
        workbook.writestr("docProps/app.xml", app_xml(sheet_names))
        workbook.writestr("docProps/core.xml", core_xml())
        workbook.writestr("xl/styles.xml", styles_xml())
        for index, (_, rows) in enumerate(normalized_sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(rows))
