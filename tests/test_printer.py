import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from ptouch_ui.printer import PrinterError, PrintOptions, print_images, printer_status

FAKE_PRINTER = """
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = Path(os.environ["PRINTER_LOG"])
if "--info" in args:
    mode = os.environ.get("PRINTER_MODE", "")
    if mode == "no-dpi":
        print("maximum printing width for this printer is 128px")
        print("maximum printing width for this tape is 120px")
        print("media width = 18 mm")
        print("error = 0000")
    elif mode == "legacy":
        print("printer has 180 dpi, maximum printing width is 128px")
        print("maximum printing width for this tape is 24px")
        print("media width = 4 mm")
        print("error = 0000")
    else:
        print("printer has 180 dpi, maximum printing width is 128 px")
        print("maximum printing width for this tape is 76px")
        print("media width = 12 mm")
        print("error = " + ("0x0002" if mode == "media-error" else "0x0000"))
elif args == ["--help"]:
    print("--image --cutmark --serial=<serial> --feed=<dots>")
elif "--image" in args:
    for idx, arg in enumerate(args):
        if arg == "--image":
            assert Path(args[idx + 1]).read_bytes()[24:26] == bytes([1, 3])
    previous_jobs = log.read_text().count("\\n") if log.exists() else 0
    with log.open("a") as file:
        file.write(json.dumps(args) + "\\n")
    if os.environ.get("PRINTER_MODE") == "fail-second" and previous_jobs:
        print("ptouch_sendraster() failed")
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
