"""USB printing through the installed ptouch-print executable."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock

from PIL import Image, ImageDraw

from . import usb
from .document import DPI, MAX_RECORDS
from .raster import save_png

# A short blank page starts each trimmed job. With auto-cut on, the printer cuts at the page
# boundary when it reaches the cutter, so the blank leader comes out as a scrap piece and the
# first label starts where the design does. ptouch-print cannot send such a job: each run is its
# own job ending in a feed, and the printer does not cut at a boundary left by an earlier run.
PRECUT_DOTS = 16
CUTMARK_DOTS = 9
RELEASE_HINT = "Press Feed & Cut on the printer to release any tape left inside."
# Serializes USB access, so a background status check never overlaps a print job.
DEVICE_LOCK = RLock()
USB_DEVICES = Path("/sys/bus/usb/devices")
PT2430PC_USB_ID = "04f9:202d"
# Checked after PATH: desktop launchers can start with a shorter PATH than a terminal, and
# build-ptouch-print.sh leaves an uninstalled build in ~/ptouch-print.
FALLBACK_LOCATIONS = ("/usr/local/bin/ptouch-print", "/usr/bin/ptouch-print", "~/ptouch-print/ptouch-print")


def supports(available, option):
    return re.search(rf"(?<![\w-]){re.escape(option)}(?=[\s=,\]]|$)", available) is not None


class PrinterError(RuntimeError):
    pass


@dataclass(frozen=True)
class TapeStatus:
    width: float
    dots: int
    dpi: int


@dataclass(frozen=True)
class PrintOptions:
    copies: int = 1
    strip: bool = False
    mirror: bool = False
    precut: bool = False
    feed: int | None = None
    serial: str = ""

    def __post_init__(self):
        if type(self.copies) is not int or not 1 <= self.copies <= 99:
            raise ValueError("Use 1–99 copies.")
        if self.feed is not None and (type(self.feed) is not int or not 0 <= self.feed <= 1000):
            raise ValueError("Use 0–1000 feed dots, or leave it blank for the printer default.")


def find_executable():
    configured = os.environ.get("PTOUCH_PRINT")
    if configured:
        return shutil.which(configured)
    found = shutil.which("ptouch-print")
    if found:
        return found
    for location in FALLBACK_LOCATIONS:
        path = Path(location).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def run(arguments, timeout):
    executable = find_executable()
    if not executable:
        if os.environ.get("PTOUCH_PRINT"):
            raise PrinterError(
                f"ptouch-print was not found at PTOUCH_PRINT={os.environ['PTOUCH_PRINT']}. "
                "Fix or unset PTOUCH_PRINT."
            )
        raise PrinterError(
            "ptouch-print was not found on PATH, in /usr/local/bin or /usr/bin, or in ~/ptouch-print. "
            "Install it or run build-ptouch-print.sh --install from the P-Touch UI checkout."
        )
    try:
        return subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            env={**os.environ, "LC_ALL": "C"},
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PrinterError("The printer command timed out. Check the printer before trying again.") from error
    except OSError as error:
        raise PrinterError(f"Could not run ptouch-print. {error}") from error


def details(result):
    return "\n".join(part.strip() for part in (result.stderr, result.stdout) if part.strip())


def printer_status(serial=""):
    with DEVICE_LOCK:
        return read_status(serial)


def pt2430pc_visible():
    """Return whether Linux enumerated the PT-2430PC, or None if sysfs is unavailable."""
    try:
        for device in USB_DEVICES.iterdir():
            try:
                vendor = (device / "idVendor").read_text(encoding="ascii").strip()
                product = (device / "idProduct").read_text(encoding="ascii").strip()
            except FileNotFoundError:
                # Interface entries have no device IDs. Devices can also unplug during the scan.
                continue
            if f"{vendor}:{product}".lower() == PT2430PC_USB_ID:
                return True
    except (OSError, UnicodeError):
        return None
    return False


def usb_troubleshooting():
    visible = pt2430pc_visible()
    if visible is True:
        return (
            f"Linux sees the PT-2430PC ({PT2430PC_USB_ID}) on USB. "
            "Check the ptouch-print udev rules and your user's USB permissions. "
            "If it is already accessible, reconnect it and check that the mode switch is set to E."
        )
    if visible is False:
        return (
            f"Linux does not see the PT-2430PC ({PT2430PC_USB_ID}) on USB. "
            "Check power, the cable, and E mode. In Parallels, attach the physical printer "
            "to this VM from the Devices menu."
        )
    return f"Check USB visibility with lsusb -d {PT2430PC_USB_ID}, E mode, and device permissions."


def read_status(serial):
    arguments = ["--serial", serial] if serial else []
    try:
        result = run([*arguments, "--info"], timeout=12)
        output = command_output(result)
    except PrinterError as error:
        raise PrinterError(f"{error}\n\n{usb_troubleshooting()}") from error
    error = re.search(r"^error\s*=\s*(?:0x)?([0-9a-f]+)\b", output, re.IGNORECASE | re.MULTILINE)
    if error and int(error[1], 16):
        raise PrinterError(f"Printer reports media or device error 0x{int(error[1], 16):04x}.")
    dpi = re.search(r"printer has\s+(\d+)\s*dpi", output, re.IGNORECASE)
    pixels = re.search(r"maximum printing width for this tape is\s+(\d+)\s*px", output, re.IGNORECASE)
    width = re.search(r"media width\s*=\s*(\d+(?:\.\d+)?)\s*mm", output, re.IGNORECASE)
    if not (pixels and width):
        detail = f"\n{output}" if output else ""
        raise PrinterError(
            f"Could not read the loaded tape from ptouch-print.{detail}\n\n{usb_troubleshooting()}"
        )
    measured = float(width[1])
    # Older releases omit the resolution but still report the detected USB model.
    if dpi:
        resolution = int(dpi[1])
    elif re.search(r"^PT-2430PC[ \t]+found on USB\b", output, re.IGNORECASE | re.MULTILINE):
        resolution = DPI
    else:
        raise PrinterError("ptouch-print did not report the resolution or identify a PT-2430PC.")
    status = TapeStatus(3.5 if measured == 4 else measured, int(pixels[1]), resolution)
    if status.dots == 0 or not status.width:
        raise PrinterError("No printable tape was detected.")
    if status.dpi != DPI:
        raise PrinterError(f"This editor expects a {DPI} dpi printer. The device reports {status.dpi} dpi.")
    return status


def printer_problem(error):
    """Summarize a printer_status failure for the toolbar indicator."""
    message = str(error).lower()
    if "ptouch-print was not found" in message:
        return "ptouch-print not installed"
    if "no p-touch printer" in message:
        return "No printer detected"
    return "Printer needs attention"


def verify_tape(status, tape, pixels):
    if status.width != tape or status.dots != pixels:
        raise PrinterError(
            f"Design uses {tape:g} mm tape ({pixels} dots). "
            f"The printer reports {status.width:g} mm ({status.dots} dots). "
            "Load the matching tape or change the label setup."
        )


def command_output(result):
    output = details(result)
    failure = re.search(
        r"\bfailed\b|image is too large|nothing to print|unable to|no p-touch printer|^write error:",
        output,
        re.IGNORECASE | re.MULTILINE,
    )
    if result.returncode or failure:
        raise PrinterError(output or f"ptouch-print exited with status {result.returncode}.")
    return output


def print_images(images, tape, options, report=lambda _count: None):
    with DEVICE_LOCK:
        send_images(images, tape, options, report)


def send_images(images, tape, options, report):
    count = len(images) * options.copies
    if not images or count > MAX_RECORDS:
        raise PrinterError(f"Send between 1 and {MAX_RECORDS} labels per print job.")
    if any(image.size != images[0].size for image in images):
        raise PrinterError("All labels in a print job must have the same size.")
    arguments = ["--serial", options.serial] if options.serial else []
    if options.precut and options.feed is not None:
        raise PrinterError("Feed dots apply only when Trim blank leader is off.")
    if options.feed is not None or (options.serial and not options.precut) or (options.strip and count > 1):
        available = details(run(["--help"], timeout=8))
        if options.strip and count > 1 and not supports(available, "--cutmark"):
            raise PrinterError("This ptouch-print version does not support strips with cut marks.")
        if options.feed is not None:
            if not supports(available, "--feed"):
                raise PrinterError("This ptouch-print version does not support feed adjustment.")
            arguments.extend(("--feed", str(options.feed)))
        if options.serial and not options.precut and not supports(available, "--serial"):
            raise PrinterError(
                "This ptouch-print version does not support printer selection by serial number."
            )

    # Save and validate all input before starting any USB print operation.
    with TemporaryDirectory(prefix="ptouch-ui-") as directory:
        paths = []
        for index, image in enumerate(images):
            path = Path(directory) / f"{index:03d}.png"
            save_png(image, path)
            paths.append(str(path))
        queue = [path for path in paths for _ in range(options.copies)]
        verify_tape(printer_status(options.serial), tape, images[0].height)
        if options.precut:
            send_trimmed(images, options, count, report)
        elif options.strip:
            jobs = []
            for index, path in enumerate(queue):
                if index:
                    jobs.append("--cutmark")
                jobs.extend(("--image", path))
            try:
                command_output(run([*arguments, *jobs], timeout=max(90, 20 * count)))
            except PrinterError as error:
                raise PrinterError(
                    f"The strip did not complete. Check the tape before printing again.\n{error}"
                ) from error
            report(count)
        else:
            for number, path in enumerate(queue, start=1):
                # Each label stays its own fed-and-cut job, so labels never run together uncut.
                try:
                    command_output(run([*arguments, "--image", path], timeout=90))
                except PrinterError as error:
                    raise PrinterError(f"Completed {number - 1} of {count} labels.\n{error}") from error
                report(number)


def strip_image(labels):
    """Join labels into one strip with dashed cut marks between them, like ptouch-print --cutmark."""
    width, height = labels[0].size
    strip = Image.new("L", (len(labels) * width + (len(labels) - 1) * CUTMARK_DOTS, height), 255)
    draw = ImageDraw.Draw(strip)
    for index, label in enumerate(labels):
        left = index * (width + CUTMARK_DOTS)
        strip.paste(label.convert("L"), (left, 0))
        if index:
            for top in range(3, height, 6):
                draw.line(
                    (left - CUTMARK_DOTS + 5, top, left - CUTMARK_DOTS + 5, min(top + 2, height - 1)), 0
                )
    return strip


def send_trimmed(images, options, count, report):
    """Print a blank page and the labels as one auto-cut job, so the leader is cut off."""
    labels = [image for image in images for _ in range(options.copies)]
    blank = Image.new("L", (PRECUT_DOTS, images[0].height), 255)
    pages = [blank, strip_image(labels)] if options.strip else [blank, *labels]
    sent = []
    started = []

    def page_sent(page):
        started.append(page)
        # Page 1 is the blank leader; each later page is one label, or the whole strip.
        if page > 1:
            sent.append(page)
            report(count if options.strip else page - 1)

    try:
        usb.print_pages(pages, options.serial, page_sent)
    except usb.UsbError as error:
        if not started:
            raise PrinterError(f"{error}\n\n{usb_troubleshooting()}") from error
        done = count if options.strip and sent else len(sent)
        raise PrinterError(f"Completed {done} of {count} labels. {RELEASE_HINT}\n{error}") from error
