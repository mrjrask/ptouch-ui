"""USB printing through the installed ptouch-print executable."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .document import DPI, MAX_RECORDS
from .raster import save_png

# A short blank page, printed without the final feed and cut, ends where the first label begins.
# The printer cuts at that page boundary once it reaches the cutter, trimming the blank leader.
PRECUT_DOTS = 16
RELEASE_HINT = "Press Feed & Cut on the printer to release any tape left inside."


def supports(available, option):
    return re.search(rf"{option}(?:[\s=,]|$)", available) is not None


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
        if not 1 <= self.copies <= 99:
            raise ValueError("Use 1–99 copies.")
        if self.feed is not None and not 0 <= self.feed <= 1000:
            raise ValueError("Use 0–1000 feed dots, or leave it blank for the printer default.")


def run(arguments, timeout):
    executable = shutil.which(os.environ.get("PTOUCH_PRINT", "ptouch-print"))
    if not executable:
        raise PrinterError("ptouch-print was not found. Install it or add it to PATH.")
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
    arguments = ["--serial", serial] if serial else []
    result = run([*arguments, "--info"], timeout=12)
    try:
        output = command_output(result)
    except PrinterError as error:
        raise PrinterError(f"{error}\nCheck USB, the printer's E mode, and device permissions.") from error
    error = re.search(r"^error\s*=\s*(?:0x)?([0-9a-f]+)\b", output, re.IGNORECASE | re.MULTILINE)
    if error and int(error[1], 16):
        raise PrinterError(f"Printer reports media or device error 0x{int(error[1], 16):04x}.")
    dpi = re.search(r"printer has\s+(\d+)\s*dpi", output, re.IGNORECASE)
    pixels = re.search(r"maximum printing width for this tape is\s+(\d+)\s*px", output, re.IGNORECASE)
    width = re.search(r"media width\s*=\s*(\d+(?:\.\d+)?)\s*mm", output, re.IGNORECASE)
    if not (pixels and width):
        raise PrinterError(f"Could not read the loaded tape from ptouch-print.\n{output}")
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
    count = len(images) * options.copies
    if not images or count > MAX_RECORDS:
        raise PrinterError(f"Send between 1 and {MAX_RECORDS} labels per print job.")
    if any(image.size != images[0].size for image in images):
        raise PrinterError("All labels in a print job must have the same size.")
    arguments = ["--serial", options.serial] if options.serial else []
    if options.feed is not None or options.serial or options.precut:
        available = details(run(["--help"], timeout=8))
        if options.feed is not None:
            if not supports(available, "--feed"):
                raise PrinterError("This ptouch-print version does not support feed adjustment.")
            arguments.extend(("--feed", str(options.feed)))
        if options.serial and not supports(available, "--serial"):
            raise PrinterError(
                "This ptouch-print version does not support printer selection by serial number."
            )
        if options.precut and not (supports(available, "--chain") and supports(available, "--pad")):
            raise PrinterError(
                "This ptouch-print version cannot trim the blank leader. "
                "Update ptouch-print or turn off Trim blank leader."
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
            try:
                command_output(run([*arguments, "--chain", "--pad", str(PRECUT_DOTS)], timeout=60))
            except PrinterError as error:
                raise PrinterError(f"The leader could not be fed. {RELEASE_HINT}\n{error}") from error
        if options.strip:
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
                # With pre-cut, labels are chained so each is cut where the next one begins.
                chain = ["--chain"] if options.precut and number < count else []
                try:
                    command_output(run([*arguments, *chain, "--image", path], timeout=90))
                except PrinterError as error:
                    hint = f" {RELEASE_HINT}" if options.precut else ""
                    raise PrinterError(f"Completed {number - 1} of {count} labels.{hint}\n{error}") from error
                report(number)
