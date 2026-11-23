import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

from ptouch_ui.document import numbered_rows


@skipUnless(os.environ.get("DISPLAY"), "Run the desktop smoke test with an X11 display.")
class DesktopTests(TestCase):
    def test_add_drag_undo_and_prepare_a_print_job(self):
        import tkinter as tk
        from dataclasses import replace
        from types import SimpleNamespace

        from ptouch_ui.app import App
        from ptouch_ui.print_dialog import PrintDialog

        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        root.update()
        self.assertIn("24 mm tape", app.tape_summary.get())
        app.new_blank()
        app.add("Text", "BIN {{number}}")
        app.change(replace(app.label, rows=numbered_rows(1, 2, 2)), app.selected)
        root.update()
        original = app.object
        left, top, right, bottom = app.canvas.box(original)
        start = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        end = SimpleNamespace(x=start.x + app.canvas.scale * 10, y=start.y)
        app.canvas.pointer_down(start)
        app.canvas.pointer_move(end)
        app.canvas.pointer_up(end)
        self.assertEqual(original.x + 10, app.object.x)
        app.undo()
        self.assertEqual(original, app.object)
        app.redo()
        self.assertEqual(original.x + 10, app.object.x)

        dialog = PrintDialog(root, app.label, 0, app.name)
        dialog.records.set("All (2)")
        dialog.count.set("2")
        dialog.mirror.set(True)
        dialog.after(5000, dialog.destroy)
        with patch("ptouch_ui.print_dialog.print_images") as mock_print:
            dialog.after(50, dialog.send)
            self.assertEqual(4, dialog.show())
        images, tape, options, _ = mock_print.call_args.args
        self.assertEqual(12, tape)
        self.assertEqual(2, options.copies)
        self.assertNotEqual(images[0].tobytes(), images[1].tobytes())
        self.assertTrue(all(image.size == app.label.size for image in images))

    def test_snap_context_menu_fit_length_and_open_from_command_line(self):
        import tkinter as tk
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace

        from ptouch_ui import app as app_module
        from ptouch_ui.document import Element, Label, dots, save_label

        with TemporaryDirectory() as directory:
            path = Path(directory) / "Shelf.ptlabel"
            save_label(path, Label(length=120, elements=(Element("Rectangle", 3, 3, 50, 40),)))
            created = []

            class RecordingApp(app_module.App):
                def __init__(self, root):
                    super().__init__(root)
                    created.append(self)

            with (
                patch.object(app_module, "App", RecordingApp),
                patch.object(tk.Tk, "mainloop", lambda _root: None),
            ):
                app_module.main([str(path)])
        app = created[0]
        root = app.root
        self.addCleanup(root.destroy)
        self.assertEqual("Shelf", app.name)
        root.update()

        app.grid.set(True)
        app.toggle_grid()
        app.select(0)
        app.canvas.zoom = 1
        app.canvas.redraw()
        root.update()
        left, top, right, bottom = app.canvas.box(app.object)
        start = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        end = SimpleNamespace(x=start.x + app.canvas.scale * 9, y=start.y)
        app.canvas.pointer_down(start)
        app.canvas.pointer_move(end)
        app.canvas.pointer_up(end)
        self.assertEqual(dots(2), app.object.x)
        self.assertEqual(3, app.object.y)
        self.assertEqual(dots(200), app.canvas.snap(dots(200)))

        with patch.object(app.context_menu, "tk_popup") as popup:
            app.canvas.context_click(SimpleNamespace(x=start.x, y=start.y, x_root=5, y_root=6))
        popup.assert_called_once_with(5, 6)
        self.assertEqual(0, app.selected)
        states = {
            app.context_menu.entrycget(index, "label"): app.context_menu.entrycget(index, "state")
            for index in range(app.context_menu.index("end") + 1)
            if app.context_menu.type(index) == "command"
        }
        self.assertEqual("normal", states["Delete"])
        self.assertEqual("disabled", states["Paste"])

        app.fit_length()
        self.assertLess(app.label.length, 120)
        self.assertGreaterEqual(dots(app.label.length), app.object.x * 2 + app.object.width)
        app.undo()
        self.assertEqual(120, app.label.length)

    def test_deleting_during_a_drag_cancels_the_pending_edit(self):
        import tkinter as tk
        from types import SimpleNamespace

        from ptouch_ui.app import App
        from ptouch_ui.document import Element, Label

        root = tk.Tk()
        self.addCleanup(root.destroy)
        app = App(root)
        element = Element("Rectangle", 10, 10, 100, 40)
        app.reset(Label(elements=(element,)), "Drag test")
        root.update()
        left, top, right, bottom = app.canvas.box(element)
        start = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        end = SimpleNamespace(x=start.x + 30, y=start.y)
        app.canvas.pointer_down(start)
        app.canvas.pointer_move(end)
        app.delete()
        app.canvas.pointer_up(end)
        self.assertEqual((), app.label.elements)
        self.assertIsNone(app.canvas.drag)
        app.undo()
        self.assertEqual((element,), app.label.elements)

    def test_unexpected_worker_failure_still_notifies_the_dialog(self):
        from queue import SimpleQueue
        from unittest.mock import Mock

        from ptouch_ui.print_dialog import background

        messages = SimpleQueue()
        with self.assertLogs("ptouch_ui.print_dialog", level="ERROR"):
            background(Mock(side_effect=RuntimeError("unexpected failure")), messages)
        event, message = messages.get_nowait()
        self.assertEqual("error", event)
        self.assertIn("stopped the printer operation", message)
