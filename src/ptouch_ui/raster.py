"""Render the editor preview and the printer input from the same 180 dpi raster."""

import subprocess
from base64 import b64decode, b64encode
from functools import lru_cache
from io import BytesIO

import barcode
import qrcode
from barcode.errors import BarcodeError
from PIL import Image, ImageDraw, ImageFont, ImageOps
from qrcode.exceptions import DataOverflowError

from .document import DPI, substitute

WHITE = 255
BLACK = 0
THRESHOLD = [BLACK] * 128 + [WHITE] * 128
# Formats with a trailing check digit: python-barcode name and full length.
CHECK_DIGIT_CODES = {"EAN-13": ("ean13", 13), "EAN-8": ("ean8", 8), "UPC-A": ("upca", 12)}


def list_fonts():
    try:
        output = subprocess.check_output(
            ["fc-list", "--format", "%{family}\n"],
            text=True,
            timeout=5,
        )
        return sorted(
            {family.strip() for line in output.splitlines() for family in line.split(",") if family.strip()},
            key=str.casefold,
        )
    except (OSError, subprocess.SubprocessError):
        return ["DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif"]


@lru_cache(maxsize=128)
def font_path(family, bold, italic):
    quoted_family = "".join(f"\\{char}" if char in "\\-,:=" else char for char in family)
    weight = "bold" if bold else "regular"
    slant = "italic" if italic else "roman"
    try:
        return subprocess.check_output(
            ["fc-match", "--format", "%{file}", f"{quoted_family}:weight={weight}:slant={slant}"],
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "DejaVuSans.ttf"


def text_tile(size, element, text):
    tile = Image.new("L", size, BLACK)
    if not text.strip():
        return tile, None
    draw = ImageDraw.Draw(tile)
    path = font_path(element.font, element.bold, element.italic)
    spacing = 2

    def measure(pixels):
        font = ImageFont.truetype(path, pixels)
        bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align=element.align)
        return font, bounds

    def fits(bounds):
        x0, y0, x1, y1 = bounds
        return x1 - x0 <= size[0] and y1 - y0 <= size[1]

    font, bounds = measure(element.font_size)
    if element.fit and not fits(bounds):
        low, high = 1, element.font_size - 1
        font, bounds = measure(low)
        while low <= high:
            middle = (low + high) // 2
            candidate, candidate_bounds = measure(middle)
            if fits(candidate_bounds):
                font, bounds = candidate, candidate_bounds
                low = middle + 1
            else:
                high = middle - 1

    x0, y0, x1, y1 = bounds
    gap = size[0] - (x1 - x0)
    x = {"left": 0, "center": gap // 2, "right": gap}[element.align]
    y = (size[1] - (y1 - y0)) // 2
    draw.multiline_text((x - x0, y - y0), text, font=font, fill=WHITE, spacing=spacing, align=element.align)
    return tile.point(THRESHOLD), None if fits(bounds) else "Text does not fit its box."


def import_image(path):
    with Image.open(path) as image:
        if image.width * image.height > 24_000_000:
            raise ValueError("Import an image with at most 24 million pixels.")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image.thumbnail((1600, 640), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    return b64encode(buffer.getvalue()).decode("ascii"), image.size


@lru_cache(maxsize=12)
def decode_image(value):
    with Image.open(BytesIO(b64decode(value, validate=True)), formats=["PNG"]) as image:
        if image.width > 1600 or image.height > 640:
            raise ValueError("The embedded image is too large.")
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("L")


def picture_tile(size, element):
    source = decode_image(element.value)
    scale = min(size[0] / source.width, size[1] / source.height)
    dimensions = max(1, round(source.width * scale)), max(1, round(source.height * scale))
    image = source.resize(dimensions, Image.Resampling.LANCZOS)
    if element.dither:
        image = image.convert("1").convert("L")
    else:
        image = image.point([BLACK if value < element.threshold else WHITE for value in range(256)])
    tile = Image.new("L", size, WHITE)
    tile.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return tile


def qr_tile(size, value):
    if not value:
        raise ValueError("Enter a value for the QR code.")
    code = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(value)
    matrix = code.get_matrix()
    scale = min(size) // len(matrix)
    if scale < 2:
        raise ValueError("QR code needs a larger box or shorter content (at least two dots per module).")
    modules = Image.new("L", (len(matrix), len(matrix)))
    modules.putdata([BLACK if cell else WHITE for row in matrix for cell in row])
    code_image = modules.resize((modules.width * scale, modules.height * scale), Image.Resampling.NEAREST)
    tile = Image.new("L", size, WHITE)
    tile.paste(code_image, ((tile.width - code_image.width) // 2, (tile.height - code_image.height) // 2))
    return tile


def barcode_tile(size, element, value):
    if not value:
        raise ValueError("Enter a value for the barcode.")
    if element.barcode == "Code 128":
        if not all(32 <= ord(char) <= 126 for char in value):
            raise ValueError("Code 128 needs printable ASCII text.")
        code = barcode.get("code128", value)
    elif element.barcode == "Code 39":
        if not value.isascii() or not all(
            char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%" for char in value.upper()
        ):
            raise ValueError("Code 39 needs letters A–Z, numbers, or - . $ / + % and spaces.")
        code = barcode.get_barcode_class("code39")(value.upper(), add_checksum=False)
    elif element.barcode == "ITF":
        if not value.isascii() or not value.isdigit() or len(value) % 2:
            raise ValueError("ITF needs an even number of digits.")
        code = barcode.get_barcode_class("itf")(value)
    else:
        name, digits = CHECK_DIGIT_CODES[element.barcode]
        if len(value) not in (digits - 1, digits) or not value.isascii() or not value.isdigit():
            raise ValueError(
                f"{element.barcode} needs {digits - 1} digits or {digits} digits with a valid check digit."
            )
        code = barcode.get_barcode_class(name)(value[: digits - 1])
        if len(value) == digits and code.get_fullcode() != value:
            raise ValueError(f"The {element.barcode} check digit is incorrect.")

    bars = code.build()[0]
    quiet = 11
    scale = size[0] // (len(bars) + 2 * quiet)
    if scale < 2:
        raise ValueError("Barcode needs a wider box or shorter content (at least two dots per module).")

    tile = Image.new("L", size, WHITE)
    draw = ImageDraw.Draw(tile)
    caption_height = 0
    if element.caption:
        caption_font = ImageFont.truetype(font_path("DejaVu Sans", False, False), 11)
        caption = code.get_fullcode()
        left, top, right, bottom = draw.textbbox((0, 0), caption, font=caption_font)
        caption_height = bottom - top + 3
        draw.text(
            ((size[0] - right + left) // 2 - left, size[1] - bottom), caption, font=caption_font, fill=BLACK
        )
    if size[1] - caption_height < 12:
        raise ValueError("Barcode needs a taller box.")
    origin = (size[0] - len(bars) * scale) // 2
    for index, bar in enumerate(bars):
        if bar == "1":
            x = origin + index * scale
            draw.rectangle((x, 0, x + scale - 1, size[1] - caption_height - 1), fill=BLACK)
    return tile.point(THRESHOLD)


def shape_mask(size, element):
    tile = Image.new("L", size, BLACK)
    draw = ImageDraw.Draw(tile)
    bounds = (0, 0, size[0] - 1, size[1] - 1)
    stroke = min(element.stroke, *size)
    if element.kind == "Line":
        draw.rectangle(bounds, fill=WHITE)
    else:
        shape = draw.ellipse if element.kind == "Ellipse" else draw.rectangle
        shape(bounds, fill=WHITE if element.filled else None, outline=WHITE, width=stroke)
    return tile


def render(label, index=0, today=None):
    tape = Image.new("L", label.size, WHITE)
    fields = label.fields(index, today)
    issues = []
    for ordinal, element in enumerate(label.elements, start=1):
        name = f"{element.kind} {ordinal}"
        box_width, box_height = element.width, element.height
        if (
            element.x < 0
            or element.y < 0
            or element.x + box_width > tape.width
            or element.y + box_height > tape.height
        ):
            issues.append(f"{name} extends outside the printable area.")
        size = (box_height, box_width) if element.rotation in (90, 270) else (box_width, box_height)
        mask = None
        try:
            if element.kind in ("Text", "QR code", "Barcode"):
                value, missing = substitute(element.value, fields)
                if missing:
                    issues.append(f"{name} references missing fields: {', '.join(missing)}.")
                if len(value) > 4000:
                    raise ValueError("Resolved content exceeds 4,000 characters.")
                if element.kind == "Text":
                    mask, issue = text_tile(size, element, value)
                    if issue:
                        issues.append(f"{name}. {issue}")
                    tile = WHITE if element.inverted else BLACK
                elif element.kind == "QR code":
                    tile = qr_tile(size, value)
                else:
                    tile = barcode_tile(size, element, value)
            elif element.kind == "Image":
                tile = picture_tile(size, element)
            else:
                mask = shape_mask(size, element)
                tile = BLACK
        except (ValueError, OSError, BarcodeError, DataOverflowError, Image.DecompressionBombError) as error:
            reason = str(error) or "Content is too long for the code."
            issues.append(f"{name}. {reason}")
            continue

        if element.rotation:
            operation = {
                90: Image.Transpose.ROTATE_270,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_90,
            }[element.rotation]
            if mask is not None:
                mask = mask.transpose(operation)
            else:
                tile = tile.transpose(operation)
        if mask is None:
            tape.paste(tile, (element.x, element.y))
        else:
            box = (element.x, element.y, element.x + box_width, element.y + box_height)
            if element.kind == "Text" and element.inverted:
                tape.paste(BLACK, box)
            tape.paste(tile, box, mask)
    return tape, issues


def save_png(image, path):
    palette = image.point([0] * 128 + [1] * 128).convert("P")
    palette.putpalette([0, 0, 0, 255, 255, 255])
    palette.save(path, format="PNG", bits=1, dpi=(DPI, DPI))
