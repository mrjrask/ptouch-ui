"""Main window for the P-Touch label editor."""

import csv
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
from tkinter import filedialog, font, messagebox, ttk

from PIL import Image

from .document import (
    DOTS_PER_MM,
    MAX_LENGTH,
    MAX_RECORDS,
    MIN_LENGTH,
    TAPES,
    TEMPLATES,
    Element,
    dots,
    fit_position,
    load_label,
    numbered_rows,
    read_csv,
    save_label,
    template,
)
from .print_dialog import PrintDialog, background
from .printer import printer_status
from .raster import import_image, list_fonts, render, save_png
from .widgets import (
    BLUE,
    INK,
    LINE,
    MUTED,
    PANEL,
    SURFACE,
    WORKSPACE,
    Dialog,
    LabelCanvas,
    ObjectDialog,
)

PROJECT_FILES = [("P-Touch label", "*.ptlabel"), ("All files", "*")]
ZOOMS = ("Fit", "25%", "50%", "100%", "150%", "200%", "300%", "400%")
LAYER_ICONS = {
    "Text": "T",
    "Image": "▧",
    "Rectangle": "□",
    "Ellipse": "○",
    "Line": "━",
    "QR code": "▦",
    "Barcode": "▥",
}


def install_style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    family = font.nametofont("TkDefaultFont", root=root).actual("family")
    body = (family, 10)
    small = (family, 9)
    section = (family, 10, "bold")
    root.option_add("*Font", body)
    root.option_add("*TCombobox*Listbox.font", body)
    style.configure(".", background=SURFACE, foreground=INK, font=body)
    style.configure("TFrame", background=SURFACE)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Work.TFrame", background=WORKSPACE)
    for name, color, foreground, typeface in (
        ("Panel.TLabel", PANEL, INK, body),
        ("PanelMuted.TLabel", PANEL, MUTED, small),
        ("Heading.TLabel", PANEL, INK, (family, 15, "bold")),
        ("Section.TLabel", PANEL, INK, section),
        ("Error.TLabel", PANEL, "#9b381d", body),
        ("StatusReady.TLabel", PANEL, "#12664a", small),
    ):
        style.configure(name, background=color, foreground=foreground, font=typeface)
    style.configure(
        "TButton",
        padding=(9, 6),
        width=0,
        background=PANEL,
        bordercolor=LINE,
        lightcolor=PANEL,
        darkcolor=PANEL,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", "#dce6f6"), ("active", "#eaf0fa"), ("disabled", SURFACE)],
        foreground=[("disabled", "#8191a3")],
    )
    style.configure(
        "Primary.TButton",
        background=BLUE,
        foreground=PANEL,
        bordercolor=BLUE,
        darkcolor=BLUE,
        lightcolor=BLUE,
        font=section,
        padding=(14, 6),
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", "#1748ac"), ("active", "#194fbf"), ("disabled", "#879cc3")],
        foreground=[("disabled", PANEL)],
    )
    style.configure(
        "TMenubutton",
        padding=(8, 5),
        width=0,
        background=PANEL,
        bordercolor=PANEL,
        lightcolor=PANEL,
        darkcolor=PANEL,
    )
    style.configure("TEntry", padding=(6, 5), fieldbackground=PANEL, bordercolor=LINE)
    style.configure("TCombobox", padding=(5, 4), fieldbackground=PANEL, bordercolor=LINE, arrowsize=11)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL), ("disabled", SURFACE)],
        selectbackground=[("readonly", PANEL)],
        selectforeground=[("readonly", INK)],
    )
    style.configure("TSpinbox", padding=(5, 4), fieldbackground=PANEL, bordercolor=LINE, arrowsize=11)
    style.configure("Panel.TCheckbutton", background=PANEL)
    style.map("Panel.TCheckbutton", background=[("active", PANEL)])
    style.configure("TSeparator", background=LINE)
    for orientation in ("Horizontal", "Vertical"):
        style.configure(
            f"{orientation}.TScrollbar",
            background="#ced9e8",
            troughcolor=SURFACE,
            bordercolor=SURFACE,
            lightcolor="#ced9e8",
            darkcolor="#ced9e8",
            arrowcolor=MUTED,
            arrowsize=10,
            gripcount=0,
        )
    style.configure(
        "Treeview",
        rowheight=32,
        background=PANEL,
        fieldbackground=PANEL,
        bordercolor=PANEL,
        lightcolor=PANEL,
        darkcolor=PANEL,
        relief="flat",
    )
    style.map("Treeview", background=[("selected", "#e4edff")], foreground=[("selected", "#1748a8")])


