"""Print preview, options, and job progress."""

import logging
import tkinter as tk
from datetime import date
from queue import Empty, SimpleQueue
from threading import Thread
from tkinter import messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from .document import MAX_RECORDS
from .printer import PrinterError, PrintOptions, print_images
from .raster import render
from .widgets import Dialog

LOGGER = logging.getLogger(__name__)


def background(operation, messages):
    try:
        messages.put(("done", operation()))
    except (PrinterError, OSError, ValueError) as error:
        messages.put(("error", str(error)))
    except Exception:
        LOGGER.exception("Unexpected failure in background printer operation")
        messages.put(
            ("error", "An unexpected error stopped the printer operation. Check the tape before retrying.")
        )


class PrintDialog(Dialog):
    def __init__(self, parent, label, current_index, title):
        super().__init__(parent, "Print labels")
        self.label = label
        self.current_index = current_index
        self.today = date.today()
        self.running = False
        self.photo = None
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        ttk.Label(self.body, text=title, style="Heading.TLabel").pack(anchor="w")
        self.summary = tk.StringVar(self)
        ttk.Label(self.body, textvariable=self.summary, style="PanelMuted.TLabel").pack(
            anchor="w", pady=(2, 10)
        )
        self.preview = tk.Label(self.body, background="#e8eef6", width=500, height=120)
        self.preview.pack(fill="x", pady=(0, 12))
        grid = ttk.Frame(self.body, style="Panel.TFrame")
        grid.pack(fill="x")
        self.count = tk.StringVar(self, value="1")
        self.mode = tk.StringVar(self, value="Separate labels")
        self.records = tk.StringVar(self, value=f"Current ({current_index + 1})" if label.rows else "Current")
        self.feed = tk.StringVar(self)
        self.serial = tk.StringVar(self)
        self.mirror = tk.BooleanVar(self)
        self.controls = []
        self.add_option(
            grid, 0, "Copies per label", ttk.Spinbox(grid, from_=1, to=99, textvariable=self.count, width=13)
        )
        self.add_option(
            grid,
            1,
            "Output",
            ttk.Combobox(
                grid,
                state="readonly",
                textvariable=self.mode,
                values=("Separate labels", "Single strip with cut marks"),
                width=29,
            ),
        )
        if label.rows:
            self.add_option(
                grid,
                2,
                "Records",
                ttk.Combobox(
                    grid,
                    state="readonly",
                    textvariable=self.records,
                    values=(self.records.get(), f"All ({len(label.rows)})"),
                    width=29,
                ),
            )
        self.add_option(grid, 3, "Feed dots (optional)", ttk.Entry(grid, textvariable=self.feed, width=15))
        self.add_option(grid, 4, "USB serial (optional)", ttk.Entry(grid, textvariable=self.serial, width=24))
        mirror = ttk.Checkbutton(
            grid, text="Mirror artwork", variable=self.mirror, style="Panel.TCheckbutton"
        )
        mirror.grid(row=5, column=1, sticky="w", pady=5)
        self.controls.append(mirror)
        ttk.Label(self.body, textvariable=self.error, style="Error.TLabel", wraplength=485).pack(
            fill="x",
            pady=(6, 0),
        )
        self.progress = ttk.Progressbar(self.body, mode="determinate")
        bar = ttk.Frame(self.body, style="Panel.TFrame")
        bar.pack(fill="x", pady=(10, 0))
        self.submit = ttk.Button(bar, text="Print", style="Primary.TButton", command=self.send)
        self.submit.pack(side="right")
        self.cancel = ttk.Button(bar, text="Cancel", command=self.close)
        self.cancel.pack(side="right", padx=(0, 8))
        self.count.trace_add("write", self.update_preview)
        self.records.trace_add("write", self.update_preview)
        self.mirror.trace_add("write", self.update_preview)
        self.update_preview()

    def add_option(self, grid, row, title, widget):
        ttk.Label(grid, text=title, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        widget.grid(row=row, column=1, sticky="w", padx=(16, 0), pady=4)
        self.controls.append(widget)

    @property
    def indices(self):
        return range(len(self.label.rows)) if self.records.get().startswith("All") else [self.current_index]

    def update_preview(self, *_):
        indices = self.indices
        image, issues = render(self.label, indices[0], self.today)
        if self.mirror.get():
            image = ImageOps.mirror(image)
        scale = min(2, 474 / image.width, 100 / image.height)
        size = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        self.photo = ImageTk.PhotoImage(image.resize(size, Image.Resampling.NEAREST), master=self)
        self.preview.configure(image=self.photo)
        try:
            count = int(self.count.get()) * len(indices)
        except ValueError:
            count = 0
        self.summary.set(
            f"{self.label.length:g} mm long  ·  {self.label.tape:g} mm tape  ·  {count} label(s)"
        )
        self.error.set(issues[0] if issues else "")

    def close(self):
        if not self.running:
            self.destroy()

    def send(self):
        if self.running:
            return
        try:
            options = PrintOptions(
                copies=int(self.count.get()),
                strip=self.mode.get().startswith("Single"),
                mirror=self.mirror.get(),
                feed=int(self.feed.get()) if self.feed.get().strip() else None,
                serial=self.serial.get().strip(),
            )
            count = len(self.indices) * options.copies
            if count > MAX_RECORDS:
                raise ValueError(f"Send up to {MAX_RECORDS} labels per print job.")
        except ValueError as error:
            self.error.set(
                str(error)
                if not str(error).startswith("invalid literal")
                else "Copies and feed must be whole numbers."
            )
            return
        self.messages = SimpleQueue()
        self.running = True
        for widget in [*self.controls, self.submit, self.cancel]:
            widget.state(["disabled"])
        self.error.set("")
        self.progress.configure(maximum=count, value=0)
        self.progress.pack(fill="x", before=self.submit.master, pady=(8, 0))
        self.summary.set("Preparing labels…")
        indices = self.indices

        def print_job():
            images = []
            for index in indices:
                image, issues = render(self.label, index, self.today)
                if issues:
                    raise ValueError(f"Record {index + 1}. {issues[0]}")
                if image.getextrema()[0] == 255:
                    raise ValueError(f"Record {index + 1} is blank.")
                images.append(ImageOps.mirror(image) if options.mirror else image)
            self.messages.put(("status", "Checking the loaded tape…"))
            print_images(
                images, self.label.tape, options, lambda count: self.messages.put(("progress", count))
            )

        Thread(target=background, args=(print_job, self.messages), daemon=True).start()
        self.after(70, self.poll, count)

    def poll(self, count):
        while True:
            try:
                event, payload = self.messages.get_nowait()
            except Empty:
                self.after(70, self.poll, count)
                return
            if event == "progress":
                self.progress.configure(value=payload)
                self.summary.set(f"Submitted {payload} of {count} label(s)")
                continue
            if event == "status":
                self.summary.set(payload)
                continue
            self.running = False
            if event == "done":
                self.result = count
                self.destroy()
            else:
                self.progress.pack_forget()
                self.update_preview()
                for widget in [*self.controls, self.submit, self.cancel]:
                    widget.state(["!disabled"])
                self.error.set("The print job did not complete.")
                messagebox.showerror("Print job stopped", payload, parent=self)
            return
