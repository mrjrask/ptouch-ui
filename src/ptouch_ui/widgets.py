"""Reusable dialogs and the interactive label canvas."""

import tkinter as tk
from dataclasses import replace
from datetime import date
from tkinter import font, ttk

from PIL import Image, ImageTk

from .document import BARCODES, DOTS_PER_MM, DPI, MAX_FONT_SIZE, dots
from .raster import render

INK = "#1b2937"
MUTED = "#536478"
BLUE = "#235ed3"
PANEL = "#ffffff"
SURFACE = "#f2f5fa"
LINE = "#dbe3ef"
WORKSPACE = "#e8eef6"
RULER = "#637995"


# Common sizes in printer dots. At 180 dpi, 2.5 dots equal one typographic point.
FONT_SIZES = (12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96, 112, 128)


def points(size):
    """Describe a size in printer dots in typographic points."""
    return f"≈ {size * 72 / DPI:g} pt"


def step_font_size(size, direction):
    """Return the next preset size above (direction 1) or below (direction -1) ``size``."""
    if direction > 0:
        return next((preset for preset in FONT_SIZES if preset > size), min(MAX_FONT_SIZE, size + 16))
    return next((preset for preset in reversed(FONT_SIZES) if preset < size), max(1, size - 1))


class FontPicker(ttk.Combobox):
    """An editable font family list that narrows to matching families as you type."""

    def __init__(self, parent, fonts, **options):
        super().__init__(parent, values=tuple(fonts), **options)
        self.fonts = tuple(fonts)
        self.bind("<KeyRelease>", self.narrow, add="+")
        self.bind("<<ComboboxSelected>>", lambda _event: self.configure(values=self.fonts), add="+")

    def set_fonts(self, fonts):
        self.fonts = tuple(fonts)
        self.configure(values=self.fonts)

    def narrow(self, event):
        if event.keysym in ("Return", "KP_Enter", "Escape", "Tab", "Up", "Down"):
            return
        typed = self.get().strip().casefold()
        matches = [family for family in self.fonts if typed in family.casefold()] if typed else self.fonts
        self.configure(values=tuple(matches) or self.fonts)

    def family(self):
        """Return the typed family, matched to an installed name regardless of case."""
        typed = self.get().strip()
        return next((family for family in self.fonts if family.casefold() == typed.casefold()), typed)


class SizeSpinbox(ttk.Spinbox):
    """A font size field whose arrows step through common sizes; any whole size can be typed."""

    def __init__(self, parent, variable, **options):
        super().__init__(parent, textvariable=variable, from_=1, to=MAX_FONT_SIZE, **options)
        self.variable = variable
        self.bind("<<Increment>>", lambda _event: self.step(1))
        self.bind("<<Decrement>>", lambda _event: self.step(-1))

    def step(self, direction):
        try:
            current = int(self.variable.get())
        except (tk.TclError, ValueError):
            current = 28
        self.variable.set(step_font_size(current, direction))
        self.event_generate("<<SizeStepped>>")
        return "break"


class Dialog(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, style="Panel.TFrame", padding=20)
        self.body.pack(fill="both", expand=True)
        self.error = tk.StringVar(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())

    def buttons(self, action, command):
        ttk.Label(self.body, textvariable=self.error, style="Error.TLabel", wraplength=400).pack(
            fill="x",
            pady=(8, 0),
        )
        bar = ttk.Frame(self.body, style="Panel.TFrame")
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text=action, style="Primary.TButton", command=command).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=(0, 8))

    def show(self, focus=None):
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        (focus or self).focus_set()
        self.wait_window()
        return self.result


