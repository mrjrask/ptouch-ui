"""Editable label documents, embedded data, and templates."""

import csv
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

DPI = 180
DOTS_PER_MM = DPI / 25.4
TAPES = {3.5: 24, 6: 32, 9: 52, 12: 76, 18: 120, 24: 128}
MIN_LENGTH, MAX_LENGTH = 5, 300
MAX_RECORDS = 250
MAX_FILE_BYTES = 20_000_000
KINDS = ("Text", "Image", "Rectangle", "Ellipse", "Line", "QR code", "Barcode")
BARCODES = ("Code 128", "Code 39", "EAN-13", "EAN-8", "UPC-A", "ITF")
FIELD = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def dots(mm):
    return round(mm * DOTS_PER_MM)


@dataclass(frozen=True)
class Element:
    kind: str
    x: int
    y: int
    width: int
    height: int
    value: str = ""
    rotation: int = 0
    font: str = "DejaVu Sans"
    font_size: int = 28
    bold: bool = False
    italic: bool = False
    align: str = "left"
    fit: bool = True
    stroke: int = 2
    filled: bool = False
    radius: int = 0
    threshold: int = 128
    dither: bool = False
    barcode: str = "Code 128"
    caption: bool = True
    inverted: bool = False

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("Unknown object type.")
        for field, low, high in (
            ("x", -dots(MAX_LENGTH), dots(MAX_LENGTH)),
            ("y", -128, 128),
            ("width", 1, dots(MAX_LENGTH)),
            ("height", 1, 128),
            ("font_size", 1, 300),
            ("stroke", 1, 128),
            ("threshold", 1, 255),
            ("radius", 0, 64),
        ):
            value = getattr(self, field)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid object {field.replace('_', ' ')}.")
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise ValueError("Rotation must be 0°, 90°, 180°, or 270°.")
        if self.align not in ("left", "center", "right") or self.barcode not in BARCODES:
            raise ValueError("Invalid text alignment or barcode format.")
        if not isinstance(self.value, str) or not isinstance(self.font, str):
            raise TypeError("Invalid object content or font.")
        if self.kind != "Image" and len(self.value) > 2000:
            raise ValueError("Object content is limited to 2,000 characters.")
        if any(
            type(getattr(self, field)) is not bool
            for field in ("bold", "italic", "fit", "filled", "dither", "caption", "inverted")
        ):
            raise ValueError("Invalid object style.")


@dataclass(frozen=True)
class Label:
    tape: float = 12
    length: float = 70
    elements: tuple[Element, ...] = ()
    rows: tuple[dict[str, str], ...] = ()

    def __post_init__(self):
        if type(self.tape) not in (int, float) or self.tape not in TAPES:
            raise ValueError("Unsupported tape width.")
        if type(self.length) not in (int, float) or not MIN_LENGTH <= self.length <= MAX_LENGTH:
            raise ValueError(f"Label length must be {MIN_LENGTH}–{MAX_LENGTH} mm.")
        if len(self.rows) > MAX_RECORDS:
            raise ValueError(f"A document can contain up to {MAX_RECORDS} records.")
        for row in self.rows:
            if not isinstance(row, dict) or "date" in row:
                raise ValueError("Invalid data record. Date is a reserved field.")
            if not all(isinstance(key, str) and isinstance(value, str) for key, value in row.items()):
                raise ValueError("Data fields and values must be text.")

    @property
    def size(self):
        return dots(self.length), TAPES[self.tape]

    def fields(self, index=0, today=None):
        row = self.rows[index] if self.rows else {}
        return {**row, "date": (today or date.today()).isoformat()}


def substitute(value, fields):
    missing = []

    def resolve(match):
        key = match[1]
        if key not in fields:
            missing.append(key)
        return fields.get(key, match[0])

    resolved = FIELD.sub(resolve, value)
    return resolved, list(dict.fromkeys(missing))


