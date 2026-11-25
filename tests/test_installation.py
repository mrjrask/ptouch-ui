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
            patch("scripts.manage.check_environment"),
        ):
            active.update()
            active.update()
        self.assertEqual(1, build.call_count)
        self.assertNotEqual(previous, active.current.resolve())
        self.assertTrue((previous / "manage.py").is_file())
        self.assertEqual("second", (active.current / "revision").read_text())
        active.uninstall()
        self.assertFalse(self.root.exists())

    def test_update_repairs_missing_launchers_at_the_same_revision(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        active.links[0].unlink()
        active.desktop.unlink()
        with (
            patch("scripts.manage.run"),
            patch("scripts.manage.source_revision", return_value="first"),
            patch("scripts.manage.build_slot", side_effect=build_fake_slot),
        ):
            active.update()
        self.assertTrue(active.links[0].is_symlink())
        self.assertTrue(active.desktop.is_file())

    def test_update_repairs_an_incompatible_environment_at_the_same_revision(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        previous = active.current.resolve()
        with (
            patch("scripts.manage.run"),
            patch("scripts.manage.source_revision", return_value="first"),
            patch("scripts.manage.check_environment", side_effect=CalledProcessError(1, "python")),
            patch("scripts.manage.build_slot", side_effect=build_fake_slot),
        ):
            active.update()
        self.assertNotEqual(previous, active.current.resolve())
        self.assertEqual("first", (active.current / "revision").read_text())

    def test_interrupt_after_activation_leaves_the_new_install_intact(self):
        replace = Path.replace

        def interrupt_after_switch(path, target):
            replace(path, target)
            raise KeyboardInterrupt

        with (
            patch("scripts.manage.build_slot", side_effect=build_fake_slot),
            patch.object(Path, "replace", interrupt_after_switch),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.installation.install(self.source, "first")
        active = manage.Installation(self.root)
        self.assertTrue(all(link.is_file() for link in active.links))
        self.assertTrue(active.desktop.is_file())

    def test_uninstall_leaves_shared_backend_alone_even_with_legacy_bookkeeping(self):
        with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
            self.installation.install(self.source, "first")
        state = self.root / "ptouch-print.json"
        state.write_text('{"method": "source", "files": ["/usr/local/bin/ptouch-print"]}', encoding="utf-8")
        with patch("scripts.manage.privileged") as privileged:
            manage.Installation(self.root).uninstall()
        privileged.assert_not_called()
        self.assertFalse(self.root.exists())


class SystemPackageTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.commands = []
        patcher = patch(
            "scripts.manage.privileged", side_effect=lambda *args, **_: self.commands.append(args)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("scripts.manage.has_apt", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_installs_only_missing_app_packages(self):
        apt = manage.Apt()
        with patch.object(manage.Apt, "missing", return_value=["python3-tk"]):
            manage.ensure_app_packages(apt)
        self.assertEqual(("apt-get", "update"), self.commands[0])
        self.assertEqual("python3-tk", self.commands[1][-1])
        self.commands.clear()
        with patch.object(manage.Apt, "missing", return_value=[]):
            manage.ensure_app_packages(apt)
        self.assertEqual([], self.commands)

    def test_parses_installed_packages(self):
        output = "python3-tk installed\nfontconfig not-installed\nfonts-dejavu-core:all installed\n"
        with patch("scripts.manage.subprocess.run") as run:
            run.return_value.stdout = output
            missing = manage.Apt.missing(("python3-tk", "fontconfig", "fonts-dejavu-core", "python3-venv"))
        self.assertEqual(["fontconfig", "python3-venv"], missing)


class PtouchPrintBuildTests(TestCase):
    def test_explicit_choice_skips_the_prompt(self):
        with patch("builtins.input") as prompt:
            self.assertTrue(manage.wants_ptouch_print_build(True))
            self.assertFalse(manage.wants_ptouch_print_build(False))
        prompt.assert_not_called()

    def test_does_not_offer_when_ptouch_print_is_installed(self):
        with (
            patch("scripts.manage.shutil.which", return_value="/usr/bin/ptouch-print"),
            patch("builtins.input") as prompt,
        ):
            self.assertFalse(manage.wants_ptouch_print_build(None))
        prompt.assert_not_called()

    def test_does_not_prompt_without_a_terminal(self):
        with (
            patch("scripts.manage.shutil.which", return_value=None),
            patch("scripts.manage.sys.stdin.isatty", return_value=False),
            patch("builtins.input") as prompt,
        ):
            self.assertFalse(manage.wants_ptouch_print_build(None))
        prompt.assert_not_called()

    def test_prompt_defaults_to_yes(self):
        for answer, expected in (("", True), ("y", True), ("YES", True), ("n", False), ("no", False)):
            with (
                patch("scripts.manage.shutil.which", return_value=None),
                patch("scripts.manage.sys.stdin.isatty", return_value=True),
                patch("builtins.input", return_value=answer),
            ):
                self.assertEqual(expected, manage.wants_ptouch_print_build(None), answer)

    def test_build_passes_install_and_package_options(self):
        source = Path("/src")
        with patch("scripts.manage.subprocess.run") as run:
            manage.build_ptouch_print(source, skip_system_packages=False)
            manage.build_ptouch_print(source, skip_system_packages=True)
        self.assertEqual(["/src/build-ptouch-print.sh", "--install"], run.call_args_list[0].args[0])
        self.assertEqual(
            ["/src/build-ptouch-print.sh", "--install", "--skip-system-packages"],
            run.call_args_list[1].args[0],
        )
