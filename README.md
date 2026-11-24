# P-Touch UI

P-Touch UI is a Linux desktop editor for designing and printing labels on the
Brother PT-2430PC. It uses your installed [ptouch-print][upstream] to talk to the
printer over USB. You can design, save, preview, and export labels with the
printer disconnected.

## Install, update, and uninstall

You need Python 3.10 or newer, Git, and a Linux desktop session. The installer
is tested on Ubuntu 24.04 and on Debian 12 and 13 for arm64, the base of
Raspberry Pi OS. Clone the repository and run the installer as your desktop
user.

```sh
git clone https://github.com/mrjrask/ptouch-ui.git
cd ptouch-ui
./install.sh
```

On Debian, Ubuntu, and Raspberry Pi OS, the installer uses `sudo apt-get` to
add any missing system packages: `python3-tk`, `python3-venv`, `fontconfig`,
and `fonts-dejavu-core`. If [ptouch-print][upstream] is not already on `PATH`,
the installer also installs it. It uses the distribution's `ptouch-print`
package when one is available. Otherwise, it installs the build tools and
libraries (`build-essential`, `cmake`, `gettext`, `libgd-dev`,
`libusb-1.0-0-dev`, and `pkg-config`), builds the latest upstream source,
and installs it in `/usr/local`. It also installs the upstream udev rules in
`/etc/udev/rules.d` so your desktop user can reach the printer without root.
Unplug and reconnect the printer after the first install.

Run `./install.sh --skip-system-packages` to skip all `sudo` steps and manage
the system packages and `ptouch-print` yourself. On other distributions,
install Tk for Python, venv, Fontconfig, and `ptouch-print` first.

Launch **P-Touch UI** from your desktop app menu or run
`~/.local/bin/ptouch-ui`. The installer adds a per-user menu entry, stores its
isolated Python environment under `$XDG_DATA_HOME/ptouch-ui` (normally
`~/.local/share/ptouch-ui`), and installs commands in `~/.local/bin` (or
`$XDG_BIN_HOME` when set to an absolute path).
Pillow, qrcode, and python-barcode are installed by pip.

Run `~/.local/bin/ptouch-ui-update` to check for the latest commit on the
repository's `main` branch and install it. Git and internet access are
required. The updater leaves a working installation in place if downloading
or building the new version fails. It can also rebuild the current revision to
repair a missing launcher or an incompatible Python environment. Older
environments stay available to already-running app windows, and the
uninstaller removes those environments.

Run `~/.local/bin/ptouch-ui-uninstall` to remove the installed app, its
commands, and its desktop menu entry. If the installer added `ptouch-print`,
the uninstaller removes it as well, including its udev rules. Add
`--keep-ptouch-print` to keep it. A `ptouch-print` that was installed before
P-Touch UI stays in place. Saved labels, exported PNGs, the source checkout,
the system packages, and the build tools stay in place.
`./update.sh` and `./uninstall.sh` in the clone provide the same operations.

The app finds `ptouch-print` on `PATH` by default. For a locally built
binary, set `PTOUCH_PRINT` to its full path when launching from a terminal.

```sh
PTOUCH_PRINT=/full/path/to/ptouch-print ~/.local/bin/ptouch-ui
```

### Run from a checkout

For development or a checkout-local installation, use a virtual environment
in the repository.

```sh
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/ptouch-ui
```

## Design a label

1. Open a blank label from **File** or choose a template. The app opens with an
   editable **Equipment tag** example. Templates also include **Cable ID**,
   **Storage label**, and **Numbered inventory**.
2. Set the tape width and design length under **Label setup**. Length can be
   5–300 mm. Enter the length and press **Set** or **Enter**, or choose
   **Fit length to content** to trim the label to its objects with equal
   margins at both ends.
3. Add objects from the toolbar. Select on the label or in **Layers**, drag to
   move, and drag a corner handle to resize. Double-click an object to edit its
   content or style. Use the inspector for millimeter coordinates, size,
   90° rotations, and alignment to the label edges or center. Right-click an
   object on the label or in **Layers** for edit, clipboard, and ordering
   commands.
4. Select a text object to change its font, size, bold, italic, and alignment
   directly in the inspector, without opening the editor. Type part of a font
   name to narrow the font list. The size arrows step through common sizes,
   and you can type any size from 1 to 300 dots. **Fill box** sets the largest
   size at which the text fits its box. New text objects start with the font
   settings you used last.
5. Use **Raise** and **Lower** to change stacking order. The **Edit** menu has
   undo, redo, cut, copy, paste, duplicate, and delete. Object copy and paste
   work within the app, including when switching documents.
