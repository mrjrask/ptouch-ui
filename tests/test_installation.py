import os
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import manage


def build_fake_slot(_source, slot, revision):
    (slot / "manage.py").write_text("# manager", encoding="utf-8")
    (slot / "revision").write_text(revision, encoding="utf-8")


class InstallationTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.root = self.directory / "share" / "ptouch-ui"
        self.root.parent.mkdir()
        self.source = self.directory / "checkout"
        self.bin_dir = self.directory / "local bin"
        self.installation = manage.Installation(self.root, self.bin_dir)

    def test_install_then_remove_only_owned_files(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        self.assertEqual(self.bin_dir, active.bin_dir)
        self.assertEqual(
            f'Exec="{self.bin_dir / "ptouch-ui"}"',
            next(line for line in active.desktop.read_text().splitlines() if line.startswith("Exec=")),
        )
        self.assertTrue(all(link.resolve().is_file() for link in active.links))

        label = self.root / "Saved label.ptlabel"
        label.write_text("user document", encoding="utf-8")
        launcher = active.links[1]
        launcher.unlink()
        launcher.write_text("user command", encoding="utf-8")
        active.uninstall()
        self.assertFalse(active.current.exists())
        self.assertFalse(active.desktop.exists())
        self.assertFalse(active.links[0].is_symlink())
        self.assertFalse((self.root / "versions").exists())
        self.assertEqual("user document", label.read_text())
        self.assertEqual("user command", launcher.read_text())

    def test_rejects_a_conflicting_command(self):
        self.bin_dir.mkdir()
        command = self.installation.links[0]
        command.write_text("other installation", encoding="utf-8")
        with self.assertRaisesRegex(manage.InstallError, "Another file"):
            self.installation.install(self.source, "first")
        self.assertEqual("other installation", command.read_text())
        self.assertFalse(self.root.exists())

    def test_failed_first_install_leaves_no_launchers_or_installation(self):
        with (
            patch("scripts.manage.build_slot", side_effect=CalledProcessError(1, "pip")),
            self.assertRaises(CalledProcessError),
        ):
            self.installation.install(self.source, "first")
        self.assertFalse(self.root.exists())
        self.assertTrue(all(not os.path.lexists(link) for link in self.installation.links))

    def test_update_keeps_active_version_when_a_build_fails(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        previous = active.current.resolve()
        with (
            patch("scripts.manage.run") as run,
            patch("scripts.manage.source_revision", return_value="second"),
            patch("scripts.manage.build_slot", side_effect=CalledProcessError(1, "pip")),
            self.assertRaises(CalledProcessError),
        ):
            active.update()
        self.assertIn(manage.REPOSITORY, run.call_args.args)
        self.assertEqual(previous, active.current.resolve())
        self.assertTrue(active.target.is_file())
        self.assertEqual([previous], list((self.root / "versions").iterdir()))

    def test_update_switches_versions_and_skips_the_same_revision(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        previous = active.current.resolve()
        with (
            patch("scripts.manage.run"),
            patch("scripts.manage.source_revision", return_value="second"),
            patch("scripts.manage.build_slot", side_effect=build_fake_slot) as build,
        ):
            active.update()
            active.update()
        self.assertEqual(1, build.call_count)
        self.assertNotEqual(previous, active.current.resolve())
        self.assertTrue((previous / "manage.py").is_file())
        self.assertEqual("second", (active.current / "revision").read_text())
        active.uninstall()
        self.assertFalse(self.root.exists())
