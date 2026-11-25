"""Send multi-page raster jobs to the PT-2430PC through libusb.

ptouch-print starts a new job for every run and ends it with a feed, so the printer never sees
a page boundary it can cut at before a label. Trimming the blank leader needs one job with
auto-cut turned on, which cuts the leader off before the first page, so this module writes that
job itself.
"""

import ctypes
import ctypes.util
from contextlib import contextmanager

from PIL import Image, ImageOps

VENDOR_ID = 0x04F9
PRODUCT_ID = 0x202D
OUT_ENDPOINT = 0x02
IN_ENDPOINT = 0x81
HEAD_DOTS = 128
TIMEOUT_MS = 15000

INITIALIZE = b"\x1b@"
STATUS_REQUEST = b"\x1biS"
RASTER_MODE = b"\x1biR\x01"
# ESC i M: bit 6 turns on auto-cut, so the printer cuts at every page boundary.
AUTO_CUT = b"\x1biM\x40"
NEXT_PAGE = b"\x0c"
LAST_PAGE = b"\x1a"
STATUS_ERRORS = {
    (8, 0x01): "no tape cassette is loaded",
    (8, 0x02): "the tape ran out",
    (8, 0x04): "the cutter jammed",
    (8, 0x10): "the printer is busy",
    (9, 0x01): "the tape cassette was changed",
    (9, 0x10): "the cover is open",
    (9, 0x20): "the print head overheated",
}


class UsbError(RuntimeError):
    pass


