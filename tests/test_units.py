import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ptouch_ui.document import DOTS_PER_MM, MAX_LENGTH, dots
from ptouch_ui.units import DEFAULT_UNIT, UNITS, load_unit, settings_file, store_unit


class UnitTests(TestCase):
    def test_converts_between_millimetres_and_display_units(self):
        self.assertEqual(25.4, UNITS["in"].to_mm("1"))
        self.assertEqual(15.0, UNITS["cm"].to_mm(1.5))
        self.assertEqual("2", UNITS["in"].text(50.8))
        self.assertEqual("1.969", UNITS["in"].field(50))
        self.assertEqual("5.00", UNITS["cm"].field(50))
        self.assertEqual("50.0", UNITS["mm"].field(50))
        with self.assertRaises(ValueError):
            UNITS["cm"].to_mm("wide")

    def test_fields_convert_back_to_the_same_dot(self):
        for unit in UNITS.values():
            for value in range(dots(MAX_LENGTH) + 1):
                shown = unit.field(value / DOTS_PER_MM)
                self.assertEqual(value, dots(unit.to_mm(shown)), (unit.name, value, shown))

    def test_remembers_the_chosen_unit(self):
        with TemporaryDirectory() as config, patch.dict(os.environ, {"XDG_CONFIG_HOME": config}):
            self.assertEqual(DEFAULT_UNIT, load_unit())
            store_unit("in")
            self.assertEqual("in", load_unit())
            self.assertEqual(Path(config) / "ptouch-ui" / "settings.json", settings_file())
            settings_file().write_text('{"units": "furlongs"}', encoding="utf-8")
            self.assertEqual(DEFAULT_UNIT, load_unit())
            settings_file().write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(DEFAULT_UNIT, load_unit())
