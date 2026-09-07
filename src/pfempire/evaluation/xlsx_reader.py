from __future__ import annotations

import html
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"m": MAIN_NS, "r": REL_NS}


def read_xlsx_sheet(path: Path, sheet_name: str) -> list[dict[str, str]]:
    raw_rows = read_xlsx_rows(path, sheet_name)
    if not raw_rows:
        return []
    headers = raw_rows[0]
    return [
        {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(headers)
            if header
        }
        for row in raw_rows[1:]
    ]


def read_xlsx_rows(path: Path, sheet_name: str) -> list[list[str]]:
    with ZipFile(Path(path)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        shared_strings = _shared_strings(archive)
        sheets = workbook.find("m:sheets", NS)
        if sheets is None:
            raise ValueError("XLSX workbook contains no sheets")
        sheet = next((item for item in sheets if item.attrib.get("name") == sheet_name), None)
        if sheet is None:
            available = ", ".join(item.attrib.get("name", "") for item in sheets)
            raise ValueError(f"Unknown sheet {sheet_name!r}; available: {available}")
        relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
        target = targets[relationship_id]
        target_path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
        root = ET.fromstring(archive.read(target_path))
        rows: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", NS):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", NS):
                reference = cell.attrib.get("r", "A1")
                values[column_index(reference)] = _cell_text(cell, shared_strings)
            rows.append([values.get(index, "") for index in range(max(values, default=-1) + 1)])
        return rows


def column_index(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference.upper())
    if not match:
        raise ValueError(f"Invalid XLSX cell reference: {reference}")
    value = 0
    for character in match.group():
        value = value * 26 + ord(character) - 64
    return value - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        html.unescape("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))
        for item in root.findall("m:si", NS)
    ]


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find("m:is", NS)
        if inline is None:
            return ""
        return html.unescape("".join(node.text or "" for node in inline.iter(f"{{{MAIN_NS}}}t")))
    value = cell.find("m:v", NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return html.unescape(value.text)
