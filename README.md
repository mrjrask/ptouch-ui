# P-Touch UI

P-Touch UI is a Linux desktop editor for designing and printing labels on the
Brother PT-2430PC. It uses your installed [ptouch-print][upstream] to talk to the
printer over USB. You can design, save, preview, and export labels with the
printer disconnected.

## Install and launch

You need Python 3.10 or newer, Tk, Fontconfig, and a Linux desktop session.
For printing, install [ptouch-print][upstream] and confirm that it can see the
printer using the same user account that will run the app.

On Debian or Ubuntu, install the desktop dependencies and launch from a virtual environment.

```sh
sudo apt install python3 python3-tk python3-venv fontconfig fonts-dejavu-core
git clone https://github.com/mrjrask/ptouch-ui.git
cd ptouch-ui
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/ptouch-ui
```

Pillow, qrcode, and python-barcode are installed by pip. Other distributions can
use their own Tk and Fontconfig packages with the same Python installation
steps. The app finds `ptouch-print` on `PATH` by default. For a locally built
binary, set `PTOUCH_PRINT` to its full path before launching.

```sh
PTOUCH_PRINT=/full/path/to/ptouch-print .venv/bin/ptouch-ui
```

## Design a label

1. Open a blank label from **File** or choose a template. The app opens with an
   editable **Equipment tag** example. Templates also include **Cable ID**,
   **Storage label**, and **Numbered inventory**.
2. Set the tape width and design length under **Label setup**. Length can be
   5–300 mm. Enter the length and press **Set** or **Enter**.
3. Add objects from the toolbar. Select on the label or in **Layers**, drag to
   move, and drag a corner handle to resize. Double-click an object to edit its
   content or style. Use the inspector for millimeter coordinates, size,
   90° rotations, and alignment to the label edges or center.
4. Use **Raise** and **Lower** to change stacking order. The **Edit** menu has
   undo, redo, cut, copy, paste, duplicate, and delete. Object copy and paste
   work within the app, including when switching documents.
5. Use **Fit** or set a zoom level from 25% to 400%. The rulers are marked in
   millimeters. Turn on **Grid** for placement guides. The canvas scrolls at
   high zoom levels, and Shift+mouse wheel scrolls horizontally.

### Objects and formatting

| Object | Options |
| --- | --- |
| Text | Multiple lines, installed Linux fonts, bold, italic, left/center/right alignment, size in printer dots, and optional shrink-to-fit |
| Image | Import PNG, JPEG, GIF, BMP, TIFF, or WebP. Adjust the black-and-white threshold or use dithering. Transparency becomes white, and the image keeps its aspect ratio. |
| Rectangle and ellipse | Outlines with adjustable stroke weight or solid fills |
| Line | Horizontal rule with adjustable length and thickness via its width and height. Rotate it 90° for a vertical rule. |
| QR code | Encode text, URLs, and data fields |
| Barcode | Code 128, Code 39, and EAN-13, with an optional human-readable caption |
| Symbols and date | Insert a symbol from the toolbar or the current local date. Both are editable as text. |

QR and barcode objects use whole printer dots and preserve their white quiet
zones. Enlarge the box or shorten the value if the editor reports that the
code is too small. A standard QR code needs at least 58 dots in both
directions, so it requires 12 mm or wider tape with this printer. Verify
machine-readable labels with the scanner and tape you intend to use.

## CSV data, numbering, and dates

- Use **Data → Import CSV** for a UTF-8, comma-separated file with a header row
  and up to 250 records. Quote values that include commas or line breaks.
- Type a field such as `{{Asset}}` into a text, QR, or barcode object, or use
  the field picker in its editor. Field names are case-sensitive. Values such
  as `007` preserve their leading zeros.
- Use **Data → Generate numbers** for a start number, record count, and minimum
  digit width. Use `{{number}}` in the label. **Generate numbers** and **Import
  CSV** replace the current records and can be undone.
