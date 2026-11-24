import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PIL import Image

from ptouch_ui.document import (
    MAX_FILE_BYTES,
    MAX_RESOLVED_LENGTH,
    TAPES,
    TEMPLATES,
    Element,
    Label,
    content_length,
    dots,
    load_label,
    read_csv,
    save_label,
    substitute,
    template,
)
from ptouch_ui.raster import import_image, largest_text_size, render, save_png


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

    def test_every_barcode_format_renders(self):
        for name, value in (
            ("Code 128", "Bin 7"),
            ("Code 39", "bin-07"),
            ("EAN-13", "590123412345"),
            ("EAN-8", "96385074"),
            ("UPC-A", "03600029145"),
            ("ITF", "12345678"),
        ):
            with self.subTest(barcode=name):
                label = Label(
                    tape=24, length=80, elements=(Element("Barcode", 0, 0, 540, 128, value, barcode=name),)
                )
                image, issues = render(label)
                self.assertEqual([], issues)
                self.assertEqual((0, 255), image.getextrema())

    def test_saving_keeps_the_existing_file_mode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "label.ptlabel"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o644)
            save_label(path, Label())
            self.assertEqual(0o644, path.stat().st_mode & 0o777)
            self.assertEqual(Label(), load_label(path))

    def test_saving_through_a_symlink_updates_the_target(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "original.ptlabel"
            alias = target.with_name("shortcut.ptlabel")
            save_label(target, Label())
            alias.symlink_to(target.name)
            revised = Label(length=42)
            save_label(alias, revised)
            self.assertTrue(alias.is_symlink())
            self.assertEqual(revised, load_label(target))

    def test_malformed_object_and_record_lists_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "label.ptlabel"
            label = {"version": 1, "tape": 12, "length": 70, "elements": []}
            path.write_text(json.dumps(label), encoding="utf-8")
            self.assertEqual(Label(), load_label(path))
            for override in ({"elements": {}}, {"rows": ""}):
                with self.subTest(override=override):
                    path.write_text(json.dumps({**label, **override}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "objects and records as lists"):
                        load_label(path)

    def test_non_regular_and_oversized_inputs_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "selected"
            os.mkfifo(path)
            for read in (load_label, read_csv):
                with self.assertRaisesRegex(ValueError, "regular file"):
                    read(path)
            path.unlink()
            with path.open("wb") as source:
                source.truncate(MAX_FILE_BYTES + 1)
            for read in (load_label, read_csv):
                with self.assertRaisesRegex(ValueError, "exceeds 20 MB"):
                    read(path)

    def test_rejects_deep_json_and_invalid_unicode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "label.ptlabel"
            path.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
            with self.assertRaises(ValueError):
                load_label(path)
            label = {"version": 1, "tape": 12, "length": 70, "elements": [], "rows": [{"id": "\ud800"}]}
            path.write_text(json.dumps(label), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid UTF-8"):
                load_label(path)

    def test_field_expansion_is_bounded_by_resolved_size(self):
        field = "x" * MAX_RESOLVED_LENGTH
        self.assertEqual((field, []), substitute("{{item}}{{empty}}", {"item": field, "empty": ""}))
        value = "{{item}}{{item}}"
        with self.assertRaisesRegex(ValueError, "4,000 characters"):
            substitute(value, {"item": field})
        label = Label(elements=(Element("Text", 0, 0, 100, 25, value),), rows=({"item": field},))
        self.assertEqual(["Text 1. Resolved content exceeds 4,000 characters."], render(label)[1])

    def test_long_thin_import_keeps_a_printable_pixel(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rule.png"
            Image.new("L", (1000, 1), 0).save(path)
            encoded, _ = import_image(path)
        image, issues = render(Label(elements=(Element("Image", 0, 0, 50, 10, encoded),)))
        self.assertEqual([], issues)
        self.assertEqual((0, 255), image.getextrema())

    def test_csv_with_another_encoding_is_explained(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "latin.csv"
            path.write_bytes("Name\nCaf\xe9\n".encode("latin-1"))
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                read_csv(path)

    def test_barcode_check_digits_and_itf_length_are_validated(self):
        for name, value, message in (
            ("UPC-A", "036000291453", "check digit"),
            ("EAN-8", "12345", "7 digits or 8"),
            ("ITF", "123", "even number"),
            ("Code 39", "groß", "letters A–Z"),
        ):
            with self.subTest(barcode=name):
                label = Label(
                    tape=24, length=80, elements=(Element("Barcode", 0, 0, 540, 128, value, barcode=name),)
                )
                _, issues = render(label)
                self.assertEqual(1, len(issues))
                self.assertIn(message, issues[0])

    def test_inverted_text_prints_white_on_a_black_box(self):
        text = Element("Text", 10, 10, 200, 50, "HELLO", font_size=30)
        normal, _ = render(Label(elements=(text,)))
        inverted, issues = render(Label(elements=(replace(text, inverted=True),)))
        self.assertEqual([], issues)
        self.assertEqual(255, normal.getpixel((11, 11)))
        self.assertEqual(0, inverted.getpixel((11, 11)))
        self.assertEqual(255, inverted.getpixel((5, 5)))
        self.assertEqual((0, 255), inverted.crop((10, 10, 210, 60)).getextrema())

    def test_content_length_keeps_matching_margins(self):
        label = Label(length=200, elements=(Element("Rectangle", dots(4), 0, dots(30), 10),))
        length = content_length(label)
        self.assertGreaterEqual(dots(length), dots(4) * 2 + dots(30))
        self.assertLessEqual(length, 38.1)
        self.assertEqual(5, content_length(Label(elements=(Element("Line", 0, 0, 3, 2),))))
        self.assertEqual(70, content_length(Label()))

    def test_rounded_rectangle_clears_its_corners(self):
        square = Element("Rectangle", 0, 0, 60, 60, filled=True)
        sharp, _ = render(Label(elements=(square,)))
        rounded, issues = render(Label(elements=(replace(square, radius=20),)))
        self.assertEqual([], issues)
        self.assertEqual(0, sharp.getpixel((1, 1)))
        self.assertEqual(255, rounded.getpixel((1, 1)))
        self.assertEqual(0, rounded.getpixel((30, 30)))
        with self.assertRaises(ValueError):
            replace(square, radius=65)

    def test_largest_text_size_fills_the_box_without_overflowing(self):
        text = Element("Text", 0, 0, 300, 60, "ASSET", font_size=12, fit=False)
        size = largest_text_size(text, text.value)
        self.assertGreater(size, 12)
        self.assertEqual([], render(Label(elements=(replace(text, font_size=size),)))[1])
        self.assertTrue(render(Label(elements=(replace(text, font_size=size + 1),)))[1])
        # A quarter turn lays the text out along the box's height.
        self.assertLess(largest_text_size(replace(text, rotation=90), text.value), size)
        self.assertEqual(12, largest_text_size(text, "  "))


class RecentFileTests(TestCase):
    def test_recent_list_is_deduplicated_capped_and_tolerates_bad_data(self):
        from unittest.mock import patch

        from ptouch_ui import recent

        with TemporaryDirectory() as directory, patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}):
            self.assertEqual([], recent.load_recent())
            for number in range(10):
                recent.remember_recent(Path(directory) / f"{number}.ptlabel")
            paths = recent.remember_recent(Path(directory) / "3.ptlabel")
            self.assertEqual(recent.LIMIT, len(paths))
            self.assertTrue(paths[0].endswith("3.ptlabel"))
            self.assertEqual(1, sum(path.endswith("3.ptlabel") for path in paths))
            self.assertEqual(paths, recent.load_recent())
            recent.recent_file().write_text('{"not": "a list"}', encoding="utf-8")
            self.assertEqual([], recent.load_recent())
            recent.recent_file().write_text('["relative.ptlabel", 7]', encoding="utf-8")
            self.assertEqual([], recent.load_recent())
