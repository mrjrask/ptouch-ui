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

    def test_keeps_an_existing_ptouch_print(self):
        with patch("scripts.manage.find_ptouch_print", return_value="/usr/bin/ptouch-print"):
            manage.ensure_ptouch_print(self.root, manage.Apt())
        self.assertEqual([], self.commands)
        self.assertFalse((self.root / manage.PTOUCH_PRINT_STATE).exists())
        manage.remove_ptouch_print(self.root)
        self.assertEqual([], self.commands)

    def test_installs_and_removes_the_distribution_package(self):
        apt = manage.Apt()
        with (
            patch("scripts.manage.find_ptouch_print", return_value=None),
            patch.object(manage.Apt, "candidate", return_value=True),
            patch.object(manage.Apt, "missing", return_value=["ptouch-print"]),
        ):
            manage.ensure_ptouch_print(self.root, apt)
        self.assertEqual("ptouch-print", self.commands[-1][-1])
        self.commands.clear()
        manage.remove_ptouch_print(self.root)
        self.assertEqual(("apt-get", "remove", "-y", "ptouch-print"), self.commands[0][2:])
        self.assertFalse((self.root / manage.PTOUCH_PRINT_STATE).exists())

    def test_builds_from_source_and_removes_only_recorded_system_files(self):
        files = [
            "/usr/local/bin/ptouch-print",
            "/usr/local/share/man/man1/ptouch-print.1",
            "/etc/udev/rules.d/20-usb-ptouch-permissions.rules",
            "/home/user/.bashrc",
            "/usr/local/../../etc/passwd",
        ]
        with (
            patch("scripts.manage.find_ptouch_print", return_value=None),
            patch.object(manage.Apt, "candidate", return_value=False),
            patch.object(manage.Apt, "missing", return_value=[]),
            patch("scripts.manage.build_ptouch_print", return_value=files),
        ):
            manage.ensure_ptouch_print(self.root, manage.Apt())
        with patch("scripts.manage.reload_udev") as reload_udev:
            manage.remove_ptouch_print(self.root)
        self.assertEqual([("rm", "-f", "--", *files[:3])], self.commands)
        reload_udev.assert_called_once()

    def test_uninstall_removes_ptouch_print_unless_kept(self):
        for keep, expected in ((False, 1), (True, 0)):
            with self.subTest(keep=keep):
                root = self.root / f"keep-{keep}" / "ptouch-ui"
                root.parent.mkdir()
                with patch("scripts.manage.build_slot", side_effect=build_fake_slot):
                    manage.Installation(root, root.parent / "bin").install(root.parent, "first")
                state = {"method": "source", "files": ["/usr/local/bin/ptouch-print"]}
                (root / manage.PTOUCH_PRINT_STATE).write_text(manage.json.dumps(state))
                self.commands.clear()
                manage.Installation(root).uninstall(keep_ptouch_print=keep)
                self.assertEqual(expected, len(self.commands))
                self.assertFalse(root.exists())

    def test_source_build_keeps_existing_udev_rules_and_stays_in_system_locations(self):
        rules = self.root / "rules.d"
        rules.mkdir()
        existing = rules / "20-usb-ptouch-permissions.rules"
        existing.write_text("user rule", encoding="utf-8")

        def fake_run(*arguments, **_):
            if arguments[:2] == ("git", "clone"):
                udev = Path(arguments[-1]) / "udev"
                udev.mkdir(parents=True)
                (udev / existing.name).write_text("upstream rule", encoding="utf-8")
                (udev / "30-new.rules").write_text("upstream rule", encoding="utf-8")

        def fake_install(arguments, env, **_):
            stage = Path(env["DESTDIR"])
            for target in ("/usr/local/bin/ptouch-print", f"{existing}", "/usr/share/other"):
                path = stage / target.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("staged", encoding="utf-8")

        with (
            patch("scripts.manage.UDEV_RULES", rules),
            patch("scripts.manage.run", side_effect=fake_run),
            patch("scripts.manage.subprocess.run", side_effect=fake_install),
            patch("scripts.manage.reload_udev"),
        ):
            files = manage.build_ptouch_print()
        self.assertEqual([str(rules / "30-new.rules"), "/usr/local/bin/ptouch-print"], files)
        self.assertEqual(
            [str(rules / "30-new.rules"), "/usr/local/bin/ptouch-print"], [str(c[-1]) for c in self.commands]
        )
        self.assertEqual("user rule", existing.read_text())

    def test_failed_source_install_removes_the_files_it_copied(self):
        def fake_install(arguments, env, **_):
            for target in ("/usr/local/bin/ptouch-print", "/usr/local/share/man/man1/ptouch-print.1"):
                path = Path(env["DESTDIR"]) / target.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("staged", encoding="utf-8")

        def fail_on_man_page(*arguments, **_):
            self.commands.append(arguments)
            if arguments[0] == "install" and str(arguments[-1]).endswith(".1"):
                raise CalledProcessError(1, "install")

        with (
            patch("scripts.manage.privileged", side_effect=fail_on_man_page),
            patch("scripts.manage.run"),
            patch("scripts.manage.subprocess.run", side_effect=fake_install),
            self.assertRaises(CalledProcessError),
        ):
            manage.build_ptouch_print()
        self.assertEqual(
            ("rm", "-f", "--", "/usr/local/bin/ptouch-print", "/usr/local/share/man/man1/ptouch-print.1"),
            self.commands[-1],
        )

    def test_does_not_claim_an_already_installed_distribution_package(self):
        with (
            patch("scripts.manage.find_ptouch_print", return_value=None),
            patch.object(manage.Apt, "candidate", return_value=True),
            patch.object(manage.Apt, "missing", return_value=[]),
        ):
            manage.ensure_ptouch_print(self.root, manage.Apt())
        self.assertFalse((self.root / manage.PTOUCH_PRINT_STATE).exists())