- The built-in `{{date}}` field uses the local date in `YYYY-MM-DD` format.
  The app resolves it when rendering and uses one date for an entire print job.
- Step through records below the canvas, or use **Data → View current fields**.
  Export the current record or print the current record or all records.

Example CSV for a label that uses `{{Asset}}` and `{{Location}}`.

```csv
Asset,Location
024,Studio A
025,"Storage, north wall"
```

## Save and export

**File → Save** creates a `.ptlabel` document. It embeds imported images and
data records, so they travel with the document. The receiving machine needs the
same fonts for matching text metrics. You can reopen and edit the label later.

Use **File → Export PNG** to export the current record as a 180 dpi,
two-color indexed PNG. The exported raster is suitable for
`ptouch-print --image label.png` with matching tape.

## Print over USB

1. Power on the PT-2430PC, connect it over USB, and set its mode switch to **E**.
2. Use **Detect printer** to read the loaded tape, then set the design to that
   width. The app checks the tape and resolution again before starting a job.
3. Choose **Print** to preview the artwork and pick copies, output mode, and
   data records. **Mirror artwork** flips the print horizontally.
4. Choose **Separate labels** to submit one printer job per label. Choose
   **Single strip with cut marks** to print consecutive labels with dashed
   trim marks between them. The printer can add lead and trailing feed beyond
   the design length. A job can contain up to 250 labels total.
5. Leave **Feed dots** blank for the printer default. Set it to 0–1000 to adjust
   feed on a version of `ptouch-print` that supports `--feed`. Use **USB
   serial** to select a printer if your version supports `--serial`.

Tape widths and the printable heights used by the editor at 180 dpi:

| Tape | Printable height |
| --- | ---: |
| 3.5 mm | 24 dots |
| 6 mm | 32 dots |
| 9 mm | 52 dots |
| 12 mm | 76 dots |
| 18 mm | 120 dots |
| 24 mm | 128 dots |

Print validation catches missing data fields, incomplete or undersized codes,
clipped objects, and blank records before sending labels. A mismatch between
the loaded tape and design stops the job. The app reports progress from
`ptouch-print`. If a job stops partway through, check the tape before resending.

### Printer troubleshooting

- Run `ptouch-print --info` as your desktop user. If it cannot see the printer,
  check the USB cable, power, and **E** mode. Use the upstream udev rules for
  user access if needed, then unplug and reconnect the device.
- If tape detection fails, reseat the cassette and close the cover. A tape
  mismatch means the design and loaded cassette widths differ.
- If optional flags are rejected, leave those fields blank or upgrade
  `ptouch-print`. Basic printing needs its `--info` and `--image` options, and
  strip printing also needs `--cutmark`.
- This version uses its own editable document format. Brother P-touch Editor
  files need to be recreated or brought in as exported images. USB hardware
  printing requires a connected printer and has to be verified on-device.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+N / Ctrl+O | New blank label / Open |
| Ctrl+S / Ctrl+Shift+S | Save / Save as |
| Ctrl+P | Open print preview |
| Ctrl+Z / Ctrl+Shift+Z or Ctrl+Y | Undo / Redo |
| Ctrl+X / Ctrl+C / Ctrl+V | Cut / Copy / Paste selected object |
| Ctrl+D / Delete | Duplicate / Delete selected object |
| Arrow keys with canvas focused | Move selected object by one printer dot |
| Shift+arrow keys with canvas focused | Move selected object by approximately one millimeter |

## Development and CI

```sh
.venv/bin/pip install -e . 'ruff>=0.9,<1'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m unittest discover -s tests -v
xvfb-run -a .venv/bin/python -m unittest discover -s tests -v
```

GitHub Actions builds and installs the wheel on Python 3.10 and 3.12, checks
formatting and lint, and runs document, raster, and print integration tests.
The print tests use a simulated `ptouch-print` executable. The Python 3.12 job
also runs the desktop smoke test under Xvfb.

[upstream]: https://git.familie-radermacher.ch/linux/ptouch-print.git