def load_label(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("The label document exceeds 20 MB.")
    root = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(root, dict) or type(root.get("version")) is not int or root["version"] != 1:
        raise ValueError("Unsupported label document format.")
    elements = root.get("elements")
    rows = root.get("rows", [])
    if not isinstance(elements, list) or not isinstance(rows, list):
        raise ValueError("The label document must store objects and records as lists.")
    try:
        return Label(
            tape=root["tape"],
            length=root["length"],
            elements=tuple(Element(**item) for item in elements),
            rows=tuple(rows),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("The label document is incomplete or malformed.") from error


def save_label(path, label):
    path = Path(os.path.realpath(path))
    payload = json.dumps({"version": 1, **asdict(label)}, indent=2, ensure_ascii=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("The label document exceeds 20 MB.")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISREG(mode):
        raise ValueError("Choose a regular file for the label document.")
    with TemporaryDirectory(prefix=".ptouch-ui-", dir=path.parent) as directory:
        temporary_path = Path(directory) / "label"
        # The OS applies the current umask to new files without changing it for other threads.
        with temporary_path.open("x", encoding="utf-8") as temporary:
            temporary.write(payload)
        if mode is not None:
            temporary_path.chmod(stat.S_IMODE(mode))
        temporary_path.replace(path)


def read_csv(path):
    try:
        return parse_csv(path)
    except UnicodeDecodeError as error:
        raise ValueError("Save the CSV with UTF-8 encoding and import it again.") from error


def parse_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        headers = [name.strip() for name in next(reader, [])]
        if not headers or any(not name or "{" in name or "}" in name for name in headers):
            raise ValueError("The CSV needs a header with non-empty field names without braces.")
        if len(set(headers)) != len(headers) or "date" in headers:
            raise ValueError("CSV field names must be unique. Date is a reserved field.")
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(headers):
                raise ValueError(f"CSV line {reader.line_num} has the wrong number of fields.")
            if len(rows) == MAX_RECORDS:
                raise ValueError(f"Import up to {MAX_RECORDS} records at a time.")
            rows.append(dict(zip(headers, row)))
    if not rows:
        raise ValueError("The CSV has no data rows.")
    return tuple(rows)


def numbered_rows(start, count, digits):
    if not 0 <= start <= 99999999 or not 1 <= count <= MAX_RECORDS or not 1 <= digits <= 8:
        raise ValueError(f"Use a nonnegative start, 1–{MAX_RECORDS} labels, and 1–8 digits.")
    return tuple({"number": str(number).zfill(digits)} for number in range(start, start + count))


def content_length(label):
    """Return the shortest valid length in mm that holds every object with matching end margins."""
    if not label.elements:
        return label.length
    left = max(0, min(element.x for element in label.elements))
    right = max(element.x + element.width for element in label.elements)
    needed = math.ceil((right + left) / DOTS_PER_MM * 10) / 10
    while dots(needed) < right + left:
        needed = round(needed + 0.1, 1)
    return max(MIN_LENGTH, min(MAX_LENGTH, needed))


def fit_position(element, label):
    width, height = label.size
    return replace(
        element,
        x=max(0, min(element.x, width - element.width)),
        y=max(0, min(element.y, height - element.height)),
    )


TEMPLATES = ("Blank label", "Equipment tag", "Cable ID", "Storage label", "Numbered inventory")


def template(name):
    if name == "Equipment tag":
        return Label(
            24,
            80,
            (
                Element("Rectangle", 9, 9, 549, 110, stroke=2),
                Element("Text", 24, 18, 330, 25, "EQUIPMENT  /  STUDIO A", font_size=18, bold=True),
                Element("Text", 23, 46, 405, 55, "ASSET 024", font_size=61, bold=True),
                Element("QR code", 452, 18, 90, 90, "ASSET-024"),
            ),
        )
    if name == "Cable ID":
        return Label(9, 45, (Element("Text", 10, 4, 298, 44, "NETWORK 01", bold=True, align="center"),))
    if name == "Storage label":
        return Label(
            18,
            60,
            (
                Element("Text", 12, 7, 401, 67, "HARDWARE", font_size=56, bold=True, align="center"),
                Element("Line", 22, 80, 381, 2),
                Element("Text", 12, 86, 401, 25, "BIN 04  /  SHELF B", font_size=17, align="center"),
            ),
        )
    if name == "Numbered inventory":
        return Label(
            24,
            78,
            (
                Element("Text", 12, 8, 528, 32, "INVENTORY  {{number}}", bold=True, align="center"),
                Element("Barcode", 14, 45, 524, 71, "INV-{{number}}"),
            ),
            numbered_rows(1, 10, 4),
        )
    return Label()