6. Use **Fit** or set a zoom level from 25% to 400%. The rulers are marked in
   millimeters. Turn on **Grid** for placement guides. While the grid is on,
   dragging and resizing snap to it. The canvas scrolls at high zoom levels,
   and Shift+mouse wheel scrolls horizontally.

### Objects and formatting

| Object | Options |
| --- | --- |
| Text | Multiple lines, installed Linux fonts, bold, italic, left/center/right alignment, size in printer dots (2.5 dots per point), optional shrink-to-fit, and white text on a black box |
| Image | Import PNG, JPEG, GIF, BMP, TIFF, or WebP. Adjust the black-and-white threshold or use dithering. Transparency becomes white, and the image keeps its aspect ratio. |
| Rectangle and ellipse | Outlines with adjustable stroke weight or solid fills. Rectangles can have rounded corners (0–64 dots). |
| Line | Horizontal rule with adjustable length and thickness via its width and height. Rotate it 90° for a vertical rule. |
| QR code | Encode text, URLs, and data fields |
| Barcode | Code 128, Code 39, EAN-13, EAN-8, UPC-A, and ITF (interleaved 2 of 5), with an optional human-readable caption. EAN and UPC values can omit the check digit, which is calculated for you. |
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
same fonts for matching text metrics. You can reopen and edit the label later, or open it at launch with
`ptouch-ui path/to/label.ptlabel`. **File → Open recent** lists the last eight
labels you opened or saved. The list is stored in
`$XDG_CONFIG_HOME/ptouch-ui/recent.json` (normally `~/.config/ptouch-ui`).

Use **File → Export PNG** to export the current record as a 180 dpi,
two-color indexed PNG. The exported raster is suitable for
`ptouch-print --image label.png` with matching tape.

## Print over USB

1. Power on the PT-2430PC, connect it over USB, and set its mode switch to **E**.
2. Use **Detect printer** to read the loaded tape. If the design uses a
   different width, the app offers to switch it to the loaded tape. The app
   checks the tape and resolution again before starting a job.
3. Choose **Print** to preview the artwork and pick copies, output mode, and
   data records. **Mirror artwork** flips the print horizontally.
4. Choose **Separate labels** to submit one printer job per label. Choose
   **Single strip with cut marks** to print consecutive labels with dashed
   trim marks between them. A job can contain up to 250 labels total.
5. **Trim blank leader (pre-cut)** is on by default. The cutter sits about
   22 mm ahead of the print head, so without it each label starts with that
   much blank tape. With it, the app prints a short blank page that is not fed
   or cut (`--chain`) before each label. If the installed `ptouch-print`
   enables auto-cut for the PT-2430PC, the printer cuts that blank tape off as
   a scrap piece when the label starts, so the label begins where the design
   does. Older builds that leave auto-cut off print the blank page attached to
   the label, adding about 2 mm to the leader; update `ptouch-print` to trim it.
   Each label is still fed and cut as its own job. This needs a `ptouch-print`
   with `--chain` and `--pad`. If a pre-cut job stops partway through, press
   **Feed & Cut** on the printer to release the tape.
6. Leave **Feed dots** blank for the printer default. Set it to 0–1000 to adjust
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
`ptouch-print` and will not close while a job is being sent. If a job stops partway through, check the tape before resending.

### Printer troubleshooting

- Run `ptouch-print --info` as your desktop user. If it cannot see the printer,
  check the USB cable, power, and **E** mode. Use the upstream udev rules for
  user access if needed, then unplug and reconnect the device.
- If tape detection fails, reseat the cassette and close the cover. A tape
  mismatch means the design and loaded cassette widths differ.
- If optional flags are rejected, leave those fields blank or upgrade
  `ptouch-print`. Basic printing needs its `--info` and `--image` options, and
  strip printing also needs `--cutmark`. When an older release omits the
  resolution in `--info`, the app uses 180 dpi if the output identifies the
  printer as a PT-2430PC.
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
| Ctrl+] / Ctrl+[ | Increase / decrease the selected text's font size |
| Ctrl+B / Ctrl+I | Toggle bold / italic on the selected text |
| Page Up / Page Down | Previous / next data record |
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
It also checks installer cleanup and update failure recovery. A separate job
runs the real installer and uninstaller, including the `ptouch-print` build,
on Ubuntu 24.04 and on Debian 12 and 13 for arm64. The print tests
use a simulated `ptouch-print` executable. The Python 3.12 job also runs the
desktop smoke test under Xvfb.

[upstream]: https://git.familie-radermacher.ch/linux/ptouch-print.git