def raster_lines(image):
    """Return one 16-byte raster line per column of a label image, as ptouch-print sends them."""
    width, height = image.size
    if height > HEAD_DOTS:
        raise UsbError(f"The label is {height} dots tall, more than the {HEAD_DOTS} dot print head.")
    # Ink is 1 in the raster. Center the tape on the head and read each column from the top.
    ink = ImageOps.invert(image.convert("L")).point(lambda value: 255 if value >= 128 else 0)
    head = Image.new("L", (width, HEAD_DOTS), 0)
    head.paste(ink, (0, HEAD_DOTS - (HEAD_DOTS // 2 - height // 2) - height))
    data = head.transpose(Image.Transpose.TRANSPOSE).convert("1").tobytes()
    size = HEAD_DOTS // 8
    return [data[start : start + size] for start in range(0, len(data), size)]


def job_pages(pages):
    """Yield the commands for the pages of one auto-cut job, after the status check."""
    yield RASTER_MODE
    yield AUTO_CUT
    for number, page in enumerate(pages, start=1):
        for line in raster_lines(page):
            yield b"G" + bytes((len(line), 0)) + line
        yield LAST_PAGE if number == len(pages) else NEXT_PAGE


def status_problem(status):
    if len(status) != 32 or status[0] != 0x80:
        return "The printer sent an unexpected status reply."
    found = [message for (index, bit), message in STATUS_ERRORS.items() if status[index] & bit]
    if found:
        return "The printer reports that " + " and ".join(found) + "."
    if status[8] or status[9]:
        return f"The printer reports error 0x{status[8]:02x}{status[9]:02x}."
    return None


class Library:
    def __init__(self):
        name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
        try:
            lib = ctypes.CDLL(name)
        except OSError as error:
            raise UsbError("libusb-1.0 is not installed. Install the libusb-1.0-0 package.") from error
        pointer = ctypes.c_void_p
        lib.libusb_init.argtypes = [ctypes.POINTER(pointer)]
        lib.libusb_exit.argtypes = [pointer]
        lib.libusb_exit.restype = None
        lib.libusb_get_device_list.argtypes = [pointer, ctypes.POINTER(ctypes.POINTER(pointer))]
        lib.libusb_get_device_list.restype = ctypes.c_ssize_t
        lib.libusb_free_device_list.argtypes = [ctypes.POINTER(pointer), ctypes.c_int]
        lib.libusb_free_device_list.restype = None
        lib.libusb_get_device_descriptor.argtypes = [pointer, ctypes.c_char_p]
        lib.libusb_open.argtypes = [pointer, ctypes.POINTER(pointer)]
        lib.libusb_close.argtypes = [pointer]
        lib.libusb_close.restype = None
        lib.libusb_get_string_descriptor_ascii.argtypes = [
            pointer,
            ctypes.c_uint8,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.libusb_set_auto_detach_kernel_driver.argtypes = [pointer, ctypes.c_int]
        lib.libusb_claim_interface.argtypes = [pointer, ctypes.c_int]
        lib.libusb_release_interface.argtypes = [pointer, ctypes.c_int]
        lib.libusb_bulk_transfer.argtypes = [
            pointer,
            ctypes.c_ubyte,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
        ]
        lib.libusb_error_name.argtypes = [ctypes.c_int]
        lib.libusb_error_name.restype = ctypes.c_char_p
        self.lib = lib

    def check(self, result, action):
        if result < 0:
            raise UsbError(f"Could not {action}: {self.lib.libusb_error_name(result).decode()}.")
        return result


class Printer:
    def __init__(self, library, handle):
        self.library, self.handle = library, handle

    def write(self, data):
        sent = ctypes.c_int()
        result = self.library.lib.libusb_bulk_transfer(
            self.handle, OUT_ENDPOINT, data, len(data), ctypes.byref(sent), TIMEOUT_MS
        )
        self.library.check(result, "send data to the printer")
        if sent.value != len(data):
            raise UsbError(f"The printer accepted only {sent.value} of {len(data)} bytes.")

    def read_status(self):
        self.write(STATUS_REQUEST)
        buffer = ctypes.create_string_buffer(32)
        received = ctypes.c_int()
        # The printer answers once it has processed the request; empty reads mean not yet.
        for _attempt in range(20):
            result = self.library.lib.libusb_bulk_transfer(
                self.handle, IN_ENDPOINT, buffer, 32, ctypes.byref(received), 500
            )
            if result != -7:  # LIBUSB_ERROR_TIMEOUT
                self.library.check(result, "read the printer status")
            if received.value:
                return buffer.raw[: received.value]
        raise UsbError("The printer did not answer the status request.")


def serial_number(library, handle, descriptor):
    index = descriptor[16]
    if not index:
        return ""
    buffer = ctypes.create_string_buffer(256)
    length = library.lib.libusb_get_string_descriptor_ascii(handle, index, buffer, len(buffer))
    return buffer.raw[:length].decode("ascii", "replace") if length > 0 else ""


@contextmanager
def open_printer(serial=""):
    library = Library()
    lib = library.lib
    context = ctypes.c_void_p()
    library.check(lib.libusb_init(ctypes.byref(context)), "start libusb")
    try:
        devices = ctypes.POINTER(ctypes.c_void_p)()
        count = library.check(lib.libusb_get_device_list(context, ctypes.byref(devices)), "list USB devices")
        handle = None
        try:
            for index in range(count):
                descriptor = ctypes.create_string_buffer(18)
                if lib.libusb_get_device_descriptor(devices[index], descriptor) < 0:
                    continue
                vendor = int.from_bytes(descriptor.raw[8:10], "little")
                product = int.from_bytes(descriptor.raw[10:12], "little")
                if (vendor, product) != (VENDOR_ID, PRODUCT_ID):
                    continue
                candidate = ctypes.c_void_p()
                library.check(lib.libusb_open(devices[index], ctypes.byref(candidate)), "open the printer")
                if serial and serial_number(library, candidate, descriptor.raw) != serial:
                    lib.libusb_close(candidate)
                    continue
                handle = candidate
                break
        finally:
            lib.libusb_free_device_list(devices, 1)
        if handle is None:
            target = f" with serial {serial}" if serial else ""
            raise UsbError(f"No PT-2430PC{target} was found on USB.")
        try:
            lib.libusb_set_auto_detach_kernel_driver(handle, 1)
            library.check(lib.libusb_claim_interface(handle, 0), "claim the printer interface")
            try:
                yield Printer(library, handle)
            finally:
                lib.libusb_release_interface(handle, 0)
        finally:
            lib.libusb_close(handle)
    finally:
        lib.libusb_exit(context)


def print_pages(pages, serial="", report=lambda _page: None):
    """Print pages as one auto-cut job.

    report(0) runs once the printer is ready and the job starts; report(n) once page n is sent.
    """
    with open_printer(serial) as printer:
        printer.write(INITIALIZE)
        problem = status_problem(printer.read_status())
        if problem:
            raise UsbError(problem)
        page = 0
        report(page)
        for command in job_pages(pages):
            printer.write(command)
            if command in (NEXT_PAGE, LAST_PAGE):
                page += 1
                report(page)