class App:
    def __init__(self, root):
        self.root = root
        install_style(root)
        root.title("P-Touch UI")
        root.geometry("1240x740")
        root.minsize(900, 570)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.label = template("Equipment tag")
        self.saved = self.label
        self.path = None
        self.name = "Equipment tag"
        self.selected = None
        self.record = 0
        self.past = []
        self.future = []
        self.clipboard = None
        self.fonts = None
        self.checking = False
        self.title = tk.StringVar(root)
        self.tape = tk.StringVar(root)
        self.length = tk.StringVar(root)
        self.zoom = tk.StringVar(root, value="Fit")
        self.grid = tk.BooleanVar(root)
        self.tape_summary = tk.StringVar(root)
        self.record_title = tk.StringVar(root)
        self.feedback = tk.StringVar(root)
        self.printer_text = tk.StringVar(root, value="Printer unchecked")
        self.build()
        self.refresh()
        self.bind_keys()

    def build(self):
        root = self.root
        top = ttk.Frame(root, style="Panel.TFrame", padding=(16, 12))
        top.pack(fill="x")
        family = font.nametofont("TkDefaultFont", root=root).actual("family")
        tk.Label(
            top, text="PT", font=(family, 11, "bold"), foreground="white", background=BLUE, width=3, pady=5
        ).pack(side="left", padx=(0, 9))
        ttk.Label(top, text="P-Touch UI", style="Heading.TLabel").pack(side="left", padx=(0, 15))
        self.menu(
            top,
            "File",
            [
                ("New blank", self.new_blank, "Ctrl+N"),
                ("Open…", self.open, "Ctrl+O"),
                ("Save", self.save, "Ctrl+S"),
                ("Save as…", lambda: self.save(True), "Ctrl+Shift+S"),
                None,
                ("Export PNG…", self.export, ""),
            ],
        ).pack(side="left")
        self.menu(
            top, "Templates", [(name, lambda name=name: self.new(name), "") for name in TEMPLATES]
        ).pack(
            side="left",
        )
        self.menu(
            top,
            "Data",
            [
                ("Import CSV…", self.import_csv, ""),
                ("Generate numbers…", self.generate_rows, ""),
                ("View current fields…", self.view_fields, ""),
                ("Clear records", self.clear_rows, ""),
            ],
        ).pack(side="left")
        self.menu(
            top,
            "Edit",
            [
                ("Undo", self.undo, "Ctrl+Z"),
                ("Redo", self.redo, "Ctrl+Shift+Z"),
                None,
                ("Cut object", self.cut, "Ctrl+X"),
                ("Copy object", self.copy, "Ctrl+C"),
                ("Paste object", self.paste, "Ctrl+V"),
                ("Duplicate object", self.duplicate, "Ctrl+D"),
                ("Delete object", self.delete, "Delete"),
            ],
        ).pack(side="left")
        self.print_button = ttk.Button(
            top, text="Print...", style="Primary.TButton", command=self.print_label
        )
        self.print_button.pack(side="right", padx=(10, 0))
        self.detect_button = ttk.Button(top, text="Detect printer", command=self.detect)
        self.detect_button.pack(side="right", padx=(8, 0))
        self.printer_indicator = ttk.Label(top, textvariable=self.printer_text, style="PanelMuted.TLabel")
        self.printer_indicator.pack(side="right")
        ttk.Separator(root).pack(fill="x")

        tools = ttk.Frame(root, style="Panel.TFrame", padding=(15, 8))
        tools.pack(fill="x")
        groups = (("Text", "Image"), ("Rectangle", "Ellipse", "Line"), ("QR code", "Barcode"))
        for position, group in enumerate(groups):
            if position:
                ttk.Separator(tools, orient="vertical").pack(side="left", fill="y", padx=7)
            for kind in group:
                ttk.Button(tools, text=kind, command=lambda kind=kind: self.add(kind)).pack(
                    side="left", padx=(0, 3)
                )
        self.menu(
            tools,
            "Symbols",
            [
                (f"{character}  {name}", lambda character=character: self.add("Text", character), "")
                for character, name in (
                    ("★", "Star"),
                    ("✓", "Check"),
                    ("⚠", "Warning"),
                    ("→", "Arrow right"),
                    ("←", "Arrow left"),
                    ("●", "Circle"),
                    ("°", "Degree"),
                    ("Ω", "Omega"),
                )
            ]
            + [None, ("Insert current date", lambda: self.add("Text", "{{date}}"), "")],
        ).pack(side="left", padx=4)
        self.redo_button = ttk.Button(tools, text="Redo", command=self.redo)
        self.redo_button.pack(side="right")
        self.undo_button = ttk.Button(tools, text="Undo", command=self.undo)
        self.undo_button.pack(side="right", padx=(0, 4))
        ttk.Separator(root).pack(fill="x")

        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)
        self.build_layers(main)
        center = ttk.Frame(main, style="Work.TFrame")
        center.grid(row=0, column=2, sticky="nsew")
        document = ttk.Frame(center, style="Panel.TFrame", padding=(16, 10))
        document.pack(fill="x")
        ttk.Label(document, textvariable=self.title, style="Section.TLabel").pack(side="left")
        ttk.Label(document, textvariable=self.tape_summary, style="PanelMuted.TLabel").pack(
            side="left", padx=12
        )
        zoom = ttk.Combobox(document, textvariable=self.zoom, values=ZOOMS, state="readonly", width=6)
        zoom.pack(side="right")
        zoom.bind("<<ComboboxSelected>>", self.change_zoom)
        ttk.Label(document, text="Zoom", style="PanelMuted.TLabel").pack(side="right", padx=(12, 4))
        ttk.Checkbutton(
            document, text="Grid", variable=self.grid, style="Panel.TCheckbutton", command=self.toggle_grid
        ).pack(side="right", padx=(5, 5))
        ttk.Separator(center).pack(fill="x")
        self.canvas = LabelCanvas(center, self.select, self.change_object, self.edit_object)
        self.canvas.pack(fill="both", expand=True)
        row = ttk.Frame(center, style="Panel.TFrame", padding=(12, 7))
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.record_title, style="PanelMuted.TLabel").pack(side="left")
        self.next_record = ttk.Button(row, text=">", width=2, command=lambda: self.step_record(1))
        self.next_record.pack(side="right")
        self.previous_record = ttk.Button(row, text="<", width=2, command=lambda: self.step_record(-1))
        self.previous_record.pack(side="right", padx=4)
        self.build_inspector(main)

        ttk.Separator(root).pack(fill="x")
        status = ttk.Frame(root, style="Panel.TFrame", padding=(16, 7))
        status.pack(fill="x")
        self.status_text = ttk.Label(status, textvariable=self.feedback, style="PanelMuted.TLabel")
        self.status_text.pack(side="left", fill="x", expand=True)
        ttk.Label(status, text="Brother PT-2430PC   ·   180 dpi", style="PanelMuted.TLabel").pack(
            side="right"
        )

    @staticmethod
    def menu(parent, label, entries):
        button = ttk.Menubutton(parent, text=label)
        menu = tk.Menu(
            button,
            tearoff=False,
            background=PANEL,
            foreground=INK,
            activebackground="#e4edff",
            activeforeground=INK,
        )
        for entry in entries:
            if entry is None:
                menu.add_separator()
            else:
                name, command, shortcut = entry
                menu.add_command(label=name, command=command, accelerator=shortcut)
        button.configure(menu=menu)
        return button

    def build_layers(self, parent):
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=(12, 13), width=193)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.pack_propagate(False)
        ttk.Separator(parent, orient="vertical").grid(row=0, column=1, sticky="ns")
        ttk.Label(panel, text="Layers", style="Section.TLabel").pack(anchor="w", pady=(0, 9))
        list_area = ttk.Frame(panel, style="Panel.TFrame")
        list_area.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_area)
        scrollbar.pack(side="right", fill="y")
        self.layers = ttk.Treeview(list_area, show="tree", selectmode="browse", yscrollcommand=scrollbar.set)
        self.layers.pack(fill="both", expand=True)
        self.layers.column("#0", width=142, stretch=True)
        scrollbar.configure(command=self.layers.yview)
        self.layers.bind("<<TreeviewSelect>>", self.select_layer)
        self.layers.bind("<Double-Button-1>", lambda _event: self.edit_object())
        controls = ttk.Frame(panel, style="Panel.TFrame")
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Raise", command=lambda: self.reorder(1)).pack(side="left")
        ttk.Button(controls, text="Lower", command=lambda: self.reorder(-1)).pack(side="left", padx=(3, 0))
        ttk.Button(panel, text="Delete selected", command=self.delete).pack(fill="x", pady=(5, 0))

    def build_inspector(self, parent):
        panel = ttk.Frame(parent, style="Panel.TFrame", width=255, padding=(13, 12))
        panel.grid(row=0, column=4, sticky="nsew")
        panel.pack_propagate(False)
        ttk.Separator(parent, orient="vertical").grid(row=0, column=3, sticky="ns")
        ttk.Label(panel, text="Label setup", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        setup = ttk.Frame(panel, style="Panel.TFrame")
        setup.pack(fill="x")
        ttk.Label(setup, text="Tape", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        tape = ttk.Combobox(
            setup, textvariable=self.tape, values=[f"{t:g} mm" for t in TAPES], state="readonly", width=10
        )
        tape.grid(row=0, column=1, sticky="w", padx=8)
        tape.bind("<<ComboboxSelected>>", self.change_tape)
        ttk.Label(setup, text="Length (mm)", style="PanelMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        length = ttk.Entry(setup, textvariable=self.length, width=9)
        length.grid(row=1, column=1, sticky="w", padx=8)
        length.bind("<Return>", lambda _event: self.change_length())
        ttk.Button(setup, text="Set", command=self.change_length).grid(row=1, column=2, sticky="w")
        ttk.Separator(panel).pack(fill="x", pady=12)
        ttk.Label(panel, text="Object", style="Section.TLabel").pack(anchor="w")
        self.object_panel = ttk.Frame(panel, style="Panel.TFrame")
        self.object_panel.pack(fill="x", pady=(6, 0))
        self.no_object = ttk.Label(
            self.object_panel,
            text="Select an object to inspect its properties.",
            style="PanelMuted.TLabel",
            wraplength=206,
        )
        self.object_form = ttk.Frame(self.object_panel, style="Panel.TFrame")
        self.object_title = tk.StringVar(self.root)
        ttk.Label(self.object_form, textvariable=self.object_title, style="PanelMuted.TLabel").pack(
            anchor="w"
        )
        self.edit_button = ttk.Button(
            self.object_form, text="Edit content / style…", command=self.edit_object
        )
        self.edit_button.pack(fill="x", pady=(7, 9))
        form = ttk.Frame(self.object_form, style="Panel.TFrame")
        form.pack(fill="x")
        self.geometry = {}
        for number, (key, name) in enumerate(
            (("x", "X (mm)"), ("y", "Y (mm)"), ("width", "Width (mm)"), ("height", "Height (mm)"))
        ):
            row_index, column = divmod(number, 2)
            ttk.Label(form, text=name, style="PanelMuted.TLabel").grid(
                row=row_index * 2,
                column=column,
                sticky="w",
                padx=(0, 4),
                pady=(3, 1),
            )
            value = tk.StringVar(self.root)
            self.geometry[key] = value
            widget = ttk.Entry(form, textvariable=value, width=9)
            widget.grid(row=row_index * 2 + 1, column=column, sticky="ew", padx=(0, 7), pady=(0, 3))
            widget.bind("<Return>", lambda _event: self.apply_geometry())
            form.columnconfigure(column, weight=1)
        self.rotation = tk.StringVar(self.root)
        ttk.Label(form, text="Rotation", style="PanelMuted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(5, 0)
        )
        ttk.Combobox(
            form, textvariable=self.rotation, values=("0°", "90°", "180°", "270°"), state="readonly", width=7
        ).grid(row=5, column=0, sticky="w")
        ttk.Button(form, text="Apply", command=self.apply_geometry).grid(
            row=5, column=1, sticky="ew", padx=(0, 7)
        )
        align = self.menu(
            self.object_form,
            "Align to label",
            [
                (name, lambda axis=axis, place=place: self.align(axis, place), "")
                for name, axis, place in (
                    ("Left", "x", 0),
                    ("Center horizontally", "x", 1),
                    ("Right", "x", 2),
                    ("Top", "y", 0),
                    ("Center vertically", "y", 1),
                    ("Bottom", "y", 2),
                )
            ],
        )
        align.pack(anchor="w", pady=(8, 0))

    def refresh(self, rebuild=True, notice=None):
        self.title.set(self.name + (" *" if self.label != self.saved else ""))
        self.root.title(f"{self.title.get()} — P-Touch UI")
        self.tape.set(f"{self.label.tape:g} mm")
        self.length.set(f"{self.label.length:g}")
        self.record = min(self.record, max(0, len(self.label.rows) - 1))
        pixels = self.label.size[1]
        self.tape_summary.set(f"{self.label.tape:g} mm tape  ·  {self.label.length:g} mm long")
        self.record_title.set(
            f"Record {self.record + 1} of {len(self.label.rows)}"
            if self.label.rows
            else f"{pixels} dot printable width"
        )
        self.previous_record.state(["!disabled" if self.record > 0 else "disabled"])
        self.next_record.state(["!disabled" if self.record + 1 < len(self.label.rows) else "disabled"])
        self.undo_button.state(["!disabled" if self.past else "disabled"])
        self.redo_button.state(["!disabled" if self.future else "disabled"])
        if rebuild:
            self.layers.delete(*self.layers.get_children())
            for index in reversed(range(len(self.label.elements))):
                element = self.label.elements[index]
                content = (
                    element.value.splitlines()[0].strip()[:21]
                    if (element.value and element.kind in ("Text", "QR code", "Barcode"))
                    else element.kind
                )
                self.layers.insert("", "end", iid=str(index), text=f"{LAYER_ICONS[element.kind]}  {content}")
        self.update_selection()
        raster, issues = render(self.label, self.record)
        self.feedback.set(
            issues[0]
            if issues
            else notice
            or (
                "Add an object to start designing."
                if raster.getextrema()[0] == 255
                else "Ready to preview or print"
            )
        )
        self.status_text.configure(style="Error.TLabel" if issues else "PanelMuted.TLabel")

    def update_selection(self):
        layer = str(self.selected) if self.selected is not None else ""
        if layer and self.layers.selection() != (layer,):
            self.layers.selection_set(layer)
            self.layers.see(layer)
        elif not layer and self.layers.selection():
            self.layers.selection_remove(*self.layers.selection())
        obj = self.object
        if obj is not None:
            self.no_object.pack_forget()
            self.object_form.pack(fill="x")
            self.object_title.set(obj.kind)
            self.edit_button.state(["disabled" if obj.kind == "Line" else "!disabled"])
            for key, variable in self.geometry.items():
                variable.set(f"{getattr(obj, key) / DOTS_PER_MM:.1f}")
            self.rotation.set(f"{obj.rotation}°")
        else:
            self.object_form.pack_forget()
            self.no_object.pack(anchor="w")
        self.canvas.set_document(self.label, self.record, self.selected)

    @property
    def object(self):
        if self.selected is not None:
            return self.label.elements[self.selected]
        return None

    def select(self, index):
        if index != self.selected:
            self.selected = index
            self.update_selection()

    def select_layer(self, _event):
        selected = self.layers.selection()
        self.select(int(selected[0]) if selected else None)

    def change(self, label, selected, notice=None):
        if label != self.label:
            self.remember(self.past)
            self.future.clear()
            self.label = label
        self.selected = selected
        self.refresh(notice=notice)

    def remember(self, stack):
        stack.append((self.label, self.selected))
        del stack[:-80]

    def restore(self, source, destination):
        if source:
            self.remember(destination)
            self.label, self.selected = source.pop()
            self.refresh()

    def undo(self):
        self.restore(self.past, self.future)

    def redo(self):
        self.restore(self.future, self.past)

    def reset(self, label, name, path=None):
        self.label = self.saved = label
        self.name, self.path = name, path
        self.selected, self.record = None, 0
        self.past.clear()
        self.future.clear()
        self.refresh()

    def confirm_replacement(self):
        if self.label == self.saved:
            return True
        choice = messagebox.askyesnocancel("Save changes?", f"Save changes to {self.name}?", parent=self.root)
        return choice is False or (choice is True and self.save())

    def close(self):
        if self.confirm_replacement():
            self.root.destroy()

    def new_blank(self):
        self.new("Blank label")

    def new(self, name):
        if self.confirm_replacement():
            self.reset(template(name), name if name != TEMPLATES[0] else "Untitled label")

    def open(self):
        path = filedialog.askopenfilename(parent=self.root, title="Open label", filetypes=PROJECT_FILES)
        if not path or not self.confirm_replacement():
            return
        try:
            label = load_label(path)
        except (OSError, ValueError) as error:
            self.show_error("Could not open label", error)
        else:
            self.reset(label, Path(path).stem, Path(path))

    def save(self, save_as=False):
        path = self.path
        if path is None or save_as:
            picked = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save label",
                filetypes=PROJECT_FILES,
                initialfile=self.name,
                defaultextension=".ptlabel",
            )
            if not picked:
                return False
            path = Path(picked)
        try:
            save_label(path, self.label)
        except (OSError, ValueError) as error:
            self.show_error("Could not save label", error)
            return False
        self.saved, self.path, self.name = self.label, path, path.stem
        self.refresh(rebuild=False, notice=f"Saved {path.name}")
        return True

    def export(self):
        image, issues = render(self.label, self.record)
        if issues:
            self.show_error("Resolve label issues", "\n".join(issues))
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export printable PNG",
            initialfile=self.name,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
        )
        if path:
            try:
                save_png(image, path)
            except OSError as error:
                self.show_error("Could not export PNG", error)
            else:
                self.feedback.set(f"Exported {Path(path).name}")

    def show_error(self, title, error):
        messagebox.showerror(title, str(error), parent=self.root)

    def change_zoom(self, _event):
        zoom = self.zoom.get()
        self.canvas.zoom = None if zoom == "Fit" else int(zoom.rstrip("%")) / 100
        self.canvas.redraw()

    def toggle_grid(self):
        self.canvas.grid_enabled = self.grid.get()
        self.canvas.redraw()

    def change_tape(self, _event):
        tape = float(self.tape.get().split()[0])
        self.change(replace(self.label, tape=tape), self.selected)

    def change_length(self):
        try:
            label = replace(self.label, length=float(self.length.get()))
        except ValueError:
            self.length.set(f"{self.label.length:g}")
            self.show_error("Invalid length", f"Enter a length from {MIN_LENGTH} to {MAX_LENGTH} mm.")
        else:
            self.change(label, self.selected)

    def apply_geometry(self):
        obj = self.object
        if obj is None:
            return
        try:
            values = {key: dots(float(value.get())) for key, value in self.geometry.items()}
            rotation = int(self.rotation.get().rstrip("°"))
            if (rotation - obj.rotation) % 180 == 90 and (
                values["width"] == obj.width and values["height"] == obj.height
            ):
                values["width"], values["height"] = obj.height, obj.width
            element = replace(obj, **values, rotation=rotation)
            width, height = self.label.size
            if element.width > width or element.height > height:
                raise ValueError(
                    "Resize the object to fit the printable area before applying this rotation or size."
                )
            element = fit_position(element, self.label)
        except (ValueError, OverflowError) as error:
            self.show_error("Invalid object geometry", error)
        else:
            self.change_object(self.selected, element)

    def align(self, axis, place):
        if self.object is not None:
            width, height = self.label.size
            space = width - self.object.width if axis == "x" else height - self.object.height
            item = replace(self.object, **{axis: max(0, space * place // 2)})
            self.change_object(self.selected, item)

    def add(self, kind, value=None):
        width, height = self.label.size
        object_width = min(width, 258 if kind in ("Text", "Line") else 369 if kind == "Barcode" else 105)
        object_height = min(height, 42 if kind == "Text" else 2 if kind == "Line" else 100)
        if kind in ("QR code", "Ellipse"):
            object_height = object_width = min(object_width, object_height)
        if kind == "Image":
            picked = filedialog.askopenfilename(
                parent=self.root,
                title="Import image",
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff *.webp"),
                    ("All files", "*"),
                ],
            )
            if not picked:
                return
            try:
                value, (image_width, image_height) = import_image(picked)
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                self.show_error("Could not import image", error)
                return
            ratio = min(width / image_width, height / image_height, 1)
            object_width, object_height = (
                max(1, round(ratio * image_width)),
                max(1, round(ratio * image_height)),
            )
        element = Element(
            kind,
            (width - object_width) // 2,
            (height - object_height) // 2,
            object_width,
            object_height,
            value or "",
        )
        if value is None and kind in ("Text", "QR code", "Barcode"):
            element = self.object_dialog(element, creating=True)
        if element is not None:
            self.insert(element)

    def insert(self, element):
        index = len(self.label.elements)
        self.change(replace(self.label, elements=(*self.label.elements, element)), index)

    def object_dialog(self, element, creating=False):
        if element.kind == "Text" and self.fonts is None:
            self.fonts = list_fonts()
        dialog = ObjectDialog(self.root, element, self.fonts or (), self.label.fields(self.record), creating)
        return dialog.show(dialog.focus)

    def edit_object(self):
        if self.object is None or self.object.kind == "Line":
            return
        index = self.selected
        element = self.object_dialog(self.object)
        if element is not None:
            self.change_object(index, element)

    def change_object(self, index, element):
        items = list(self.label.elements)
        items[index] = element
        self.change(replace(self.label, elements=tuple(items)), index)

    def delete(self):
        if self.selected is not None:
            items = list(self.label.elements)
            items.pop(self.selected)
            self.change(replace(self.label, elements=tuple(items)), None)

    def reorder(self, delta):
        if self.selected is None:
            return
        items = list(self.label.elements)
        new = self.selected + delta
        if 0 <= new < len(items):
            items[new], items[self.selected] = items[self.selected], items[new]
            self.change(replace(self.label, elements=tuple(items)), new)

    def nudge(self, dx, dy):
        if self.object is not None:
            item = self.object
            x = max(-dots(MAX_LENGTH), min(dots(MAX_LENGTH), item.x + dx))
            y = max(-128, min(128, item.y + dy))
            self.change_object(self.selected, fit_position(replace(item, x=x, y=y), self.label))

    def copy(self):
        if self.object is not None:
            self.clipboard = self.object
            self.feedback.set("Object copied")

    def cut(self):
        self.copy()
        self.delete()

    def paste(self):
        if self.clipboard is not None:
            self.insert(self.offset(self.clipboard))

    def duplicate(self):
        if self.object is not None:
            self.insert(self.offset(self.object))

    def offset(self, item):
        return fit_position(
            replace(item, x=min(dots(MAX_LENGTH), item.x + dots(1)), y=min(128, item.y + dots(1))), self.label
        )

    def import_csv(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import data",
            filetypes=[
                ("CSV file", "*.csv"),
                ("All files", "*"),
            ],
        )
        if path:
            try:
                rows = read_csv(path)
            except (OSError, ValueError, csv.Error) as error:
                self.show_error("Could not import data", error)
            else:
                self.record = 0
                self.change(replace(self.label, rows=rows), self.selected, f"Imported {len(rows)} records")

    def generate_rows(self):
        dialog = Dialog(self.root, "Generate numbers")
        ttk.Label(dialog.body, text="Use {{number}} in a text or code object.", style="Panel.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
        form = ttk.Frame(dialog.body, style="Panel.TFrame")
        form.pack(fill="x")
        values = []
        for index, (name, initial) in enumerate(
            (("Start", "1"), (f"Labels (1–{MAX_RECORDS})", "10"), ("Digits (1–8)", "3"))
        ):
            ttk.Label(form, text=name, style="Panel.TLabel").grid(row=index, column=0, sticky="w", pady=4)
            value = tk.StringVar(dialog, value=initial)
            values.append(value)
            ttk.Entry(form, textvariable=value, width=14).grid(row=index, column=1, padx=(15, 0), pady=4)

        def apply():
            try:
                numbers = [int(value.get()) for value in values]
                dialog.result = numbered_rows(*numbers)
            except ValueError as error:
                dialog.error.set(
                    str(error) if not str(error).startswith("invalid literal") else "Enter whole numbers."
                )
            else:
                dialog.destroy()

        dialog.buttons("Generate", apply)
        rows = dialog.show()
        if rows:
            self.record = 0
            self.change(replace(self.label, rows=rows), self.selected, f"Generated {len(rows)} records")

    def view_fields(self):
        fields = self.label.fields(self.record)
        messagebox.showinfo(
            "Current data fields",
            "\n".join(f"{{{{{key}}}}} = {value}" for key, value in fields.items()),
            parent=self.root,
        )

    def clear_rows(self):
        self.record = 0
        self.change(replace(self.label, rows=()), self.selected)

    def step_record(self, delta):
        self.record = max(0, min(self.record + delta, len(self.label.rows) - 1))
        self.refresh(rebuild=False)

    def detect(self):
        if self.checking:
            return
        self.checking = True
        self.detect_button.state(["disabled"])
        self.print_button.state(["disabled"])
        self.printer_text.set("Checking USB…")
        self.printer_indicator.configure(style="PanelMuted.TLabel")
        messages = SimpleQueue()
        Thread(target=background, args=(printer_status, messages), daemon=True).start()
        self.root.after(70, self.poll_device, messages)

    def poll_device(self, messages):
        try:
            event, payload = messages.get_nowait()
        except Empty:
            self.root.after(70, self.poll_device, messages)
            return
        self.checking = False
        self.detect_button.state(["!disabled"])
        self.print_button.state(["!disabled"])
        if event == "done":
            self.printer_text.set(f"●  {payload.width:g} mm detected")
            self.printer_indicator.configure(style="StatusReady.TLabel")
            if payload.width in TAPES and payload.width != self.label.tape:
                self.feedback.set(
                    f"Loaded tape is {payload.width:g} mm. Set the design tape to match before printing."
                )
        else:
            self.printer_text.set("Printer unavailable")
            self.show_error("Could not detect printer", payload)

    def print_label(self):
        if self.checking:
            return
        count = PrintDialog(self.root, self.label, self.record, self.name).show()
        if count:
            self.feedback.set(f"Submitted {count} label(s) to the printer")

    def bind_keys(self):
        shortcuts = {
            "<Control-n>": self.new_blank,
            "<Control-o>": self.open,
            "<Control-s>": self.save,
            "<Control-Shift-S>": lambda: self.save(True),
            "<Control-p>": self.print_label,
            "<Control-z>": self.undo,
            "<Control-Shift-Z>": self.redo,
            "<Control-y>": self.redo,
            "<Control-x>": self.cut,
            "<Control-c>": self.copy,
            "<Control-v>": self.paste,
            "<Control-d>": self.duplicate,
            "<Delete>": self.delete,
        }
        editing = ("z", "Z", "y", "x", "c", "v", "d", "Delete")
        for sequence, action in shortcuts.items():

            def command(event, action=action):
                if event.widget.winfo_toplevel() != self.root:
                    return None
                if event.keysym in editing and event.widget.winfo_class() in (
                    "Text",
                    "Entry",
                    "TEntry",
                    "TCombobox",
                    "TSpinbox",
                ):
                    return None
                action()
                return "break"

            self.root.bind(sequence, command)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):

            def move(_event, dx=dx, dy=dy, amount=1):
                self.nudge(dx * amount, dy * amount)
                return "break"

            self.canvas.canvas.bind(f"<{key}>", move)
            self.canvas.canvas.bind(f"<Shift-{key}>", lambda event, move=move: move(event, amount=dots(1)))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
