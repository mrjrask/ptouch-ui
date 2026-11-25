"""Display units for lengths and positions; documents always store millimetres and dots."""

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .recent import recent_file


@dataclass(frozen=True)
class Unit:
    name: str
    mm: float
    # Enough decimals that a value shown in a field converts back to the same dot.
    decimals: int
    # Ruler steps between numbered ticks, from finest to coarsest, and minor ticks per step.
    rulers: tuple
    divisions: int
    # Grid spacing when zoomed in and when zoomed out.
    grid: tuple

    def from_mm(self, mm):
        return mm / self.mm

    def to_mm(self, value):
        return float(value) * self.mm

    def field(self, mm):
        """Return mm as fixed-precision text for an editable field."""
        return f"{self.from_mm(mm):.{self.decimals}f}"

    def text(self, mm):
        """Return mm as short text without trailing zeros."""
        return f"{round(self.from_mm(mm), self.decimals):g}"


UNITS = {
    "mm": Unit("mm", 1, 1, (5, 10, 20, 50, 100), 5, (1, 5)),
    "cm": Unit("cm", 10, 2, (0.5, 1, 2, 5, 10), 5, (0.1, 0.5)),
    "in": Unit("in", 25.4, 3, (0.25, 0.5, 1, 2, 4), 4, (1 / 16, 1 / 4)),
}
DEFAULT_UNIT = "mm"


def settings_file():
    return recent_file().with_name("settings.json")


def load_settings():
    try:
        settings = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, RecursionError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def load_unit():
    name = load_settings().get("units")
    return name if isinstance(name, str) and name in UNITS else DEFAULT_UNIT


def store_unit(name):
    target = settings_file()
    settings = {**load_settings(), "units": name}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".settings-", dir=target.parent) as directory:
            temporary = Path(directory) / target.name
            temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
    except OSError:
        # The preference is a convenience; never interrupt editing because of it.
        pass
