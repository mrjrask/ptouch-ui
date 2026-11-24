#!/usr/bin/env python3
"""Install, update, and remove a per-user desktop installation."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

REPOSITORY = "https://github.com/mrjrask/ptouch-ui.git"
COMMANDS = ("ptouch-ui", "ptouch-ui-update", "ptouch-ui-uninstall")
DESKTOP_ID = "io.github.mrjrask.PtouchUI.desktop"
SLOT_MARKER = ".ptouch-ui-slot"
# Packages the app needs at runtime on Debian, Ubuntu, and Raspberry Pi OS.
APP_PACKAGES = ("python3-tk", "python3-venv", "fontconfig", "fonts-dejavu-core")


class InstallError(Exception):
    pass


def home_path(variable, default):
    value = Path(os.environ.get(variable, ""))
    return value if value.is_absolute() else Path.home() / default


def default_root():
    return home_path("XDG_DATA_HOME", ".local/share") / "ptouch-ui"


@contextmanager
def install_lock(root):
    root.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def run(*arguments, capture=False):
    return subprocess.run(arguments, text=True, check=True, stdout=subprocess.PIPE if capture else None)


def has_apt():
    return bool(shutil.which("apt-get") and shutil.which("dpkg-query"))


def privileged(*arguments):
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            raise InstallError(f"Install sudo, or run as root: {' '.join(map(str, arguments))}")
        arguments = ("sudo", *arguments)
    print("Running:", " ".join(map(str, arguments)), flush=True)
    return subprocess.run(arguments, text=True, check=True)


class Apt:
    """Installs Debian packages with sudo, refreshing package lists once per run."""

    def __init__(self):
        self.updated = False

    def update(self):
        if not self.updated:
            privileged("apt-get", "update")
            self.updated = True

    @staticmethod
    def missing(packages):
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package} ${db:Status-Status}\n", *packages],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        installed = {
            line.split()[0].split(":")[0]
            for line in result.stdout.splitlines()
            if line.split()[1:] == ["installed"]
        }
        return [package for package in packages if package not in installed]

    def install(self, packages):
        missing = self.missing(packages)
        if missing:
            print(f"Installing system packages: {' '.join(missing)}", flush=True)
            self.update()
            privileged(
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                *missing,
            )
        return missing


def ensure_app_packages(apt):
    if has_apt():
        apt.install(APP_PACKAGES)
    else:
        print("apt-get was not found. Install Tk for Python, venv, and Fontconfig yourself.")


def source_revision(source):
    if not shutil.which("git") or not (source / ".git").exists():
        return ""
    if run("git", "-C", source, "status", "--porcelain", capture=True).stdout:
        return ""
    return run("git", "-C", source, "rev-parse", "HEAD", capture=True).stdout.strip()


def desktop_entry(launcher):
    # Desktop-entry string and Exec quoting are two separate escaping layers.
    value = str(launcher)
    if any(character in value for character in "\n\r\t"):
        raise InstallError("The command directory cannot contain tabs or line breaks.")
    quoted = "".join("\\" + char if char in '\\"$`' else char for char in value)
    quoted = quoted.replace("\\", "\\\\").replace("%", "%%")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=P-Touch UI\n"
        "Comment=Design and print labels\n"
        f'Exec="{quoted}"\n'
        "Icon=printer\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def owns_link(path, target):
    return path.is_symlink() and os.readlink(path) == str(target)


def owns_desktop(path, content):
    return path.is_file() and not path.is_symlink() and path.read_bytes() == content.encode("utf-8")


class Installation:
    def __init__(self, root, bin_dir=None):
        self.root = root
        self.current = root / "current"
        self.manifest = root / "install.json"
        self.desktop = root.parent / "applications" / DESKTOP_ID
        if self.manifest.is_file() and not self.manifest.is_symlink() and not root.is_symlink():
            state = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("format") != 1:
                raise InstallError(f"Unrecognized installation at {root}.")
            path = state.get("bin_dir")
            if not isinstance(path, str):
                raise InstallError(f"Invalid command directory in {self.manifest}.")
            self.bin_dir = Path(path)
            if not self.bin_dir.is_absolute():
                raise InstallError(f"Invalid command directory in {self.manifest}.")
            self.exists = True
        else:
            if os.path.lexists(root):
                raise InstallError(f"{root} exists and is not a P-Touch UI installation.")
            self.bin_dir = bin_dir or home_path("XDG_BIN_HOME", ".local/bin")
            self.exists = False
        self.target = self.current / "manage.py"
        self.links = [self.bin_dir / name for name in COMMANDS]
        self.desktop_content = desktop_entry(self.links[0])

    def check_links(self):
        for link in self.links:
            if os.path.lexists(link) and not owns_link(link, self.target):
                raise InstallError(f"Another file already uses {link}.")
        if os.path.lexists(self.desktop) and (
            not self.exists or not self.desktop.is_file() or self.desktop.is_symlink()
        ):
            raise InstallError(f"Another file already uses {self.desktop}.")

    def register(self, created):
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for link in self.links:
            if not os.path.lexists(link):
                link.symlink_to(self.target)
                created.append(link)
        if not os.path.lexists(self.desktop):
            self.desktop.parent.mkdir(parents=True, exist_ok=True)
            with self.desktop.open("x", encoding="utf-8") as file:
                created.append(self.desktop)
                file.write(self.desktop_content)

    def install(self, source, revision):
        self.check_links()
        if not self.exists:
            self.root.mkdir()
        slot = None
        created = []
        next_current = None
        try:
            if not self.exists:
                self.manifest.write_text(
                    json.dumps({"format": 1, "bin_dir": str(self.bin_dir)}) + "\n", encoding="utf-8"
                )
            versions = self.root / "versions"
            versions.mkdir(exist_ok=True)
            slot = Path(mkdtemp(prefix="install-", dir=versions))
            (slot / SLOT_MARKER).touch()
            build_slot(source, slot, revision)
            self.register(created)
            next_current = self.root / (slot.name + ".link")
            next_current.symlink_to(slot.relative_to(self.root), target_is_directory=True)
            next_current.replace(self.current)
        except BaseException:
            if slot is not None and owns_link(self.current, slot.relative_to(self.root)):
                # The atomic switch completed before we received the interruption.
                raise
            for path in reversed(created):
                path.unlink(missing_ok=True)
            if next_current is not None:
                next_current.unlink(missing_ok=True)
            if not self.exists:
                shutil.rmtree(self.root)
            elif slot is not None:
                shutil.rmtree(slot)
            raise
        print(f"Installed P-Touch UI. Launch from the desktop menu or run {self.links[0]}.")

    def update(self):
        if not self.exists:
            raise InstallError("P-Touch UI is not installed. Run ./install.sh first.")
        if not shutil.which("git"):
            raise InstallError("Install Git to check for P-Touch UI updates.")
        with TemporaryDirectory(prefix="ptouch-ui-update-") as directory:
            source = Path(directory) / "source"
            run(
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                "main",
                REPOSITORY,
                source,
            )
            revision = source_revision(source)
            saved_revision = self.current / "revision"
            if (
                revision
                and saved_revision.is_file()
                and saved_revision.read_text(encoding="utf-8") == revision
                and self.ready()
            ):
                print(f"P-Touch UI is already up to date ({revision[:8]}).")
                return
            self.install(source, revision)

    def ready(self):
        if not self.target.is_file() or not self.desktop.is_file() or self.desktop.is_symlink():
            return False
        if not all(owns_link(link, self.target) for link in self.links):
            return False
        try:
            check_environment(self.current / "venv" / "bin" / "python", quiet=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        return True

    def uninstall(self):
        if not self.exists:
            print("P-Touch UI is already uninstalled.")
            return
        # Discard old app bookkeeping without touching the shared printer backend.
        legacy_state = self.root / "ptouch-print.json"
        if legacy_state.is_file() or legacy_state.is_symlink():
            legacy_state.unlink()
        for link in self.links:
            if owns_link(link, self.target):
                link.unlink()
        if owns_desktop(self.desktop, self.desktop_content):
            self.desktop.unlink()
        if self.current.is_symlink():
            self.current.unlink()
        versions = self.root / "versions"
        if versions.is_dir() and not versions.is_symlink():
            for slot in versions.iterdir():
                if (
                    slot.name.startswith("install-")
                    and not slot.is_symlink()
                    and (slot / SLOT_MARKER).is_file()
                ):
                    shutil.rmtree(slot)
            if not any(versions.iterdir()):
                versions.rmdir()
        if list(self.root.iterdir()) == [self.manifest]:
            self.manifest.unlink()
            self.root.rmdir()
        else:
            print(f"Kept other files in {self.root}.")
        print("P-Touch UI was uninstalled. Saved labels are still in their original locations.")


def check_environment(python, *, quiet=False):
    subprocess.run(
        [python, "-I", "-c", "import ptouch_ui.app"],
        check=True,
        capture_output=quiet,
    )


def build_slot(source, slot, revision):
    if sys.version_info < (3, 10):
        raise InstallError("P-Touch UI needs Python 3.10 or newer.")
    try:
        import tkinter  # noqa: F401
    except ImportError as error:
        raise InstallError("Install Tk for Python (python3-tk on Ubuntu or Debian).") from error
    if not shutil.which("fc-match"):
        raise InstallError("Install Fontconfig before installing P-Touch UI.")
    venv = slot / "venv"
    try:
        run(sys.executable, "-m", "venv", venv)
    except subprocess.CalledProcessError as error:
        raise InstallError(
            "Could not create a virtual environment. Check that python3-venv is installed."
        ) from error
    python = venv / "bin" / "python"
    with TemporaryDirectory(prefix="ptouch-ui-source-") as directory:
        package = Path(directory)
        for filename in ("pyproject.toml", "README.md"):
            shutil.copy2(source / filename, package)
        shutil.copytree(
            source / "src", package / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")
        )
        run(python, "-m", "pip", "--disable-pip-version-check", "install", "--no-input", package)
    check_environment(python)
    shutil.copy2(source / "scripts" / "manage.py", slot / "manage.py")
    (slot / "manage.py").chmod(0o755)
    (slot / "revision").write_text(revision, encoding="utf-8")


def main():
    command = Path(sys.argv[0]).name
    installed = command in COMMANDS
    if command == COMMANDS[0]:
        python = Path(__file__).resolve().parent / "venv" / "bin" / "python"
        os.execv(python, [str(python), "-m", "ptouch_ui", *sys.argv[1:]])

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "update", "uninstall"))
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument(
        "--skip-system-packages",
        action="store_true",
        help="install: skip installing system prerequisites with sudo",
    )
    parser.add_argument(
        "--keep-ptouch-print",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args([command.removeprefix("ptouch-ui-"), *sys.argv[1:]] if installed else None)
    if (args.operation == "install") != (args.source is not None):
        parser.error("Pass the source directory when installing.")
    if os.geteuid() == 0:
        parser.error("Run this per-user installer as your desktop user.")
    root = Path(__file__).resolve().parents[2] if installed else default_root()
    try:
        with install_lock(root):
            installation = Installation(root)
            if args.operation == "install":
                apt = Apt()
                if not args.skip_system_packages:
                    ensure_app_packages(apt)
                source = args.source.resolve()
                installation.install(source, source_revision(source))
            elif args.operation == "update":
                installation.update()
            else:
                installation.uninstall()
    except (InstallError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"P-Touch UI {args.operation} failed. {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
