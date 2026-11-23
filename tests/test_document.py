from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image

from ptouch_ui.document import (
    TAPES,
    TEMPLATES,
    Element,
    Label,
    load_label,
    read_csv,
    save_label,
    template,
)
from ptouch_ui.raster import import_image, render, save_png


class DocumentTests(TestCase):
    def test_png_is_an_exact_two_color_palette_at_printer_resolution(self):
        for tape, height in TAPES.items():
            with self.subTest(tape=tape), TemporaryDirectory() as directory:
                label = Label(tape=tape, length=25.4, elements=(Element("Rectangle", 0, 0, 10, 5),))
                image, issues = render(label)
                self.assertEqual([], issues)
                path = Path(directory) / "tape.png"
                save_png(image, path)
                self.assertEqual(bytes([1, 3]), path.read_bytes()[24:26])
                with Image.open(path) as result:
                    self.assertEqual("P", result.mode)
                    self.assertEqual((180, height), result.size)
                    self.assertAlmostEqual(180, result.info["dpi"][0], places=1)
                    self.assertEqual(image.tobytes(), result.convert("L").tobytes())

    def test_all_templates_are_ready_to_print(self):
        for name in TEMPLATES[1:]:
            with self.subTest(name=name):
                label = template(name)
                for record in range(max(1, len(label.rows))):
                    image, issues = render(label, record)
                    self.assertEqual([], issues)
                    self.assertEqual((0, 255), image.getextrema())

    def test_round_trip_embeds_the_image_and_csv_records(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            image_path = folder / "logo.png"
            Image.new("RGB", (20, 10), "black").save(image_path)
            encoded, _ = import_image(image_path)
            image_path.unlink()
            csv_path = folder / "source.csv"
            csv_path.write_text('Name,ID\n"Room, North",001\nRoom South,002\n', encoding="utf-8-sig")
            rows = read_csv(csv_path)
            csv_path.unlink()
            self.assertEqual("Room, North", rows[0]["Name"])
            self.assertEqual("001", rows[0]["ID"])
            label = Label(
                elements=(
                    Element("Image", 0, 0, 20, 10, encoded),
                    Element("Text", 25, 0, 100, 30, "{{Name}}"),
                ),
                rows=rows,
            )
            path = folder / "label.ptlabel"
            save_label(path, label)
            self.assertEqual(label, load_label(path))
            first, issues = render(load_label(path))
            self.assertFalse(issues)
            self.assertEqual(0, first.getpixel((10, 5)))
            self.assertNotEqual(first.tobytes(), render(label, 1)[0].tobytes())

    def test_invalid_content_and_print_area_are_reported(self):
        label = Label(
            tape=6,
            elements=(
                Element("Text", 0, 0, 100, 25, "{{missing}}"),
                Element("QR code", 100, 0, 32, 32, "desk"),
                Element("Barcode", 0, 26, 300, 40, "5901234123450", barcode="EAN-13"),
            ),
        )
        _, issues = render(label)
        self.assertTrue(any("missing fields" in issue for issue in issues))
        self.assertTrue(any("two dots" in issue for issue in issues))
        self.assertTrue(any("outside" in issue for issue in issues))
        self.assertTrue(any("check digit" in issue for issue in issues))

    def test_date_and_rotated_multiline_text(self):
        label = Label(elements=(Element("Text", 0, 0, 30, 76, "Due\n{{date}}", rotation=90, align="center"),))
        first, issues = render(label, today=date(2026, 1, 2))
        self.assertEqual([], issues)
        self.assertNotEqual(first.tobytes(), render(label, today=date(2026, 1, 3))[0].tobytes())
