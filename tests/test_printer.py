import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from ptouch_ui import printer, usb
from ptouch_ui.printer import PrinterError, PrintOptions, print_images, printer_problem, printer_status

FAKE_PRINTER = """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["PRINTER_LOG"])
if "--info" in args:
    mode = os.environ.get("PRINTER_MODE", "")
    if mode == "not-found":
        print("No P-Touch printer found on USB (remember to put switch to position E)", file=sys.stderr)
        sys.exit(1)
    if mode in ("no-dpi", "no-dpi-other-model"):
        model = "PT-2430PC" if mode == "no-dpi" else "PT-9500PC"
        print(f"{model} found on USB bus 1, device 5", file=sys.stderr)
        print("maximum printing width for this tape is 120px")
        print("media width = 18 mm")
        print("error = 0000")
    elif mode == "legacy":
        print("printer has 180 dpi, maximum printing width is 128px")
        print("maximum printing width for this tape is 24px")
        print("media width = 4 mm")
        print("error = 0000")
    else:
        dpi = 360 if mode == "wrong-dpi" else 180
        print(f"printer has {dpi} dpi, maximum printing width is 128 px")
        print("maximum printing width for this tape is 76px")
        print("media width = 12 mm")
        print("error = " + ("0x0002" if mode == "media-error" else "0x0000"))
        if mode == "init-error":
            print("ptouch_init() failed")
elif args == ["--help"]:
    if os.environ.get("PRINTER_MODE") == "no-cutmark":
        print("--image --no--cutmark --pad <n>")
    else:
        print("--image --cutmark --pad <n> --serial=<serial> --feed=<dots>")
elif "--image" in args or "--pad" in args:
    for idx, arg in enumerate(args):
        if arg == "--image":
            assert Path(args[idx + 1]).read_bytes()[24:26] == bytes([1, 3])
    previous_jobs = log.read_text().count("\\n") if log.exists() else 0
    with log.open("a") as file:
        file.write(json.dumps(args) + "\\n")
    if os.environ.get("PRINTER_MODE") == "fail-second" and previous_jobs:
        print("ptouch_sendraster() failed")
    if os.environ.get("PRINTER_MODE") == "write-error":
        print("write error: LIBUSB_ERROR_IO", file=sys.stderr)
else:
    sys.exit(1)
"""


class PrinterTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = Path(directory.name)
        executable = folder / "ptouch-print"
        executable.write_text(f"#!{sys.executable}\n{FAKE_PRINTER}", encoding="utf-8")
        executable.chmod(0o755)
        self.log = folder / "jobs.jsonl"
        environment = patch.dict(os.environ, {"PTOUCH_PRINT": str(executable), "PRINTER_LOG": str(self.log)})
        environment.start()
        self.addCleanup(environment.stop)
        self.image = Image.new("L", (100, 76), 0)

    def test_detects_legacy_tape_status(self):
        with patch.dict(os.environ, {"PRINTER_MODE": "legacy"}):
            tape = printer_status()
        self.assertEqual((3.5, 24, 180), (tape.width, tape.dots, tape.dpi))

    def test_older_status_output_without_resolution_assumes_180_dpi(self):
        with patch.dict(os.environ, {"PRINTER_MODE": "no-dpi"}):
            tape = printer_status()
        self.assertEqual((18, 120, 180), (tape.width, tape.dots, tape.dpi))

    def test_rejects_unverified_or_incompatible_resolution(self):
        for mode, expectation in (
            ("no-dpi-other-model", "did not report the resolution"),
            ("wrong-dpi", "device reports 360 dpi"),
        ):
            with (
                self.subTest(mode=mode),
                patch.dict(os.environ, {"PRINTER_MODE": mode}),
                self.assertRaisesRegex(PrinterError, expectation),
            ):
                print_images([self.image], 12, PrintOptions())
            self.assertFalse(self.log.exists())

    def test_status_checks_failures_reported_with_successful_exit_code(self):
        with (
            patch.dict(os.environ, {"PRINTER_MODE": "init-error"}),
            self.assertRaisesRegex(PrinterError, r"ptouch_init\(\) failed"),
        ):
            printer_status()

    def test_usb_diagnostic_distinguishes_passthrough_and_backend_failures(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"PRINTER_MODE": "not-found"}):
            devices = Path(directory) / "devices"
            with patch("ptouch_ui.printer.USB_DEVICES", devices):
                with self.assertRaisesRegex(PrinterError, "lsusb -d 04f9:202d"):
                    printer_status()
                device = devices / "1-1"
                device.mkdir(parents=True)
                (devices / "1-1:1.0").mkdir()
                (device / "idVendor").write_text("04f9\n")
                (device / "idProduct").write_text("202c\n")
                with self.assertRaisesRegex(PrinterError, "attach the physical printer"):
                    printer_status()
                (device / "idProduct").write_text("202d\n")
                with self.assertRaisesRegex(PrinterError, "Linux sees the PT-2430PC .*udev rules"):
                    printer_status()

    def test_check_printer_works_without_a_display(self):
        arguments = [sys.executable, "-m", "ptouch_ui", "--check-printer"]
        env = {**os.environ, "DISPLAY": ""}
        success = subprocess.run(arguments, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(0, success.returncode, success.stderr)
        self.assertIn("12 mm tape, 76 printable dots, 180 dpi", success.stdout)
        env["PRINTER_MODE"] = "not-found"
        failure = subprocess.run(arguments, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(1, failure.returncode)
        self.assertIn("No P-Touch printer found on USB", failure.stderr)
        self.assertFalse(self.log.exists())

    def test_usb_write_failure_is_not_reported_as_a_printed_label(self):
        progress = []
        with (
            patch.dict(os.environ, {"PRINTER_MODE": "write-error"}),
            self.assertRaisesRegex(PrinterError, "write error: LIBUSB_ERROR_IO"),
        ):
            print_images([self.image], 12, PrintOptions(), progress.append)
        self.assertEqual([], progress)

    def test_rejects_tape_mismatch_before_sending_any_image(self):
        with self.assertRaisesRegex(PrinterError, "printer reports 12 mm"):
            print_images([self.image], 9, PrintOptions())
        self.assertFalse(self.log.exists())

    def test_rejects_media_error(self):
        with (
            patch.dict(os.environ, {"PRINTER_MODE": "media-error"}),
            self.assertRaisesRegex(PrinterError, "0x0002"),
        ):
            printer_status()

    def test_strip_interleaves_cut_marks_and_supports_optional_flags(self):
        progress = []
        print_images(
            [self.image, self.image],
            12,
            PrintOptions(copies=2, strip=True, feed=5, serial="12 34"),
            progress.append,
        )
        command = json.loads(self.log.read_text())
        self.assertEqual(["--serial", "12 34", "--feed", "5"], command[:4])
        self.assertEqual(4, command.count("--image"))
        self.assertEqual(3, command.count("--cutmark"))
        self.assertEqual([4], progress)
        self.assertTrue(all(not Path(arg).exists() for arg in command if arg.endswith(".png")))

    def test_separate_jobs_stop_after_cli_reports_a_failure_even_with_exit_zero(self):
        progress = []
        with (
            patch.dict(os.environ, {"PRINTER_MODE": "fail-second"}),
            self.assertRaisesRegex(PrinterError, "Completed 1 of 3 labels"),
        ):
            print_images([self.image], 12, PrintOptions(copies=3), progress.append)
        self.assertEqual([1], progress)
        self.assertEqual(2, len(self.log.read_text().splitlines()))

    def usb_job(self, status=None, fail_after=None):
        """Patch the USB printer and return the list its writes are recorded in."""
        writes = []
        ready = bytes([0x80, 0x20]) + bytes(30)

        class FakePrinter:
            def write(self, data):
                if fail_after is not None and data in (usb.NEXT_PAGE, usb.LAST_PAGE):
                    if writes.count(usb.NEXT_PAGE) + writes.count(usb.LAST_PAGE) == fail_after:
                        raise usb.UsbError("Could not send data to the printer: LIBUSB_ERROR_IO.")
                writes.append(data)

            def read_status(self):
                return status or ready

        @contextmanager
        def fake_open(serial=""):
            writes.append(("open", serial))
            yield FakePrinter()

        patcher = patch.object(usb, "open_printer", fake_open)
        patcher.start()
        self.addCleanup(patcher.stop)
        return writes

    @staticmethod
    def pages(writes):
        """Split recorded writes into pages of raster lines, with the command ending each page."""
        pages, lines = [], []
        for data in writes:
            if isinstance(data, bytes) and data.startswith(b"G"):
                lines.append(data)
            elif data in (usb.NEXT_PAGE, usb.LAST_PAGE):
                pages.append((len(lines), data))
                lines = []
        return pages

    def test_precut_sends_a_blank_page_and_every_label_as_one_auto_cut_job(self):
        writes = self.usb_job()
        progress = []
        print_images([self.image, self.image], 12, PrintOptions(precut=True, serial="A1"), progress.append)
        # Only the status check goes through ptouch-print; the job goes straight to USB.
        self.assertFalse(self.log.exists())
        self.assertEqual([("open", "A1"), usb.INITIALIZE, usb.RASTER_MODE, usb.AUTO_CUT], writes[:4])
        self.assertEqual(
            [(16, usb.NEXT_PAGE), (100, usb.NEXT_PAGE), (100, usb.LAST_PAGE)], self.pages(writes)
        )
        self.assertEqual([1, 2], progress)
        line = writes[4]
        self.assertEqual(b"G\x10\x00", line[:3])
        self.assertEqual(19, len(line))

    def test_raster_lines_match_ptouch_print_layout(self):
        image = Image.new("L", (2, 76), 255)
        image.putpixel((0, 0), 0)
        image.putpixel((1, 75), 0)
        top, bottom = usb.raster_lines(image)
        # ptouch-print centers the image on the 128-dot head and stores dot n in bit n of a
        # little-endian 128-bit line, with the image's top row at the highest dot.
        self.assertEqual(1 << (26 + 75), int.from_bytes(top, "big"))
        self.assertEqual(1 << 26, int.from_bytes(bottom, "big"))

    def test_precut_strip_prints_one_page_with_cut_marks_after_the_leader(self):
        writes = self.usb_job()
        progress = []
        print_images([self.image], 12, PrintOptions(copies=2, strip=True, precut=True), progress.append)
        self.assertEqual([(16, usb.NEXT_PAGE), (209, usb.LAST_PAGE)], self.pages(writes))
        self.assertEqual([2], progress)
        strip = printer.strip_image([Image.new("L", (10, 76), 255)] * 2)
        self.assertEqual(29, strip.width)
        self.assertEqual([255, 255, 255, 0, 0, 0], [strip.getpixel((15, y)) for y in range(6)])

    def test_precut_reports_printer_errors_before_starting(self):
        status = bytearray([0x80, 0x20]) + bytearray(30)
        status[9] = 0x10
        writes = self.usb_job(status=bytes(status))
        with self.assertRaisesRegex(PrinterError, "cover is open") as caught:
            print_images([self.image], 12, PrintOptions(precut=True))
        self.assertNotIn("Feed & Cut", str(caught.exception))
        self.assertEqual([], self.pages(writes))

    def test_failed_trimmed_job_tells_the_user_to_release_the_tape(self):
        progress = []
        self.usb_job(fail_after=2)
        with self.assertRaisesRegex(PrinterError, "Completed 1 of 2 labels. Press Feed & Cut"):
            print_images([self.image], 12, PrintOptions(copies=2, precut=True), progress.append)
        self.assertEqual([1], progress)

    def test_strip_without_precut_requires_cut_marks(self):
        with (
            patch.dict(os.environ, {"PRINTER_MODE": "no-cutmark"}),
            self.assertRaisesRegex(PrinterError, "does not support strips with cut marks"),
        ):
            print_images([self.image], 12, PrintOptions(copies=2, strip=True))
        self.assertFalse(self.log.exists())

    def test_precut_rejects_feed_adjustment(self):
        with self.assertRaisesRegex(PrinterError, "only when Trim blank leader is off"):
            print_images([self.image], 12, PrintOptions(precut=True, feed=10))

    def test_print_job_waits_for_a_running_status_check(self):
        from threading import Event, Thread

        from ptouch_ui import printer

        started = Event()
        release = Event()
        order = []

        def slow_status(_serial):
            started.set()
            release.wait(5)
            order.append("status")

        def record_print(*_args):
            order.append("print")

        with (
            patch("ptouch_ui.printer.read_status", slow_status),
            patch("ptouch_ui.printer.send_images", record_print),
        ):
            check = Thread(target=printer.printer_status)
            check.start()
            started.wait(5)
            job = Thread(target=printer.print_images, args=([], 12, PrintOptions()))
            job.start()
            job.join(0.2)
            self.assertTrue(job.is_alive())
            release.set()
            check.join(5)
            job.join(5)
        self.assertEqual(["status", "print"], order)


class PrinterProblemTests(TestCase):
    def test_summarizes_detection_failures_for_the_toolbar(self):
        self.assertEqual(
            "ptouch-print not installed",
            printer_problem(PrinterError("ptouch-print was not found. Install it or add it to PATH.")),
        )
        self.assertEqual(
            "No printer detected",
            printer_problem("No P-Touch printer found on USB (remember to put switch to position E)"),
        )
        self.assertEqual(
            "Printer needs attention", printer_problem("Printer reports media or device error 0x0002.")
        )


class FindExecutableTests(TestCase):
    def test_falls_back_to_a_home_build_when_not_on_path(self):
        with TemporaryDirectory() as home:
            binary = Path(home) / "ptouch-print" / "ptouch-print"
            binary.parent.mkdir()
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            environment = {key: value for key, value in os.environ.items() if key != "PTOUCH_PRINT"}
            with (
                patch.dict(os.environ, {**environment, "HOME": home, "PATH": home}, clear=True),
                patch("ptouch_ui.printer.FALLBACK_LOCATIONS", ("~/ptouch-print/ptouch-print",)),
            ):
                self.assertEqual(str(binary), printer.find_executable())

    def test_configured_path_does_not_fall_back(self):
        with (
            patch.dict(os.environ, {"PTOUCH_PRINT": "/nonexistent/ptouch-print"}),
            patch("ptouch_ui.printer.shutil.which", return_value=None),
        ):
            self.assertIsNone(printer.find_executable())
            with self.assertRaisesRegex(PrinterError, "ptouch-print was not found at PTOUCH_PRINT"):
                printer.run(["--info"], timeout=1)
