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
