"""Recently opened or saved label documents, kept in the user's config directory."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

LIMIT = 8


def recent_file():
    base = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    if not base.is_absolute():
        base = Path.home() / ".config"
    return base / "ptouch-ui" / "recent.json"


def load_recent():
    try:
        paths = json.loads(recent_file().read_text(encoding="utf-8"))
    except (OSError, RecursionError, ValueError):
        return []
    if not isinstance(paths, list):
        return []
    return [path for path in paths if isinstance(path, str) and Path(path).is_absolute()][:LIMIT]


def store_recent(paths):
    target = recent_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".recent-", dir=target.parent) as directory:
            temporary = Path(directory) / target.name
            temporary.write_text(json.dumps(paths[:LIMIT], indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
    except OSError:
        # The list is a convenience; never block opening or saving a label because of it.
        pass


def remember_recent(path):
    try:
        path = str(Path(path).resolve())
    except (OSError, RuntimeError, ValueError):
        return load_recent()
    paths = [path, *(item for item in load_recent() if item != path)][:LIMIT]
    store_recent(paths)
    return paths


def forget_recent(path):
    paths = [item for item in load_recent() if item != str(path)]
    store_recent(paths)
    return paths