class ObjectDialog(Dialog):
    def __init__(self, parent, element, fonts, fields, creating=False):
        super().__init__(parent, ("Add " if creating else "Edit ") + element.kind.lower())
        self.element = element
        self.variables = {}
        self.focus = None
        if element.kind in ("Text", "QR code", "Barcode"):
            ttk.Label(self.body, text="Content", style="Panel.TLabel").pack(anchor="w")
            self.content = tk.Text(
                self.body,
                height=4,
                width=46,
                undo=True,
                wrap="word",
                relief="solid",
                borderwidth=1,
                padx=7,
                pady=6,
                font="TkDefaultFont",
            )
            self.content.pack(fill="x", pady=(5, 8))
            self.content.insert("1.0", "" if creating else element.value)
            self.focus = self.content
            field = tk.StringVar(self, value="Insert field…")
            picker = ttk.Combobox(
                self.body, state="readonly", values=tuple(fields), textvariable=field, width=20
            )
            picker.pack(anchor="w", pady=(0, 8))

            def insert(_event):
                self.content.insert("insert", "{{" + field.get() + "}}")
                field.set("Insert field…")
                self.content.focus_set()

            picker.bind("<<ComboboxSelected>>", insert)
        form = ttk.Frame(self.body, style="Panel.TFrame")
        form.pack(fill="x")
        if element.kind == "Text":
            family = tk.StringVar(self, value=element.font)
            self.picker = FontPicker(form, fonts, textvariable=family, width=22)
            self.control(form, "Font family", "font", family, self.picker)
            size = tk.IntVar(self, value=element.font_size)
            sizes = ttk.Frame(form, style="Panel.TFrame")
            SizeSpinbox(sizes, size, width=6).pack(side="left")
            hint = ttk.Label(sizes, style="PanelMuted.TLabel")
            hint.pack(side="left", padx=(8, 0))

            def describe(*_args):
                try:
                    hint.configure(text=points(size.get()))
                except tk.TclError:
                    hint.configure(text="")

            size.trace_add("write", describe)
            describe()
            self.control(form, "Font size (dots)", "font_size", size, sizes)
            self.choice(form, "Alignment", "align", ("left", "center", "right"))
            self.check(form, "Bold", "bold")
            self.check(form, "Italic", "italic")
            self.check(form, "Shrink text to fit", "fit")
            self.check(form, "White text on black", "inverted")
        elif element.kind == "Barcode":
            self.choice(form, "Format", "barcode", BARCODES)
            self.check(form, "Show value below bars", "caption")
        elif element.kind == "Image":
            self.entry(form, "Threshold (1–255)", "threshold")
            self.check(form, "Dither image", "dither")
        elif element.kind in ("Rectangle", "Ellipse"):
            self.entry(form, "Stroke (dots)", "stroke")
            if element.kind == "Rectangle":
                self.entry(form, "Corner radius (0–64 dots)", "radius")
            self.check(form, "Fill shape", "filled")
        self.buttons("Add object" if creating else "Apply", self.apply)

    def control(self, form, label, field, variable, widget=None):
        row = len(self.variables)
        self.variables[field] = variable
        if widget is None:
            ttk.Checkbutton(form, text=label, variable=variable, style="Panel.TCheckbutton").grid(
                row=row,
                column=0,
                columnspan=2,
                sticky="w",
                pady=4,
            )
        else:
            ttk.Label(form, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            widget.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
            form.columnconfigure(1, weight=1)
            self.focus = self.focus or widget

    def check(self, form, label, field):
        self.control(form, label, field, tk.BooleanVar(self, value=getattr(self.element, field)))

    def entry(self, form, label, field):
        variable = tk.IntVar(self, value=getattr(self.element, field))
        self.control(form, label, field, variable, ttk.Entry(form, textvariable=variable, width=18))

    def choice(self, form, label, field, values, editable=False):
        variable = tk.StringVar(self, value=getattr(self.element, field))
        combo = ttk.Combobox(
            form, textvariable=variable, values=values, state="normal" if editable else "readonly", width=22
        )
        self.control(form, label, field, variable, combo)

    def apply(self):
        try:
            changes = {key: variable.get() for key, variable in self.variables.items()}
            if self.element.kind == "Text":
                changes["font"] = self.picker.family()
            if self.element.kind in ("Text", "QR code", "Barcode"):
                changes["value"] = self.content.get("1.0", "end-1c")
            self.result = replace(self.element, **changes)
        except (ValueError, tk.TclError) as error:
            self.error.set(
                "Enter a whole number for numeric settings." if isinstance(error, tk.TclError) else str(error)
            )
        else:
            self.destroy()


class LabelCanvas(ttk.Frame):
    def __init__(self, parent, select, change, edit, context=None):
        super().__init__(parent, style="Work.TFrame")
        self.select = select
        self.change = change
        self.edit = edit
        self.context = context
        self.label = None
        self.index = 0
        self.selected = None
        self.zoom = None
        self.scale = 1
        self.origin = (0, 0)
        self.drag = None
        self.grid_enabled = False
        # While set, objects may be dragged past the label end up to this many mm.
        self.max_length = None
        self.raster_key = None
        self.raster = None
        self.issues = []
        self.ruler_font = (font.nametofont("TkDefaultFont", root=self).actual("family"), 8)
        self.photo = None
        self.canvas = tk.Canvas(self, background=WORKSPACE, highlightthickness=0, takefocus=True)
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.pointer_down)
        self.canvas.bind("<B1-Motion>", self.pointer_move)
        self.canvas.bind("<ButtonRelease-1>", self.pointer_up)
        self.canvas.bind("<Double-Button-1>", self.double_click)
        self.canvas.bind("<Button-3>", self.context_click)
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Shift-Button-4>", lambda _event: self.canvas.xview_scroll(-3, "units"))
        self.canvas.bind("<Shift-Button-5>", lambda _event: self.canvas.xview_scroll(3, "units"))

    def set_document(self, label, index, selected):
        self.drag = None
        self.label, self.index, self.selected = label, index, selected
        self.redraw()

    def box(self, element):
        left = self.origin[0] + element.x * self.scale
        top = self.origin[1] + element.y * self.scale
        return left, top, left + element.width * self.scale, top + element.height * self.scale

    def redraw(self):
        if self.label is None:
            return
        label = self.label
        if self.drag:
            index, _, _, _, element = self.drag
            items = list(label.elements)
            items[index] = element
            label = replace(label, elements=tuple(items))
        width, height = label.size
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.scale = self.zoom or min(3, max(0.08, (cw - 104) / width), max(0.08, (ch - 104) / height))
        pw, ph = max(1, round(width * self.scale)), max(1, round(height * self.scale))
        left = max(48, (cw - pw) // 2)
        top = max(52, (ch - ph) // 2)
        self.origin = left, top
        right, bottom = left + pw, top + ph
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, max(cw, right + 42), max(ch, bottom + 48)))
        self.draw_rulers(label, left, top, pw, ph)
        self.canvas.create_rectangle(left + 2, top + 4, right + 3, bottom + 5, fill="#d4deeb", outline="")
        today = date.today()
        key = label, self.index, today
        if key != self.raster_key:
            self.raster, self.issues = render(label, self.index, today)
            self.raster_key = key
        self.photo = ImageTk.PhotoImage(
            self.raster.resize((pw, ph), Image.Resampling.NEAREST), master=self.canvas
        )
        self.canvas.create_image(left, top, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(left - 1, top - 1, right, bottom, outline="#9eafc4")
        if self.grid_enabled:
            self.draw_grid(left, top, right, bottom)
        if self.selected is not None and self.selected < len(label.elements):
            bounds = self.box(label.elements[self.selected])
            self.canvas.create_rectangle(*bounds, outline=BLUE, width=2, dash=(4, 2))
            for x, y, _direction in self.corners(bounds):
                self.canvas.create_rectangle(x - 3, y - 3, x + 3, y + 3, fill=PANEL, outline=BLUE, width=1)

    def draw_rulers(self, label, left, top, pw, ph):
        canvas = self.canvas
        canvas.create_line(left, top - 17, left + pw, top - 17, fill="#9eafc4")
        canvas.create_line(left - 14, top, left - 14, top + ph, fill="#9eafc4")
        pixels_per_mm = DOTS_PER_MM * self.scale
        major = next(step for step in (5, 10, 20, 50, 100) if step * pixels_per_mm >= 32)
        tick = max(1, major // 5)
        for mm in range(0, int(label.length) + 1, tick):
            x = left + dots(mm) * self.scale
            marked = mm % major == 0
            canvas.create_line(x, top - 17, x, top - (24 if marked else 20), fill=RULER)
            if marked:
                canvas.create_text(x, top - 31, text=str(mm), fill=RULER, font=self.ruler_font)
        # The vertical ruler measures the printable height inside the physical tape.
        for mm in range(int(label.size[1] / DOTS_PER_MM) + 1):
            if pixels_per_mm < 4 and mm % 5:
                continue
            y = top + dots(mm) * self.scale
            marked = mm % 5 == 0
            canvas.create_line(left - (21 if marked else 17), y, left - 14, y, fill=RULER)
            if marked and pixels_per_mm >= 2.5:
                canvas.create_text(left - 25, y, text=str(mm), anchor="e", fill=RULER, font=self.ruler_font)
        canvas.create_text(left - 15, top - 31, text="mm", anchor="e", fill=RULER, font=self.ruler_font)

    def grid_step_mm(self):
        return 5 if DOTS_PER_MM * self.scale < 7 else 1

    def snap(self, value):
        if not self.grid_enabled:
            return value
        step = self.grid_step_mm()
        return dots(round(value / (DOTS_PER_MM * step)) * step)

    def move_edge(self, position, delta):
        return self.snap(position + delta) if delta else position

    def draw_grid(self, left, top, right, bottom):
        step = self.grid_step_mm()
        for mm in range(step, int(self.label.length) + 1, step):
            x = left + dots(mm) * self.scale
            if x >= right:
                break
            self.canvas.create_line(x, top, x, bottom, fill="#9aabc5", stipple="gray25")
        for mm in range(step, int(self.label.size[1] / DOTS_PER_MM) + 1, step):
            y = top + dots(mm) * self.scale
            if y >= bottom:
                break
            self.canvas.create_line(left, y, right, y, fill="#9aabc5", stipple="gray25")

    @staticmethod
    def corners(bounds):
        left, top, right, bottom = bounds
        return ((left, top, "nw"), (right, top, "ne"), (left, bottom, "sw"), (right, bottom, "se"))

    def point(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def hit(self, x, y):
        for index in reversed(range(len(self.label.elements))):
            left, top, right, bottom = self.box(self.label.elements[index])
            if left - 3 <= x <= right + 3 and top - 3 <= y <= bottom + 3:
                return index
        return None

    def context_click(self, event):
        self.canvas.focus_set()
        was_dragging = self.drag is not None
        self.drag = None
        self.select(self.hit(*self.point(event)))
        if was_dragging:
            self.redraw()
        if self.context is not None:
            self.context(event.x_root, event.y_root)

    def pointer_down(self, event):
        self.canvas.focus_set()
        x, y = self.point(event)
        elements = self.label.elements
        if self.selected is not None:
            item = elements[self.selected]
            for cx, cy, direction in self.corners(self.box(item)):
                if abs(x - cx) <= 7 and abs(y - cy) <= 7:
                    self.drag = self.selected, (x, y), item, direction, item
                    return
        index = self.hit(x, y)
        self.select(index)
        if index is not None:
            self.drag = index, (x, y), elements[index], "", elements[index]

    def pointer_move(self, event):
        if self.drag is None:
            return
        index, (origin_x, origin_y), original, direction, _item = self.drag
        x, y = self.point(event)
        dx, dy = round((x - origin_x) / self.scale), round((y - origin_y) / self.scale)
        if not (dx or dy):
            item = original
        elif not direction:
            width, height = self.limits()
            item = replace(
                original,
                x=max(0, min(width - original.width, self.move_edge(original.x, dx))),
                y=max(0, min(height - original.height, self.move_edge(original.y, dy))),
            )
        else:
            left, top = max(0, original.x), max(0, original.y)
            max_width, max_height = self.limits()
            right, bottom = (
                min(max_width, original.x + original.width),
                min(max_height, original.y + original.height),
            )
            left, top = min(left, max_width - 1), min(top, max_height - 1)
            right, bottom = max(left + 1, right), max(top + 1, bottom)
            if "w" in direction:
                left = max(0, min(right - 1, self.move_edge(left, dx)))
            else:
                right = max(left + 1, min(max_width, self.move_edge(right, dx)))
            if "n" in direction:
                top = max(0, min(bottom - 1, self.move_edge(top, dy)))
            else:
                bottom = max(top + 1, min(max_height, self.move_edge(bottom, dy)))
            item = replace(original, x=left, y=top, width=right - left, height=bottom - top)
        self.drag = index, (origin_x, origin_y), original, direction, item
        self.redraw()

    def limits(self):
        width, height = self.label.size
        if self.max_length is not None:
            width = max(width, dots(self.max_length))
        return width, height

    def pointer_up(self, _event):
        if self.drag is None:
            return
        index, _, previous, _, item = self.drag
        self.drag = None
        if item != previous:
            self.change(index, item)

    def double_click(self, event):
        self.drag = None
        if self.selected is not None:
            x, y = self.point(event)
            left, top, right, bottom = self.box(self.label.elements[self.selected])
            if left <= x <= right and top <= y <= bottom:
                self.edit()
