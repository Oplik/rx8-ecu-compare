"""Windows desktop GUI for browsing and comparing Mazda RX-8 ECU binaries."""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ecu_compare.compare import CompareResult, change_blurb, change_direction, compare_roms, key_changes
from ecu_compare.defs import DefinitionDB
from ecu_compare.edit import (
    CHECKSUM_WARNING,
    DANGER_WARNING,
    EditError,
    apply_cell,
    export_edited,
    is_immobilizer_table,
    replace_bytes,
)
from ecu_compare.report import write_report
from ecu_compare.rom import DecodedAxis, DecodedTable, LoadedRom, load_rom
from ecu_compare.safety import ROOT, SafetyError, assert_inside_root, import_rom, write_text
from ecu_compare.scaling import format_scaled

_NEW_DEFS = ROOT / "data" / "defs" / "rx8_defs_10.2.2026.xml"
_LABELED_DEFS = ROOT / "data" / "defs" / "rx8_defs_labeled.xml"
_STOCK_DEFS = ROOT / "data" / "defs" / "rx8_defs.xml"
DEFS_PATH = next(
    (path for path in (_NEW_DEFS, _LABELED_DEFS, _STOCK_DEFS) if path.is_file()),
    _STOCK_DEFS,
)
STATE_PATH = ROOT / "data" / "ui_state.json"
SAMPLE_ORIG = ROOT / "data" / "roms" / "ORIG.bin"
SAMPLE_EDITED = ROOT / "data" / "roms" / "EditedMap.bin"

CHANGED_BG = "#F7D774"
UP_BG = "#F5B7B1"
DOWN_BG = "#A9CCE3"
HEADER_BG = "#E8EEF4"
AXIS_BG = "#D6E4F0"
EDIT_WARN = (
    "Enable map editing?\n\n"
    + DANGER_WARNING
    + "\n\n"
    + CHECKSUM_WARNING
    + "\n\nImmobilizer and switch tables stay locked."
)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def heatmap_color(value: float | None, vmin: float, vmax: float) -> str:
    """Low = pale teal, high = medium blue. Stays light enough that black text still reads."""
    if value is None or vmax <= vmin:
        return "#F4F8FB"
    t = (value - vmin) / (vmax - vmin)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    r = _lerp(0xE8, 0x3D, t)
    g = _lerp(0xF6, 0x7A, t)
    b = _lerp(0xFB, 0xB5, t)
    return f"#{r:02X}{g:02X}{b:02X}"


def text_on(bg_hex: str) -> str:
    """Black or white, whichever contrasts with the cell background."""
    h = bg_hex.lstrip("#")
    if len(h) != 6:
        return "#111111"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return "#111111" if lum >= 0.62 else "#FFFFFF"


A_LINE = "#1A4F86"
B_LINE = "#C0392B"
UP_FILL = "#F8D0D0"
DOWN_FILL = "#D6E6F5"


@dataclass(frozen=True)
class CurvePoint:
    index: int
    x: float
    y: float | None
    y_index: int
    x_index: int
    x_text: str


@dataclass(frozen=True)
class PlotBox:
    left: float
    top: float
    right: float
    bottom: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def px(self, x: float) -> float:
        span = self.x_max - self.x_min or 1.0
        return self.left + (x - self.x_min) / span * (self.right - self.left)

    def py(self, y: float) -> float:
        span = self.y_max - self.y_min or 1.0
        return self.bottom - (y - self.y_min) / span * (self.bottom - self.top)


def is_curve_table(table: DecodedTable) -> bool:
    """A 1D or 2D map with a single row or column and at least two points."""
    kind = (table.definition.table_type or "").lower()
    if kind in {"3d", "switch"} or table.decode_error:
        return False
    rows, cols = table.rows(), table.cols()
    if rows != 1 and cols != 1:
        return False
    count = cols if rows == 1 else rows
    return count >= 2


def curve_points(table: DecodedTable) -> list[CurvePoint]:
    """Breakpoints along X, table values along Y. Empty when the map is not a curve."""
    if not is_curve_table(table):
        return []
    count = table.cols() if table.rows() == 1 else table.rows()
    axis = table.y_axis or table.x_axis
    use_values = (
        axis is not None
        and len(axis.scaled) >= count
        and all(axis.scaled[i] is not None for i in range(count))
    )
    points: list[CurvePoint] = []
    for i in range(count):
        y_index, x_index = (0, i) if table.rows() == 1 else (i, 0)
        if use_values and axis is not None:
            raw_x = axis.scaled[i]
            assert raw_x is not None
            err = axis.errors[i] if i < len(axis.errors) else None
            x_val = float(raw_x)
            x_text = format_scaled(raw_x, axis.format, err)
        else:
            x_val = float(i)
            x_text = str(i)
        raw_y = table.data_scaled[y_index][x_index]
        y_val = None if raw_y is None else float(raw_y)
        points.append(CurvePoint(i, x_val, y_val, y_index, x_index, x_text))
    return points


def values_on(base: list[CurvePoint], decoded: DecodedTable) -> list[CurvePoint] | None:
    """Same breakpoints as base, values taken from another copy of the map."""
    out: list[CurvePoint] = []
    for point in base:
        if point.y_index >= decoded.rows() or point.x_index >= decoded.cols():
            return None
        raw = decoded.data_scaled[point.y_index][point.x_index]
        y_val = None if raw is None else float(raw)
        out.append(CurvePoint(point.index, point.x, y_val, point.y_index, point.x_index, point.x_text))
    return out


def axis_title(axis: DecodedAxis | None) -> str:
    if axis is None:
        return "Axis"
    name = axis.name or "Axis"
    if axis.units:
        return f"{name} ({axis.units})"
    return name


def curve_axis_title(table: DecodedTable) -> str:
    return axis_title(table.y_axis or table.x_axis)


def row_points(table: DecodedTable, y_index: int) -> list[CurvePoint]:
    """One row of a 3D map. The X axis is the breakpoint, the cell value is the height."""
    if table.decode_error or y_index < 0 or y_index >= table.rows() or table.cols() < 2:
        return []
    count = table.cols()
    axis = table.x_axis
    use_values = (
        axis is not None
        and len(axis.scaled) >= count
        and all(axis.scaled[i] is not None for i in range(count))
    )
    points: list[CurvePoint] = []
    for x in range(count):
        if use_values and axis is not None:
            raw_x = axis.scaled[x]
            assert raw_x is not None
            err = axis.errors[x] if x < len(axis.errors) else None
            x_val = float(raw_x)
            x_text = format_scaled(raw_x, axis.format, err)
        else:
            x_val = float(x)
            x_text = str(x)
        raw_y = table.data_scaled[y_index][x]
        y_val = None if raw_y is None else float(raw_y)
        points.append(CurvePoint(x, x_val, y_val, y_index, x, x_text))
    return points


def best_changed_row(left: DecodedTable | None, right: DecodedTable | None, rows: int) -> int:
    """Row with the largest absolute difference. The first row when nothing changed."""
    if left is None or right is None or rows <= 0:
        return 0
    best_y = 0
    best_mag = -1.0
    limit_y = min(rows, left.rows(), right.rows())
    limit_x = min(left.cols(), right.cols())
    for y in range(limit_y):
        for x in range(limit_x):
            a = left.data_scaled[y][x]
            b = right.data_scaled[y][x]
            if a is None or b is None:
                continue
            mag = abs(float(b) - float(a))
            if mag > best_mag:
                best_mag = mag
                best_y = y
    return best_y


def _pad_span(lo: float, hi: float) -> tuple[float, float]:
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        pad = abs(lo) * 0.08 or 1.0
        return lo - pad, hi + pad
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def plot_box(width: int, height: int, xs: list[float], ys: list[float]) -> PlotBox:
    left, top = 78.0, 36.0
    right = max(left + 10.0, width - 16.0)
    bottom = max(top + 10.0, height - 32.0)
    if not xs:
        xs = [0.0, 1.0]
    if not ys:
        ys = [0.0, 1.0]
    x0, x1 = _pad_span(min(xs), max(xs))
    y0, y1 = _pad_span(min(ys), max(ys))
    return PlotBox(left, top, right, bottom, x0, x1, y0, y1)


def nearest_index(points: list[CurvePoint], box: PlotBox, pixel_x: float) -> int | None:
    best: int | None = None
    best_d = 1e18
    for point in points:
        dist = abs(box.px(point.x) - pixel_x)
        if dist < best_d:
            best_d = dist
            best = point.index
    return best


def line_segments(points: list[CurvePoint], box: PlotBox) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for point in points:
        if point.y is None:
            if len(current) >= 2:
                segments.append(current)
            current = []
            continue
        current.append((box.px(point.x), box.py(point.y)))
    if len(current) >= 2:
        segments.append(current)
    return segments


def y_ticks(values: list[float], count: int = 4) -> list[float]:
    if not values:
        return [0.0]
    lo, hi = min(values), max(values)
    if hi == lo or count <= 1:
        return [lo]
    return [lo + (hi - lo) * i / (count - 1) for i in range(count)]


def x_tick_indices(count: int, plot_width: float) -> list[int]:
    if count <= 0:
        return []
    if count == 1:
        return [0]
    slot = plot_width / max(count - 1, 1)
    stride = 1 if slot >= 52 else max(1, int(round(52 / max(slot, 1.0))))
    picked = list(range(0, count, stride))
    last = count - 1
    if picked[-1] != last:
        if last - picked[-1] < stride * 0.6 and len(picked) > 1:
            picked[-1] = last
        else:
            picked.append(last)
    return picked


def series_match(left: list[CurvePoint] | None, right: list[CurvePoint] | None) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    return all(a.y == b.y for a, b in zip(left, right))


def fill_bands(
    left: list[CurvePoint], right: list[CurvePoint], box: PlotBox
) -> list[tuple[list[float], str]]:
    """Polygons between File A and File B. Pink where B is higher, blue where B is lower."""
    bands: list[tuple[list[float], str]] = []
    for i in range(min(len(left), len(right)) - 1):
        a0, a1 = left[i], left[i + 1]
        b0, b1 = right[i], right[i + 1]
        if a0.y is None or a1.y is None or b0.y is None or b1.y is None:
            continue
        delta = ((b0.y - a0.y) + (b1.y - a1.y)) / 2.0
        if delta == 0:
            continue
        color = UP_FILL if delta > 0 else DOWN_FILL
        coords = [
            box.px(a0.x), box.py(a0.y),
            box.px(a1.x), box.py(a1.y),
            box.px(b1.x), box.py(b1.y),
            box.px(b0.x), box.py(b0.y),
        ]
        bands.append((coords, color))
    return bands


def _map_states(item: tuple) -> tuple:
    """State names from one Treeview style-map entry. Tk returns them in two shapes."""
    if len(item) >= 2 and not isinstance(item[0], str):
        return tuple(item[0])
    return tuple(item[:-1])


def allow_tree_tags(style: ttk.Style) -> None:
    """Vista paints every unselected row itself, which hides per-row colors."""

    def scrub(option: str) -> list:
        kept = []
        for item in style.map("Treeview", query_opt=option):
            states = _map_states(item)
            if states in {("!disabled", "!selected"), ("!disabled !selected",)}:
                continue
            kept.append(item)
        return kept

    style.map(
        "Treeview",
        foreground=scrub("foreground") + [("selected", "#FFFFFF")],
        background=scrub("background") + [("selected", "#1A4F86")],
    )


def enable_windows_dpi() -> None:
    """Stop Windows from bitmap-stretching the window, which makes text look pale."""
    if os.name != "nt":
        return
    try:
        import ctypes

        # PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def signed_delta(a: float | None, b: float | None, fmt: str) -> str:
    if a is None or b is None:
        return "—"
    delta = b - a
    if a != b and format_scaled(a, fmt) == format_scaled(b, fmt):
        return f"{delta:+.6g}"
    text = format_scaled(delta, fmt)
    if delta > 0 and not text.startswith("+"):
        return "+" + text
    return text


def diff_cell_text(a: float | None, b: float | None, fmt: str) -> str:
    """File B on the first line and the signed change under it."""
    if a is None or b is None:
        return signed_delta(a, b, fmt)
    now = format_scaled(b, fmt)
    if a == b:
        return now
    return f"{now}\n{signed_delta(a, b, fmt)}"


def _table_name(iid: str) -> str:
    text = str(iid)
    if text.startswith("chg::"):
        return text[5:]
    if text.startswith("miss::"):
        return text[6:]
    return text


def _defs_from_state() -> Path:
    """Use a definition the user picked. Otherwise keep the built-in order."""
    raw = _load_state().get("defs")
    if not isinstance(raw, str):
        return DEFS_PATH
    try:
        chosen = assert_inside_root(Path(raw))
    except (OSError, SafetyError):
        return DEFS_PATH
    folder = (ROOT / "data" / "defs").resolve()
    if chosen.is_file() and chosen.parent == folder:
        return chosen
    return DEFS_PATH


def is_placeholder_name(name: str) -> bool:
    """RomRaider leftovers such as 'Table 2D - 5' or 'UNKNOWN_68c4c'."""
    n = " ".join((name or "").lower().split())
    if n.startswith(("table 1d", "table 2d", "table 3d", "table float")):
        return True
    if "unknown_" in n or "check datatype" in n:
        return True
    return False


def _value_range(table: DecodedTable) -> tuple[float, float]:
    nums = [v for row in table.data_scaled for v in row if v is not None]
    if not nums:
        return 0.0, 1.0
    return min(nums), max(nums)


def _load_state() -> dict:
    try:
        if STATE_PATH.is_file():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_state(payload: dict) -> None:
    try:
        assert_inside_root(STATE_PATH)
        write_text(STATE_PATH, json.dumps(payload, indent=2))
    except OSError:
        pass


class EcuCompareApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.defs_path = _defs_from_state()
        self._defs_chosen = "defs" in _load_state()
        self.defs_var = tk.StringVar(value=str(self.defs_path))
        self.title(f"RX-8 ECU Compare  —  {self.defs_path.name}")
        self.geometry("1380x860")
        self.minsize(1000, 680)
        self.configure(bg="#F4F6F8")
        self.option_add("*Font", "{Segoe UI} 11")
        self.option_add("*foreground", "#111111")
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", foreground="#111111", font=("Segoe UI", 11))
        style.configure("TFrame", background="#F4F6F8")
        style.configure("TLabel", foreground="#111111", background="#F4F6F8", font=("Segoe UI", 11))
        style.configure("TButton", padding=5, font=("Segoe UI", 11))
        style.configure("TCheckbutton", foreground="#111111", background="#F4F6F8", font=("Segoe UI", 11))
        style.configure("TRadiobutton", foreground="#111111", background="#F4F6F8", font=("Segoe UI", 11))
        style.configure(
            "Treeview",
            rowheight=26,
            font=("Segoe UI", 11),
            foreground="#111111",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
        )
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"), foreground="#111111")
        allow_tree_tags(style)

        self.db = DefinitionDB(self.defs_path)
        self.rom_a: LoadedRom | None = None
        self.rom_b: LoadedRom | None = None
        self.result: CompareResult | None = None
        self.edit_enabled = False
        self.pending_notes: list[str] = []
        self._undo: list[tuple[int, bytes, str]] = []
        self._load_queue: list[tuple[str, Path]] = []
        self._load_busy = False
        self._chart_drawing = False
        self._grid_cells: dict[tuple[int, int], tk.Label] = {}
        self._current_table: str | None = None
        self._fill_job: str | None = None
        self._jump_to_change = False
        self._jump_target: str | None = None
        self._jump_cell: tuple[int, int] | None = None
        self._key_targets: dict[str, tuple[str, int, int]] = {}
        self._restore_table = _load_state().get("last_table")
        self._hold_restore = False
        self._selected_cell: tuple[int, int] | None = None
        self._chart_a: list[CurvePoint] | None = None
        self._chart_b: list[CurvePoint] | None = None
        self._chart_x_title = ""
        self._chart_y_title = ""
        self._chart_format = ""
        self._plot_box: PlotBox | None = None
        self._chart_size: tuple[int, int] | None = None
        self._chart_mode: str | None = None
        self._chart_row: int | None = None
        self._row_headers: dict[int, tk.Label] = {}

        self._build_menu()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restore_geometry()
        self.status.set("Ready. Load a ROM to browse maps, or load two and compare.")
        self.after(50, self._autoload_samples)
        self.bind("<Control-f>", lambda _e: (self.filter_entry.focus_set(), "break")[-1])
        self.bind("<Control-F>", lambda _e: (self.filter_entry.focus_set(), "break")[-1])
        self.bind("<F5>", lambda _e: self._compare())
        self.bind("<Control-s>", lambda _e: self._export_current_table())
        self.bind("<Control-z>", self._undo_edit)
        self.bind("<Control-Z>", self._undo_edit)
        self.bind("<F3>", lambda _e: self._step_changed(1))
        self.bind("<Shift-F3>", lambda _e: self._step_changed(-1))
        self.bind("<F6>", lambda _e: self._step_changed_cell(1))
        self.bind("<Shift-F6>", lambda _e: self._step_changed_cell(-1))
        self.bind("<Alt-Up>", lambda _e: (self._step_row(-1), "break")[-1])
        self.bind("<Alt-Down>", lambda _e: (self._step_row(1), "break")[-1])
        self.bind("<Alt-Right>", lambda _e: (self._step_changed_row(1), "break")[-1])
        for key in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(key, self._arrow_cell)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Load File A…", command=lambda: self._load("a"), accelerator="Ctrl+O")
        file_menu.add_command(label="Load File B…", command=lambda: self._load("b"))
        file_menu.add_separator()
        file_menu.add_command(label="Load ORIG as A", command=lambda: self._load_path("a", SAMPLE_ORIG))
        file_menu.add_command(label="Load EditedMap as B", command=lambda: self._load_path("b", SAMPLE_EDITED))
        file_menu.add_command(label="Load ORIG vs EditedMap", command=self._load_samples)
        defs_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Definition file", menu=defs_menu)
        defs_menu.configure(postcommand=lambda menu=defs_menu: self._fill_defs_menu(menu))
        file_menu.add_separator()
        file_menu.add_command(label="Export compare report…", command=self._export, accelerator="F5 then export")
        file_menu.add_command(label="Copy current table", command=self._copy_table)
        file_menu.add_command(label="Export current table as text…", command=self._export_current_table)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.bind("<Control-o>", lambda _e: self._load("a"))

        extra = tk.Menu(menubar, tearoff=0)
        self.edit_var = tk.BooleanVar(value=False)
        extra.add_checkbutton(
            label="Enable map editing (dangerous)",
            variable=self.edit_var,
            command=self._toggle_edit,
        )
        extra.add_command(label="Undo last edit", command=self._undo_edit, accelerator="Ctrl+Z")
        extra.add_command(label="Save edited copy…", command=self._save_edited)
        menubar.add_cascade(label="Extra settings", menu=extra)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._about)
        help_menu.add_command(label="Open defs folder", command=lambda: os.startfile(ROOT / "data" / "defs"))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        self.info_a = tk.StringVar(value="No file loaded")
        self.info_b = tk.StringVar(value="No file loaded")
        card_a = self._file_card(
            top, "FILE A", A_LINE, self.info_a, lambda: self._load("a"), lambda: self._load_path("a", SAMPLE_ORIG), "Sample: ORIG"
        )
        card_b = self._file_card(
            top,
            "FILE B",
            B_LINE,
            self.info_b,
            lambda: self._load("b"),
            lambda: self._load_path("b", SAMPLE_EDITED),
            "Sample: EditedMap",
        )
        card_a.pack(side="left", fill="both", expand=True, padx=(0, 6))
        card_b.pack(side="left", fill="both", expand=True, padx=(6, 0))

        actions = ttk.Frame(self, padding=(8, 4, 8, 2))
        actions.pack(fill="x")
        tk.Button(
            actions,
            text="Compare",
            command=self._compare,
            bg=A_LINE,
            fg="#FFFFFF",
            activebackground="#153E6B",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 11, "bold"),
            padx=16,
            pady=4,
            relief="flat",
            bd=0,
        ).pack(side="left")
        ttk.Button(actions, text="Export report…", command=self._export).pack(side="left", padx=6)
        ttk.Button(actions, text="Copy table", command=self._copy_table).pack(side="left")
        ttk.Button(actions, text="Reports folder", command=self._open_reports).pack(side="left", padx=6)
        ttk.Button(actions, text="Previous change", command=lambda: self._step_changed(-1)).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Next change", command=lambda: self._step_changed(1)).pack(side="left", padx=4)

        filters = ttk.Frame(self, padding=(8, 2, 8, 6))
        filters.pack(fill="x")
        ttk.Label(filters, text="Show").pack(side="left", padx=(0, 6))
        self.view_mode = tk.StringVar(value="a")
        self._view_buttons: dict[str, tk.Button] = {}
        for label, mode in (("File A", "a"), ("File B", "b"), ("Difference", "diff")):
            self._view_button(filters, label, mode)
        self._sync_view_buttons()
        self.show_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filters, text="Unchanged maps", variable=self.show_all, command=self._fill_table_list
        ).pack(side="left", padx=(12, 4))
        self.named_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filters, text="Named maps only", variable=self.named_only, command=self._fill_table_list
        ).pack(side="left", padx=(0, 8))
        ttk.Label(filters, text="Pink: B higher    Blue: B lower").pack(side="left", padx=(4, 8))
        ttk.Label(filters, text="Search").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filters, textvariable=self.filter_var)
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.filter_var.trace_add("write", lambda *_: self._schedule_fill())

        key_wrap = ttk.Frame(self, padding=(8, 0, 8, 4))
        key_wrap.pack(fill="x")
        ttk.Label(
            key_wrap,
            text="Largest changes",
            font=("Segoe UI", 12, "bold"),
            foreground="#111111",
        ).pack(anchor="w")
        self.key_list = ttk.Treeview(
            key_wrap, columns=("map", "change"), show="headings", height=4, selectmode="browse"
        )
        self.key_list.heading("map", text="Map")
        self.key_list.heading("change", text="Change")
        self.key_list.column("map", width=420, anchor="w", stretch=True)
        self.key_list.column("change", width=700, anchor="w", stretch=True)
        self.key_list.tag_configure("up", background=UP_BG, foreground="#111111")
        self.key_list.tag_configure("down", background=DOWN_BG, foreground="#111111")
        self.key_list.pack(fill="x")
        self.key_list.bind("<<TreeviewSelect>>", lambda _e: self._pick_key_change())
        self._key_targets: dict[str, tuple[str, int, int]] = {}

        self.summary = tk.StringVar(value="Load two ROMs to compare them.")
        tk.Label(
            self,
            textvariable=self.summary,
            anchor="w",
            bg="#E7F0FA",
            fg="#111111",
            font=("Segoe UI", 12, "bold"),
            padx=10,
            pady=6,
        ).pack(fill="x", padx=8, pady=(0, 6))

        paned = ttk.Panedwindow(self, orient="horizontal")
        self.paned = paned
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left_pane = ttk.Frame(paned)
        right_pane = ttk.Frame(paned)
        self.right_pane = right_pane
        paned.add(left_pane, weight=1)
        paned.add(right_pane, weight=4)
        paned.bind("<ButtonRelease-1>", lambda _e: self._sync_desc_wrap(), add="+")
        try:
            paned.pane(left_pane, minsize=480)
        except tk.TclError:
            pass
        ttk.Label(left_pane, text="Maps", font=("Segoe UI", 12, "bold"), foreground="#111111").pack(
            anchor="w", pady=(0, 2)
        )

        tree_wrap = ttk.Frame(left_pane)
        tree_wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=("change",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Map", anchor="w")
        self.tree.heading("change", text="Change", anchor="w")
        self.tree.column("#0", width=260, minwidth=140, stretch=True, anchor="w")
        self.tree.column("change", width=220, minwidth=120, stretch=True, anchor="w")
        self.tree.tag_configure("changed", foreground="#8B0000", font=("Segoe UI", 11, "bold"))
        self.tree.tag_configure("up", background=UP_BG, foreground="#111111")
        self.tree.tag_configure("down", background=DOWN_BG, foreground="#111111")
        self.tree.tag_configure("category", font=("Segoe UI", 11, "bold"), foreground="#111111")
        scroll_l = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_l.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_l.grid(row=0, column=1, sticky="ns")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected_table())
        self.tree.bind("<Double-1>", lambda _e: self._show_selected_table())

        title_row = ttk.Frame(right_pane)
        title_row.pack(fill="x")
        ttk.Label(
            title_row,
            textvariable=self._make_title_var(),
            font=("Segoe UI", 18, "bold"),
            foreground="#111111",
        ).pack(side="left", anchor="w")
        ttk.Button(title_row, text="Copy address", command=self._copy_address).pack(side="right")

        self.table_meta = tk.StringVar(value="")
        ttk.Label(
            right_pane,
            textvariable=self.table_meta,
            foreground="#111111",
            font=("Segoe UI", 12),
        ).pack(anchor="w")
        self.table_desc = tk.StringVar(value="")
        self.desc_label = ttk.Label(
            right_pane,
            textvariable=self.table_desc,
            wraplength=980,
            foreground="#111111",
            font=("Segoe UI", 11),
        )
        self.desc_label.pack(anchor="w")
        self.range_line = tk.StringVar(value="")
        ttk.Label(
            right_pane,
            textvariable=self.range_line,
            foreground="#111111",
            font=("Segoe UI", 11),
        ).pack(anchor="w")
        self.cell_detail = tk.StringVar(value="Click a cell for A, B, and the difference.")
        self.cell_detail_label = tk.Label(
            right_pane,
            textvariable=self.cell_detail,
            bg="#F4F7FB",
            fg="#111111",
            font=("Segoe UI", 18, "bold"),
            anchor="w",
            padx=10,
            pady=8,
        )
        self.cell_detail_label.pack(fill="x", pady=(4, 0))
        self.cell_status = tk.StringVar(value="")
        ttk.Label(
            right_pane,
            textvariable=self.cell_status,
            foreground="#111111",
            font=("Consolas", 11),
        ).pack(anchor="w")

        self.row_bar = ttk.Frame(right_pane)
        ttk.Button(self.row_bar, text="◀ Previous row", command=lambda: self._step_row(-1)).pack(side="left")
        self.row_caption = tk.StringVar(value="")
        ttk.Label(
            self.row_bar,
            textvariable=self.row_caption,
            font=("Segoe UI", 14, "bold"),
            foreground="#111111",
        ).pack(side="left", padx=12)
        ttk.Button(self.row_bar, text="Next row ▶", command=lambda: self._step_row(1)).pack(side="left")
        ttk.Button(self.row_bar, text="Next changed row", command=lambda: self._step_changed_row(1)).pack(
            side="left", padx=(8, 0)
        )

        self.chart = tk.Canvas(
            right_pane,
            height=220,
            bg="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#9AA6B2",
        )
        self.chart.bind("<Configure>", self._on_chart_configure)
        self.chart.bind("<Button-1>", self._on_chart_click)
        self.chart.bind("<MouseWheel>", self._on_mousewheel)

        grid_wrap = ttk.Frame(right_pane)
        self.grid_wrap = grid_wrap
        grid_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(grid_wrap, highlightthickness=0, bg="white", takefocus=True)
        self.hscroll = ttk.Scrollbar(grid_wrap, orient="horizontal", command=self.canvas.xview)
        self.vscroll = ttk.Scrollbar(grid_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hscroll.set, yscrollcommand=self.vscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.hscroll.grid(row=1, column=0, sticky="ew")
        grid_wrap.rowconfigure(0, weight=1)
        grid_wrap.columnconfigure(0, weight=1)
        self.grid_frame = tk.Frame(self.canvas, bg="white")
        self._canvas_win = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.grid_frame.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<MouseWheel>", self._on_tree_wheel)

        change_wrap = ttk.Frame(right_pane)
        change_wrap.pack(fill="both", expand=False, pady=(6, 0))
        change_head = ttk.Frame(change_wrap)
        change_head.pack(fill="x")
        ttk.Label(
            change_head,
            text="Changes in this map",
            font=("Segoe UI", 11, "bold"),
            foreground="#111111",
        ).pack(side="left")
        ttk.Button(change_head, text="Next cell", command=lambda: self._step_changed_cell(1)).pack(side="right")
        ttk.Button(change_head, text="Previous cell", command=lambda: self._step_changed_cell(-1)).pack(
            side="right", padx=(0, 4)
        )
        cols = ("where", "a", "b", "delta")
        self.changes = ttk.Treeview(change_wrap, columns=cols, show="headings", height=7)
        self.changes.heading("where", text="Cell")
        self.changes.heading("a", text="A")
        self.changes.heading("b", text="B")
        self.changes.heading("delta", text="Difference")
        self.changes.column("where", width=280, anchor="w", stretch=True)
        self.changes.column("a", width=160, anchor="e", stretch=True)
        self.changes.column("b", width=160, anchor="e", stretch=True)
        self.changes.column("delta", width=160, anchor="e", stretch=True)
        self.changes.tag_configure("up", background=UP_BG, foreground="#111111")
        self.changes.tag_configure("down", background=DOWN_BG, foreground="#111111")
        change_scroll = ttk.Scrollbar(change_wrap, orient="vertical", command=self.changes.yview)
        self.changes.configure(yscrollcommand=change_scroll.set)
        self.changes.pack(side="left", fill="both", expand=True)
        change_scroll.pack(side="right", fill="y")
        self.changes.bind("<<TreeviewSelect>>", lambda _e: self._pick_change_row())

        self.status = tk.StringVar()
        tk.Label(
            self,
            textvariable=self.status,
            anchor="w",
            bg="#E6E6E6",
            fg="#111111",
            font=("Segoe UI", 11),
            padx=8,
            pady=4,
        ).pack(fill="x", side="bottom")

    def _make_title_var(self) -> tk.StringVar:
        self.table_title = tk.StringVar(value="Select a map")
        return self.table_title

    def _file_card(self, parent, title, accent, info_var, load_cmd, sample_cmd, sample_label):
        card = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1, highlightbackground="#C5CED6")
        tk.Frame(card, bg=accent, width=10).pack(side="left", fill="y")
        body = tk.Frame(card, bg="#FFFFFF")
        body.pack(side="left", fill="both", expand=True, padx=(10, 6), pady=6)
        tk.Label(
            body, text=title, bg="#FFFFFF", fg=accent, font=("Segoe UI", 11, "bold"), anchor="w"
        ).pack(fill="x")
        tk.Label(
            body,
            textvariable=info_var,
            bg="#FFFFFF",
            fg="#111111",
            font=("Segoe UI", 12),
            anchor="w",
            justify="left",
        ).pack(fill="x")
        buttons = tk.Frame(card, bg="#FFFFFF")
        buttons.pack(side="right", padx=(0, 8), pady=6)
        ttk.Button(buttons, text="Load…", command=load_cmd).pack(fill="x")
        ttk.Button(buttons, text=sample_label, command=sample_cmd).pack(fill="x", pady=(4, 0))
        return card

    def _view_button(self, parent, text: str, mode: str) -> None:
        button = tk.Button(
            parent,
            text=text,
            font=("Segoe UI", 11, "bold"),
            padx=14,
            pady=3,
            relief="raised",
            bd=1,
            command=lambda m=mode: self._choose_view(m),
        )
        button.pack(side="left", padx=(0, 4))
        self._view_buttons[mode] = button

    def _choose_view(self, mode: str) -> None:
        self.view_mode.set(mode)
        self._sync_view_buttons()
        self._refresh_view()

    def _sync_view_buttons(self) -> None:
        current = self.view_mode.get()
        colors = {"a": A_LINE, "b": B_LINE, "diff": "#243447"}
        for mode, button in self._view_buttons.items():
            if mode == current:
                bg = colors.get(mode, A_LINE)
                button.configure(
                    bg=bg, fg="#FFFFFF", activebackground=bg, activeforeground="#FFFFFF", relief="sunken"
                )
            else:
                button.configure(
                    bg="#E8EEF4",
                    fg="#111111",
                    activebackground="#D6E0EA",
                    activeforeground="#111111",
                    relief="raised",
                )

    def _change_suffix(self, name: str, diffs: dict) -> str:
        diff = diffs.get(name)
        if diff is None:
            return ""
        return change_blurb(diff)

    def _change_tags(self, name: str, diffs: dict) -> tuple[str, ...]:
        diff = diffs.get(name)
        if diff is None:
            return ()
        direction = change_direction(diff)
        if direction > 0:
            return ("up",)
        if direction < 0:
            return ("down",)
        return ("changed",)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.state & 0x0001:  # Shift
            self.canvas.xview_scroll(int(-event.delta / 120), "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_tree_wheel(self, event: tk.Event) -> None:
        if event.state & 0x0001:
            self.tree.xview_scroll(int(-event.delta / 120), "units")
        else:
            self.tree.yview_scroll(int(-event.delta / 120), "units")

    def _schedule_fill(self) -> None:
        if self._fill_job:
            self.after_cancel(self._fill_job)
        self._fill_job = self.after(120, self._fill_table_list)

    def _about(self) -> None:
        messagebox.showinfo(
            "About",
            "RX-8 ECU Compare\n\n"
            f"Definitions: {self.defs_path.name}\n"
            "Browse maps in one ROM, or compare two.\n"
            f"Working copies live under {ROOT}. Originals are never written.\n"
            "This program does not flash an ECU.\n\n"
            "Ctrl+O load A   Ctrl+F search   F5 compare   F3 next map   F6 next cell",
        )

    def _toggle_edit(self) -> None:
        if self.edit_var.get():
            if not messagebox.askokcancel("Dangerous option", EDIT_WARN, icon="warning"):
                self.edit_var.set(False)
                self.edit_enabled = False
                return
            self.edit_enabled = True
            self.status.set("Editing enabled on File B working copy. Immobilizer/switch tables stay locked.")
        else:
            self.edit_enabled = False
            self.status.set("Editing disabled.")
        if self._current_table:
            self._render_table(self._current_table)

    def _describe_rom(self, rom: LoadedRom) -> str:
        ident = rom.rom_id.label() if rom.rom_id else "unidentified"
        return f"{rom.path.name}\nCAL {rom.cal_id() or 'n/a'}    ·    {ident}"

    def _load_path(self, which: str, path: Path) -> None:
        if not Path(path).is_file():
            messagebox.showerror("Missing file", str(path))
            return
        self._load_from(which, Path(path))

    def _load(self, which: str) -> None:
        path = filedialog.askopenfilename(
            title=f"Select ECU file {which.upper()}",
            filetypes=[("ECU binary", "*.bin"), ("All files", "*.*")],
            initialdir=str(ROOT / "data" / "roms"),
        )
        if not path:
            return
        self._load_from(which, Path(path))

    def _load_from(self, which: str, path: Path) -> None:
        self._load_queue.append((which, Path(path)))
        self.status.set(f"Reading {Path(path).name}…")
        self.config(cursor="watch")
        self._pump_loads()

    def _pump_loads(self) -> None:
        if self._load_busy or not self._load_queue:
            return
        self.config(cursor="watch")
        which, path = self._load_queue.pop(0)
        self._load_busy = True
        db = self.db

        def work() -> None:
            try:
                copied = import_rom(path)
                rom = load_rom(copied, db, source_hint=str(path))
            except Exception as exc:
                self.after(0, lambda err=exc: self._finish_load(which, path, None, None, err))
                return
            self.after(0, lambda: self._finish_load(which, path, copied, rom, None))

        threading.Thread(target=work, daemon=True).start()

    def _finish_load(self, which: str, path: Path, copied, rom, error: Exception | None) -> None:
        self._load_busy = False
        b_still_queued = any(item[0] == "b" for item in self._load_queue)
        if which == "b" or not b_still_queued:
            self._hold_restore = False
        if error is not None:
            self.config(cursor="")
            messagebox.showerror("Load failed", str(error))
            self._pump_loads()
            return
        if which == "a":
            self.rom_a = rom
            self.info_a.set(self._describe_rom(rom))
            if self.view_mode.get() == "b" and self.rom_b is None:
                self.view_mode.set("a")
                self._sync_view_buttons()
        else:
            self.rom_b = rom
            self.info_b.set(self._describe_rom(rom))
        self.result = None
        self.pending_notes = []
        self._undo.clear()
        if not (self.rom_a is not None and self.rom_b is not None):
            self._fill_key_list([])
        ntables = len(rom.tables)
        self.status.set(
            f"Loaded {copied.name}  ({ntables} tables, CAL {rom.cal_id() or 'n/a'}). "
            f"Working copy is under {ROOT / 'data' / 'roms'}."
        )
        if self.rom_a is not None and self.rom_b is not None:
            self.view_mode.set("diff")
            self._sync_view_buttons()
            self._compare()
        else:
            self.show_all.set(True)
            self._fill_table_list()
        if not self._load_queue:
            self.config(cursor="")
        self._pump_loads()

    def _fill_defs_menu(self, menu: tk.Menu) -> None:
        menu.delete(0, "end")
        folder = ROOT / "data" / "defs"
        files = sorted(folder.glob("*.xml"), key=lambda path: path.name.lower())
        if not files:
            menu.add_command(label="No definition files", state="disabled")
            return
        self.defs_var.set(str(self.defs_path))
        for path in files:
            menu.add_radiobutton(
                label=path.name,
                command=lambda chosen=path: self._switch_defs(chosen),
                variable=self.defs_var,
                value=str(path),
            )

    def _switch_defs(self, path: Path) -> None:
        if self._load_busy or self._load_queue:
            self.status.set("Wait until the current ROM finishes opening.")
            return
        if path.resolve() == self.defs_path.resolve():
            return
        try:
            database = DefinitionDB(path)
        except Exception as exc:
            messagebox.showerror("Definitions", str(exc))
            return
        self.db = database
        self.defs_path = path
        self._defs_chosen = True
        self.defs_var.set(str(path))
        self.title(f"RX-8 ECU Compare  —  {path.name}")
        self._undo.clear()
        self.result = None
        pending: list[tuple[str, Path]] = []
        if self.rom_a is not None:
            pending.append(("a", self.rom_a.path))
        if self.rom_b is not None:
            pending.append(("b", self.rom_b.path))
        self.rom_a = None
        self.rom_b = None
        self._hold_restore = len(pending) > 1
        self._save_defs_choice()
        if not pending:
            self._fill_key_list([])
            self._fill_table_list()
            self.status.set(f"Definitions: {path.name}")
            return
        for which, rom_path in pending:
            self._load_from(which, rom_path)

    def _save_defs_choice(self) -> None:
        state = _load_state()
        state["defs"] = str(self.defs_path)
        _save_state(state)

    def _load_samples(self) -> None:
        self._load_path("a", SAMPLE_ORIG)
        self._load_path("b", SAMPLE_EDITED)

    def _autoload_samples(self) -> None:
        if self.rom_a is not None:
            return
        state = _load_state()
        file_a = Path(state["file_a"]) if state.get("file_a") else SAMPLE_ORIG
        if "file_b" in state:
            file_b = Path(state["file_b"]) if state.get("file_b") else None
        else:
            file_b = SAMPLE_EDITED if SAMPLE_EDITED.is_file() else None
        pending_b = file_b is not None and file_b.is_file()
        self._hold_restore = pending_b
        if file_a.is_file():
            self._load_from("a", file_a)
        elif SAMPLE_ORIG.is_file():
            self._load_from("a", SAMPLE_ORIG)
        if pending_b and file_b is not None:
            self._load_from("b", file_b)

    def _compare(self) -> None:
        if self.rom_a is None or self.rom_b is None:
            messagebox.showwarning("Compare", "Load File A and File B first.")
            return
        try:
            self.result = compare_roms(self.rom_a, self.rom_b)
        except Exception as exc:
            messagebox.showerror("Compare failed", str(exc))
            return
        r = self.result
        size = min(r.rom_a.size, r.rom_b.size) or 1
        pct = 100.0 * r.differing_bytes / size
        extra = ""
        if r.warnings:
            extra = "  |  " + " ".join(r.warnings)
        self.summary.set(
            f"{r.differing_bytes} bytes differ ({pct:.4f}%)  |  "
            f"{r.tables_changed} named tables  |  "
            f"{len(r.mapped_changes)} named cells  |  "
            f"{r.unmapped_count} unmapped bytes{extra}"
        )
        self.view_mode.set("diff")
        self._sync_view_buttons()
        headlines = key_changes(r, limit=12)
        self._fill_key_list(headlines)
        self._jump_to_change = True
        if self._restore_table:
            self._jump_target = self._restore_table
            self._jump_cell = None
        else:
            self._jump_target = headlines[0].table_name if headlines else None
            self._jump_cell = (headlines[0].y, headlines[0].x) if headlines else None
        self._fill_table_list()
        self.status.set(
            "Compare complete. Largest changes are listed above. Click one to open that map."
        )

    def _fill_key_list(self, headlines) -> None:
        if not hasattr(self, "key_list"):
            return
        self.key_list.delete(*self.key_list.get_children())
        self._key_targets = {}
        self._change_order = [item.table_name for item in headlines]
        for index, item in enumerate(headlines):
            iid = f"key-{index}"
            self._key_targets[iid] = (item.table_name, item.y, item.x)
            if item.direction > 0:
                prefix, tag = "▲  ", "up"
            elif item.direction < 0:
                prefix, tag = "▼  ", "down"
            else:
                prefix, tag = "", "flat"
            self.key_list.insert(
                "",
                "end",
                iid=iid,
                values=(prefix + item.table_name, item.summary),
                tags=(tag,),
            )

    def _pick_key_change(self) -> None:
        sel = self.key_list.selection()
        if not sel:
            return
        target = self._key_targets.get(str(sel[0]))
        if not target:
            return
        name, y, x = target
        if not self.tree.exists(name):
            self.filter_var.set("")
            self.show_all.set(True)
            self.named_only.set(False)
            self._fill_table_list()
        if not self.tree.exists(name):
            self.status.set(f"{name} is not in the current list.")
            return
        self._focus_tree_table(self._prefer_changed_iid(name))
        self._select_cell(y, x, take_focus=True)

    def _changed_names(self) -> set[str]:
        if not self.result:
            return set()
        return {d.name for d in self.result.table_diffs}

    def _ordered_table_names(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for cat in self.tree.get_children(""):
            for child in self.tree.get_children(cat):
                text = str(child)
                if text.startswith("cat::") or text in {"chgcat", "missing"}:
                    continue
                name = _table_name(text)
                if name in seen:
                    continue
                seen.add(name)
                names.append(name)
        return names

    def _prefer_changed_iid(self, name: str) -> str:
        shortcut = f"chg::{name}"
        if self.tree.exists(shortcut):
            return shortcut
        return name

    def _focus_tree_table(self, iid: str) -> None:
        parent = self.tree.parent(iid)
        if parent:
            self.tree.item(parent, open=True)
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self._render_table(_table_name(iid))

    def _step_changed(self, delta: int) -> None:
        changed = [n for n in self._ordered_table_names() if n in self._changed_names()]
        if not changed:
            self.status.set("No changed tables in the current list. Load both files and Compare.")
            return
        if self._current_table in changed:
            index = (changed.index(self._current_table) + delta) % len(changed)
        else:
            index = 0 if delta > 0 else len(changed) - 1
        name = changed[index]
        self._focus_tree_table(self._prefer_changed_iid(name))
        self.status.set(f"Changed table {index + 1} of {len(changed)}: {name}")

    def _active_rom(self) -> LoadedRom | None:
        mode = self.view_mode.get()
        if mode == "a":
            return self.rom_a or self.rom_b
        if mode == "b":
            return self.rom_b or self.rom_a
        return self.rom_b or self.rom_a

    def _fill_table_list(self) -> None:
        self._fill_job = None
        prev = self.tree.selection()
        prev_id = prev[0] if prev else None
        open_cats = {iid for iid in self.tree.get_children("") if self.tree.item(iid, "open")}
        if self.tree.exists("missing"):
            self._missing_open = bool(self.tree.item("missing", "open"))
        if self.tree.exists("chgcat"):
            self._changed_folder_open = bool(self.tree.item("chgcat", "open"))
        self.tree.delete(*self.tree.get_children())
        rom = self._active_rom()
        if rom is None:
            return
        needle = (self.filter_var.get() or "").strip().lower()
        changed = self._changed_names()
        diffs = {d.name: d for d in self.result.table_diffs} if self.result else {}
        groups: dict[str, list[str]] = {}
        unlocated: list[str] = []
        hidden_address = {"No storage address", "Switch has no address or data"}
        for name, table in rom.tables.items():
            if not self.show_all.get() and self.result is not None and name not in changed:
                continue
            hay = f"{table.category} {name} {table.definition.table_type or ''} "
            if table.definition.storageaddress is not None:
                hay += f"{table.definition.storageaddress:x} 0x{table.definition.storageaddress:x}"
            if name in diffs:
                hay += " " + change_blurb(diffs[name])
            hay_l = hay.lower()
            if needle and needle not in hay_l:
                continue
            if table.decode_error in hidden_address and not needle:
                if not (self.named_only.get() and is_placeholder_name(name)):
                    unlocated.append(name)
                continue
            if (
                self.named_only.get()
                and is_placeholder_name(name)
                and name not in changed
                and not needle
            ):
                continue
            groups.setdefault(table.category or "Uncategorized", []).append(name)

        first_table = None
        first_changed = None

        def _cat_sort(cat: str) -> tuple:
            nchg = sum(1 for n in groups[cat] if n in changed)
            return (0 if nchg else 1, cat.lower())

        for cat in sorted(groups, key=_cat_sort):
            names = sorted(groups[cat], key=str.lower)
            nchg = sum(1 for n in names if n in changed)
            cid = f"cat::{cat}"
            suffix = f"  ({len(names)})"
            if nchg:
                suffix += f"  • {nchg} changed"
            opened = cid in open_cats or bool(needle) or nchg > 0
            self.tree.insert("", "end", iid=cid, text=cat + suffix, values=("",), open=opened, tags=("category",))
            for name in names:
                self.tree.insert(
                    cid,
                    "end",
                    iid=name,
                    text=name,
                    values=(self._change_suffix(name, diffs),),
                    tags=self._change_tags(name, diffs),
                )
                if first_table is None:
                    first_table = name
                if first_changed is None and name in changed:
                    first_changed = name

        changed_names = [name for names in groups.values() for name in names if name in changed]
        if changed_names and not needle:
            rank = {table_name: index for index, table_name in enumerate(getattr(self, "_change_order", []))}
            changed_names.sort(key=lambda table_name: (rank.get(table_name, 1000), table_name.lower()))
            self.tree.insert(
                "",
                0,
                iid="chgcat",
                text=f"Changed ({len(changed_names)})",
                values=("",),
                open=getattr(self, "_changed_folder_open", True),
                tags=("changed",),
            )
            for name in changed_names:
                self.tree.insert(
                    "chgcat",
                    "end",
                    iid=f"chg::{name}",
                    text=name,
                    values=(self._change_suffix(name, diffs),),
                    tags=self._change_tags(name, diffs),
                )
            first_changed = changed_names[0]

        if unlocated and not needle:
            self.tree.insert(
                "",
                "end",
                iid="missing",
                text=f"Not on this ROM ({len(unlocated)})",
                values=("",),
                open=getattr(self, "_missing_open", False),
                tags=("category",),
            )
            for name in sorted(unlocated, key=str.lower):
                self.tree.insert(
                    "missing",
                    "end",
                    iid=f"miss::{name}",
                    text=name,
                    values=("no address on this ROM",),
                    tags=("category",),
                )

        pick = None
        if self._jump_to_change and self._jump_target and self.tree.exists(self._jump_target):
            pick = self._prefer_changed_iid(self._jump_target)
        elif self._jump_to_change and first_changed:
            pick = self._prefer_changed_iid(first_changed)
        elif prev_id and self.tree.exists(prev_id) and not str(prev_id).startswith("cat::") and prev_id not in {"chgcat", "missing"}:
            pick = prev_id
        elif self._restore_table and not self._hold_restore:
            plain = str(self._restore_table)
            if self.tree.exists(plain):
                pick = self._prefer_changed_iid(plain)
            elif self.tree.exists(f"miss::{plain}"):
                pick = f"miss::{plain}"
        elif needle and first_table:
            pick = first_table
        self._jump_to_change = False
        if not self._hold_restore:
            self._restore_table = None
        if pick:
            self._focus_tree_table(pick)
            if self._jump_cell and _table_name(pick) == self._jump_target:
                self._select_cell(*self._jump_cell)
            self._jump_cell = None
            self._jump_target = None

    def _show_selected_table(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("cat::") or iid in {"chgcat", "missing"}:
            return
        self._render_table(_table_name(iid))

    def _refresh_view(self) -> None:
        if self._current_table:
            self._render_table(self._current_table)

    def _clear_grid(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._grid_cells.clear()
        self._row_headers.clear()
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)

    def _label(
        self,
        r: int,
        c: int,
        text: str,
        bg: str = HEADER_BG,
        table: str | None = None,
        y: int | None = None,
        x: int | None = None,
        fg: str | None = None,
        bold: bool = False,
    ) -> tk.Label:
        if fg is None:
            fg = text_on(bg)
        font = ("Consolas", 11, "bold") if bold else ("Consolas", 11)
        longest = max((len(line) for line in text.split("\n")), default=1)
        width = max(6, min(22, longest + 1))
        lbl = tk.Label(
            self.grid_frame,
            text=text,
            bg=bg,
            fg=fg,
            relief="solid",
            bd=1,
            padx=6,
            pady=4,
            font=font,
            width=width,
            anchor="center",
        )
        lbl.grid(row=r, column=c, sticky="nsew")
        if table is not None and y is not None and x is not None:
            self._grid_cells[(y, x)] = lbl
            lbl.bind("<Enter>", lambda _e, yy=y, xx=x: self._hover_cell(yy, xx))
            lbl.bind("<Button-1>", lambda _e, yy=y, xx=x: self._select_cell(yy, xx, take_focus=True))
            lbl.bind("<Button-3>", lambda _e, t=text: self._copy_text(t))
            lbl.bind("<MouseWheel>", self._on_mousewheel)
            if self.edit_enabled and not is_immobilizer_table(table):
                lbl.bind("<Double-1>", lambda _e, yy=y, xx=x: self._edit_cell(yy, xx))
        return lbl

    def _hover_cell(self, y: int, x: int) -> None:
        if not self._current_table:
            return
        ta = self.rom_a.tables.get(self._current_table) if self.rom_a else None
        tb = self.rom_b.tables.get(self._current_table) if self.rom_b else None
        table = self._display_table()
        if table is None or y >= table.rows() or x >= table.cols():
            return
        fmt = table.definition.format
        units = table.units

        def fmt_one(dec: DecodedTable | None) -> str:
            if dec is None or y >= dec.rows() or x >= dec.cols():
                return "n/a"
            return format_scaled(dec.data_scaled[y][x], fmt, dec.data_errors[y][x])

        a_txt = fmt_one(ta)
        b_txt = fmt_one(tb)
        addr = table.cell_address(y, x)
        addr_s = f"0x{addr:X}" if addr is not None else "?"
        extra = f" {units}" if units else ""
        loc = []
        if table.y_axis and y < len(table.y_axis.scaled):
            loc.append(f"{table.y_axis.name or 'Y'}={table.y_axis.label(y)}")
        if table.x_axis and x < len(table.x_axis.scaled):
            loc.append(f"{table.x_axis.name or 'X'}={table.x_axis.label(x)}")
        loc_s = ("  " + ", ".join(loc)) if loc else ""
        hint = "  ·  double-click to edit" if self.edit_enabled else "  ·  arrows move, right-click copies"
        place = ""
        cells = self._changed_cells()
        if (y, x) in cells:
            place = f"  ·  changed cell {cells.index((y, x)) + 1} of {len(cells)}"
        self.cell_status.set(f"{addr_s}{loc_s}{place}{hint}")
        self._show_cell_detail(y, x, a_txt, b_txt, extra, addr_s)

    def _fill_change_list(self, name: str | None) -> None:
        if not hasattr(self, "changes"):
            return
        self.changes.delete(*self.changes.get_children())
        if not name or self.result is None:
            return
        diff = next((item for item in self.result.table_diffs if item.name == name), None)
        if diff is None:
            return
        for index, cell in enumerate(diff.cells):
            where = cell.location_text() or f"row {cell.y}, col {cell.x}"
            tag = "up"
            if cell.scaled_a is not None and cell.scaled_b is not None and cell.scaled_b < cell.scaled_a:
                tag = "down"
            arrow = "▲ " if tag == "up" else "▼ "
            self.changes.insert(
                "",
                "end",
                iid=f"{cell.y}:{cell.x}:{index}",
                values=(where, cell.scaled_text("a"), cell.scaled_text("b"), arrow + (cell.delta_text() or "—")),
                tags=(tag,),
            )

    def _changed_cells(self) -> list[tuple[int, int]]:
        name = self._current_table
        if not name or self.result is None:
            return []
        diff = next((item for item in self.result.table_diffs if item.name == name), None)
        if diff is None:
            return []
        return [(cell.y, cell.x) for cell in diff.cells if cell.kind != "axis"]

    def _pick_change_row(self) -> None:
        if getattr(self, "_ignore_change_select", False):
            return
        sel = self.changes.selection()
        if not sel:
            return
        parts = str(sel[0]).split(":")
        if len(parts) < 2:
            return
        try:
            y, x = int(parts[0]), int(parts[1])
        except ValueError:
            return
        # selection_set posts <<TreeviewSelect>> after this handler returns.
        # Selecting the cell we already show would post that event forever.
        if self._selected_cell == (y, x):
            return
        self._select_cell(y, x, take_focus=True)

    def _release_change_select(self) -> None:
        self._ignore_change_select = False

    def _highlight_change_row(self, y: int, x: int) -> None:
        if not hasattr(self, "changes"):
            return
        prefix = f"{y}:{x}:"
        for iid in self.changes.get_children():
            if not str(iid).startswith(prefix):
                continue
            current = self.changes.selection()
            if current and str(current[0]).startswith(prefix):
                return
            self._ignore_change_select = True
            try:
                self.changes.selection_set(iid)
                self.changes.see(iid)
            finally:
                # The virtual event is delivered after this call returns.
                self.after_idle(self._release_change_select)
            return

    def _scroll_cell_into_view(self, y: int, x: int) -> None:
        label = self._grid_cells.get((y, x))
        if label is None:
            return
        self.canvas.update_idletasks()
        frame_w = self.grid_frame.winfo_width()
        frame_h = self.grid_frame.winfo_height()
        view_w = self.canvas.winfo_width()
        view_h = self.canvas.winfo_height()
        if frame_h > 1 and frame_h > view_h:
            self.canvas.yview_moveto(min(1.0, max(0.0, (label.winfo_y() - 8) / frame_h)))
        if frame_w > 1 and frame_w > view_w:
            self.canvas.xview_moveto(min(1.0, max(0.0, (label.winfo_x() - 40) / frame_w)))

    def _step_changed_cell(self, delta: int) -> None:
        cells = self._changed_cells()
        if not cells:
            self.status.set("This map has no changed cells.")
            return
        current = self._selected_cell
        if current in cells:
            index = (cells.index(current) + delta) % len(cells)
        else:
            index = 0 if delta > 0 else len(cells) - 1
        y, x = cells[index]
        self._select_cell(y, x, take_focus=True)
        self.status.set(f"Changed cell {index + 1} of {len(cells)}")

    def _arrow_cell(self, event: tk.Event):
        widget = self.focus_get()
        in_grid = isinstance(widget, tk.Label) and getattr(widget, "master", None) is self.grid_frame
        if widget not in (self, self.canvas, self.grid_frame) and not in_grid:
            return None
        table = self._display_table()
        if table is None or table.rows() < 1 or table.cols() < 1:
            return None
        y, x = self._selected_cell or (0, 0)
        if event.keysym == "Left":
            x = max(0, x - 1)
        elif event.keysym == "Right":
            x = min(table.cols() - 1, x + 1)
        elif event.keysym == "Up":
            y = max(0, y - 1)
        elif event.keysym == "Down":
            y = min(table.rows() - 1, y + 1)
        else:
            return None
        self._select_cell(y, x, take_focus=True)
        return "break"

    def _select_cell(self, y: int, x: int, take_focus: bool = False) -> None:
        self._selected_cell = (y, x)
        self._hover_cell(y, x)
        self._highlight_change_row(y, x)
        self.after_idle(lambda yy=y, xx=x: self._scroll_cell_into_view(yy, xx))
        if take_focus:
            self.canvas.focus_set()
        if self._chart_mode == "row" and self._chart_row != y:
            self._show_map_row(y)
            return
        self._mark_selected_cell()
        self._draw_chart()

    def _show_cell_detail(self, y: int, x: int, a_txt: str, b_txt: str, extra: str, addr_s: str) -> None:
        table = self._display_table()
        if table is None:
            return
        ta = self.rom_a.tables.get(self._current_table) if self.rom_a and self._current_table else None
        tb = self.rom_b.tables.get(self._current_table) if self.rom_b and self._current_table else None
        a_num = ta.data_scaled[y][x] if ta and y < ta.rows() and x < ta.cols() else None
        b_num = tb.data_scaled[y][x] if tb and y < tb.rows() and x < tb.cols() else None
        fmt = table.definition.format
        if a_num is not None and b_num is not None:
            delta = f"Δ {signed_delta(a_num, b_num, fmt)}{extra}"
        elif self.rom_b is None:
            delta = ""
        else:
            delta = ""
        if self.rom_b is None:
            self.cell_detail.set(f"{addr_s}    {a_txt}{extra}")
            self._paint_cell_detail(y, x, a_num, b_num)
            return
        self.cell_detail.set(f"A  {a_txt}{extra}      B  {b_txt}{extra}      {delta}".strip())
        self._paint_cell_detail(y, x, a_num, b_num)

    def _paint_cell_detail(self, y: int, x: int, a_num: float | None, b_num: float | None) -> None:
        bg = "#E8F6FB" if self.rom_b is None else "#F4F7FB"
        if self.rom_a is not None and self.rom_b is not None and self._cell_changed(y, x):
            if a_num is not None and b_num is not None and b_num > a_num:
                bg = UP_BG
            elif a_num is not None and b_num is not None and b_num < a_num:
                bg = DOWN_BG
            else:
                bg = CHANGED_BG
        self.cell_detail_label.configure(bg=bg, fg=text_on(bg))

    def _display_table(self) -> DecodedTable | None:
        if not self._current_table:
            return None
        mode = self.view_mode.get()
        if mode == "a" and self.rom_a and self._current_table in self.rom_a.tables:
            return self.rom_a.tables[self._current_table]
        if mode == "b" and self.rom_b and self._current_table in self.rom_b.tables:
            return self.rom_b.tables[self._current_table]
        rom = self.rom_b or self.rom_a
        if rom and self._current_table in rom.tables:
            return rom.tables[self._current_table]
        return None

    def _cell_changed(self, y: int, x: int) -> bool:
        if not self._current_table or self.rom_a is None or self.rom_b is None:
            return False
        ta = self.rom_a.tables.get(self._current_table)
        tb = self.rom_b.tables.get(self._current_table)
        if ta is None or tb is None:
            return False
        if y >= min(ta.rows(), tb.rows()) or x >= min(ta.cols(), tb.cols()):
            return False
        return ta.data_raw[y][x] != tb.data_raw[y][x]

    def _hide_row_bar(self) -> None:
        self._chart_mode = None
        self._chart_row = None
        if self.row_bar.winfo_ismapped():
            self.row_bar.pack_forget()

    def _hide_chart(self) -> None:
        self._hide_row_bar()
        self._chart_a = None
        self._chart_b = None
        self._plot_box = None
        self._chart_size = None
        if self.chart.winfo_ismapped():
            self.chart.pack_forget()
        self.chart.delete("all")

    def _pack_chart(self, with_row_bar: bool) -> None:
        if with_row_bar and not self.row_bar.winfo_ismapped():
            self.row_bar.pack(fill="x", before=self.grid_wrap, pady=(6, 0))
        if not with_row_bar and self.row_bar.winfo_ismapped():
            self.row_bar.pack_forget()
        if not self.chart.winfo_ismapped():
            self.chart.pack(fill="x", before=self.grid_wrap, pady=(4, 0))
        self._chart_size = None
        self.update_idletasks()

    def _curve_focus(
        self, series: list[CurvePoint], other: list[CurvePoint] | None
    ) -> tuple[int, int] | None:
        """Keep the current point, otherwise the largest A-to-B step, otherwise the first point."""
        keep = self._selected_cell
        if keep is not None and any((point.y_index, point.x_index) == keep for point in series):
            return None
        best: tuple[int, int] | None = None
        best_mag = -1.0
        if other is not None:
            for left, right in zip(series, other):
                if left.y is None or right.y is None or left.y == right.y:
                    continue
                mag = abs(right.y - left.y)
                if mag > best_mag:
                    best_mag = mag
                    best = (left.y_index, left.x_index)
        if best is None and series:
            first = series[0]
            best = (first.y_index, first.x_index)
        return best

    def _present_chart(
        self, table: DecodedTable, ta: DecodedTable | None, tb: DecodedTable | None
    ) -> None:
        base = curve_points(table)
        if not base:
            self._hide_chart()
            return
        a_series = values_on(base, ta) if ta is not None else None
        b_series = values_on(base, tb) if tb is not None else None
        if a_series is None and b_series is None:
            a_series = base
        self._hide_row_bar()
        self._chart_mode = "curve"
        self._chart_a = a_series
        self._chart_b = b_series
        self._chart_x_title = curve_axis_title(table)
        self._chart_y_title = table.units or "Value"
        self._chart_format = table.definition.format or ""
        self._pack_chart(with_row_bar=False)
        primary = a_series or b_series or []
        other = b_series if a_series is not None else None
        focus = self._curve_focus(primary, other)
        if focus is not None:
            self._select_cell(*focus)
        else:
            self._draw_chart()

    def _load_row_series(
        self,
        table: DecodedTable,
        ta: DecodedTable | None,
        tb: DecodedTable | None,
        y: int,
    ) -> None:
        base = row_points(table, y)
        a_series = values_on(base, ta) if ta is not None and base else None
        b_series = values_on(base, tb) if tb is not None and base else None
        if a_series is None and b_series is None:
            a_series = base or None
        self._chart_mode = "row"
        self._chart_row = y
        self._chart_a = a_series
        self._chart_b = b_series
        self._chart_x_title = axis_title(table.x_axis)
        self._chart_y_title = table.units or "Value"
        self._chart_format = table.definition.format or ""

    def _update_row_caption(self, table: DecodedTable, y: int) -> None:
        axis_name = table.y_axis.name if table.y_axis and table.y_axis.name else "Row"
        label = table.y_axis.label(y) if table.y_axis else str(y)
        changed = sum(1 for x in range(table.cols()) if self._cell_changed(y, x))
        extra = f"     ·     {changed} changed in this row" if self.rom_a and self.rom_b else ""
        self.row_caption.set(f"{axis_name}:  {label}      row {y + 1} of {table.rows()}{extra}")

    def _present_map(
        self, table: DecodedTable, ta: DecodedTable | None, tb: DecodedTable | None
    ) -> None:
        if table.cols() < 2 or table.rows() < 1:
            self._hide_chart()
            return
        if self._selected_cell is not None and 0 <= self._selected_cell[0] < table.rows():
            row = self._selected_cell[0]
        else:
            row = best_changed_row(ta, tb, table.rows())
        self._load_row_series(table, ta, tb, row)
        self._update_row_caption(table, row)
        self._pack_chart(with_row_bar=True)
        primary = self._chart_a or self._chart_b or []
        other = self._chart_b if self._chart_a is not None else None
        focus = self._curve_focus(primary, other)
        if focus is not None:
            self._select_cell(*focus)
        else:
            self._draw_chart()

    def _show_map_row(self, y: int) -> None:
        table = self._display_table()
        if table is None or not (0 <= y < table.rows()):
            self._draw_chart()
            return
        name = self._current_table
        ta = self.rom_a.tables.get(name) if self.rom_a and name else None
        tb = self.rom_b.tables.get(name) if self.rom_b and name else None
        self._load_row_series(table, ta, tb, y)
        self._update_row_caption(table, y)
        self._mark_active_row()
        self._mark_selected_cell()
        self._draw_chart()
        self._scroll_row_into_view(y)

    def _row_has_change(self, y: int) -> bool:
        table = self._display_table()
        if table is None:
            return False
        return any(self._cell_changed(y, x) for x in range(table.cols()))

    def _mark_active_row(self) -> None:
        for y, label in self._row_headers.items():
            if y == self._chart_row:
                label.configure(bg="#1A4F86", fg="#FFFFFF")
            elif self._row_has_change(y):
                label.configure(bg=UP_BG, fg="#111111")
            else:
                label.configure(bg=AXIS_BG, fg="#111111")

    def _mark_selected_cell(self) -> None:
        selected = self._selected_cell
        for key, label in self._grid_cells.items():
            label.configure(font=("Consolas", 11, "bold" if key == selected else "normal"))

    def _scroll_row_into_view(self, y: int) -> None:
        header = self._row_headers.get(y)
        if header is None:
            return
        self.canvas.update_idletasks()
        frame_h = self.grid_frame.winfo_height()
        view_h = self.canvas.winfo_height()
        if frame_h <= 1 or frame_h <= view_h:
            return
        top = header.winfo_y()
        self.canvas.yview_moveto(min(1.0, max(0.0, (top - 8) / frame_h)))

    def _activate_row(self, y: int) -> None:
        table = self._display_table()
        if table is None or table.rows() < 1:
            return
        x = 0
        if self._selected_cell is not None:
            x = min(max(self._selected_cell[1], 0), max(table.cols() - 1, 0))
        self._select_cell(y, x, take_focus=True)

    def _step_row(self, delta: int) -> None:
        if self._chart_mode != "row" or self._chart_row is None:
            return
        table = self._display_table()
        if table is None or table.rows() < 1:
            return
        y = (self._chart_row + delta) % table.rows()
        x = 0
        if self._selected_cell is not None:
            x = min(max(self._selected_cell[1], 0), max(table.cols() - 1, 0))
        self._select_cell(y, x, take_focus=True)

    def _step_changed_row(self, delta: int = 1) -> None:
        table = self._display_table()
        if table is None or self._chart_mode != "row" or table.rows() < 1:
            return
        changed = [y for y in range(table.rows()) if self._row_has_change(y)]
        if not changed:
            self.status.set("This map has no changed row.")
            return
        if len(changed) == 1 and changed[0] == self._chart_row:
            self.status.set("This is the only changed row.")
            return
        start = self._chart_row or 0
        ordered = changed if delta > 0 else list(reversed(changed))
        nxt = next((y for y in ordered if (y - start) * delta > 0), ordered[0])
        best_x = 0
        best_mag = -1.0
        name = self._current_table
        ta = self.rom_a.tables.get(name) if self.rom_a and name else None
        tb = self.rom_b.tables.get(name) if self.rom_b and name else None
        if ta is not None and tb is not None:
            for x in range(min(ta.cols(), tb.cols(), table.cols())):
                if not self._cell_changed(nxt, x):
                    continue
                a = ta.data_scaled[nxt][x]
                b = tb.data_scaled[nxt][x]
                if a is None or b is None:
                    continue
                mag = abs(float(b) - float(a))
                if mag > best_mag:
                    best_mag = mag
                    best_x = x
        self._select_cell(nxt, best_x, take_focus=True)

    def _draw_chart(self) -> None:
        if not self.chart.winfo_ismapped():
            return
        width = int(self.chart.winfo_width())
        height = int(self.chart.winfo_height())
        if width < 80 or height < 40:
            return
        a_series = self._chart_a or []
        b_series = self._chart_b or []
        source = a_series or b_series
        if not source:
            return
        ys = [point.y for point in a_series + b_series if point.y is not None]
        if not ys:
            return
        box = plot_box(width, height, [point.x for point in source], ys)
        self._plot_box = box
        self.chart.delete("all")
        for tick in y_ticks(ys, 4):
            y = box.py(tick)
            self.chart.create_line(box.left, y, box.right, y, fill="#E1E6EB")
            self.chart.create_text(
                box.left - 8,
                y,
                text=format_scaled(tick, self._chart_format),
                anchor="e",
                fill="#111111",
                font=("Segoe UI", 11),
            )
        self.chart.create_rectangle(box.left, box.top, box.right, box.bottom, outline="#111111")
        for index in x_tick_indices(len(source), box.right - box.left):
            point = source[index]
            x = box.px(point.x)
            self.chart.create_line(x, box.bottom, x, box.bottom + 5, fill="#111111")
            self.chart.create_text(
                x,
                box.bottom + 8,
                text=point.x_text,
                anchor="n",
                fill="#111111",
                font=("Segoe UI", 10),
            )
        self.chart.create_text(
            box.left,
            14,
            text=self._chart_y_title,
            anchor="w",
            fill="#111111",
            font=("Segoe UI", 12, "bold"),
        )
        self.chart.create_text(
            box.right,
            14,
            text=self._chart_x_title,
            anchor="e",
            fill="#111111",
            font=("Segoe UI", 12, "bold"),
        )
        matched = series_match(a_series, b_series)
        if a_series and b_series and not matched:
            for coords, color in fill_bands(a_series, b_series, box):
                self.chart.create_polygon(*coords, fill=color, outline="")
        selected = self._selected_cell
        if selected is not None:
            for point in source:
                if (point.y_index, point.x_index) == selected:
                    x = box.px(point.x)
                    self.chart.create_line(x, box.top, x, box.bottom, fill="#7A8694", dash=(3, 2))
                    break

        def stroke(points: list[CurvePoint], color: str) -> None:
            for segment in line_segments(points, box):
                flat = [coord for xy in segment for coord in xy]
                self.chart.create_line(*flat, fill=color, width=3, capstyle="round", joinstyle="round")
            for point in points:
                if point.y is None:
                    continue
                x, y = box.px(point.x), box.py(point.y)
                radius = 6 if selected == (point.y_index, point.x_index) else 3.5
                self.chart.create_oval(
                    x - radius, y - radius, x + radius, y + radius, fill=color, outline="#111111"
                )

        if a_series and not (matched and b_series):
            stroke(a_series, A_LINE)
        if b_series:
            stroke(b_series, A_LINE if matched else B_LINE)
        self._draw_legend(box, bool(a_series), bool(b_series), matched)

    def _draw_legend(self, box: PlotBox, has_a: bool, has_b: bool, matched: bool) -> None:
        x = box.left + 12
        y = box.top + 14
        font = ("Segoe UI", 11, "bold")
        if matched and has_a and has_b:
            entries = [(A_LINE, "A and B match")]
        else:
            entries = []
            if has_a:
                entries.append((A_LINE, "File A"))
            if has_b:
                entries.append((B_LINE, "File B"))
        for color, text in entries:
            self.chart.create_line(x, y, x + 26, y, fill=color, width=3, capstyle="round")
            self.chart.create_text(x + 32, y, text=text, anchor="w", fill="#111111", font=font)
            y += 20

    def _on_chart_configure(self, event: tk.Event) -> None:
        if event.widget is not self.chart or self._chart_drawing:
            return
        if event.width < 80 or event.height < 40:
            return
        size = (int(event.width), int(event.height))
        if size == self._chart_size:
            return
        self._chart_size = size
        self._chart_drawing = True
        try:
            self._draw_chart()
        finally:
            self._chart_drawing = False

    def _on_chart_click(self, event: tk.Event) -> None:
        source = self._chart_a or self._chart_b
        if not source or self._plot_box is None:
            return
        index = nearest_index(source, self._plot_box, float(event.x))
        if index is None or index < 0 or index >= len(source):
            return
        point = source[index]
        self._select_cell(point.y_index, point.x_index, take_focus=True)

    def _render_table(self, name: str) -> None:
        if name != self._current_table:
            self._selected_cell = None
        self._current_table = name
        self._clear_grid()
        table = self._display_table()
        if table is None:
            self.table_title.set("Table not in this file")
            self.table_meta.set("")
            self.range_line.set("")
            self._fill_change_list(None)
            self._hide_chart()
            return
        ta = self.rom_a.tables.get(name) if self.rom_a else None
        tb = self.rom_b.tables.get(name) if self.rom_b else None
        kind = (table.definition.table_type or "").lower()
        addr = table.definition.storageaddress
        addr_s = f"0x{addr:X}" if addr is not None else "no address"
        changed_n = 0
        if self.result:
            for d in self.result.table_diffs:
                if d.name == name:
                    changed_n = d.changed_count
        shape = f"{table.cols()} points" if kind != "3d" else f"{table.cols()} × {table.rows()}"
        self.table_title.set(name)
        meta = [table.category, (kind or "map").upper(), shape, addr_s]
        if table.units:
            meta.append(table.units)
        if changed_n:
            meta.append(f"{changed_n} changed")
        self.table_meta.set("    ·    ".join(meta))
        self.table_desc.set(table.definition.description or "")
        self._sync_desc_wrap()
        self._fill_change_list(name)
        vmin, vmax = _value_range(table)
        units = table.units or ""
        if vmin == vmax:
            span = f"{format_scaled(vmin, table.definition.format)} {units}".strip()
        else:
            span = (
                f"{format_scaled(vmin, table.definition.format)} … "
                f"{format_scaled(vmax, table.definition.format)} {units}"
            ).strip()
        self.range_line.set(
            "Range "
            + span
            + "     pale = low, blue = high, pink = File B higher, blue = File B lower"
        )
        if table.decode_error:
            self._hide_chart()
            self._label(0, 0, f"Decode error: {table.decode_error}")
            return
        if kind == "1d" and table.rows() == 1 and table.cols() == 1:
            self._hide_chart()
            self._render_scalar(name, table, ta, tb)
            return
        if kind == "switch":
            self._hide_chart()
            sa = ta.switch_state if ta else "?"
            sb = tb.switch_state if tb else table.switch_state
            self._label(0, 0, f"A: {sa}")
            self._label(1, 0, f"B: {sb}", bg=CHANGED_BG if sa != sb else HEADER_BG)
            return
        if kind == "3d" and table.cols() >= 2:
            self._present_map(table, ta, tb)
            hint = self.range_line.get()
            self.range_line.set(
                hint + "     graph shows this row. A changed cell shows File B, then the change."
            )
        elif is_curve_table(table):
            self._present_chart(table, ta, tb)
            hint = self.range_line.get()
            self.range_line.set(hint + "     navy line = File A, red line = File B")
        else:
            self._hide_chart()

        show_diff = self.view_mode.get() == "diff" and ta is not None and tb is not None

        def cell_payload(y: int, x: int, stacked: bool = False) -> tuple[str, str]:
            val = format_scaled(table.data_scaled[y][x], table.definition.format, table.data_errors[y][x])
            changed = self._cell_changed(y, x)
            text = val
            bg = heatmap_color(table.data_scaled[y][x], vmin, vmax)
            if show_diff and changed and ta is not None and tb is not None:
                a_num = ta.data_scaled[y][x]
                b_num = tb.data_scaled[y][x]
                if stacked:
                    text = diff_cell_text(a_num, b_num, table.definition.format)
                else:
                    text = signed_delta(a_num, b_num, table.definition.format)
                if a_num is not None and b_num is not None and b_num > a_num:
                    bg = UP_BG
                elif a_num is not None and b_num is not None and b_num < a_num:
                    bg = DOWN_BG
                else:
                    bg = CHANGED_BG
            return text, bg

        if kind == "3d":
            corner = f"{(table.y_axis.name if table.y_axis else 'Y')} \\ {(table.x_axis.name if table.x_axis else 'X')}"
            self._label(0, 0, corner, bg=AXIS_BG, bold=True)
            for x in range(table.cols()):
                hdr = table.x_axis.label(x) if table.x_axis else str(x)
                self._label(0, x + 1, hdr, bg=AXIS_BG, bold=True)
            for y in range(table.rows()):
                hdr = table.y_axis.label(y) if table.y_axis else str(y)
                header = self._label(y + 1, 0, hdr, bg=AXIS_BG, bold=True)
                self._row_headers[y] = header
                header.bind("<Button-1>", lambda _e, yy=y: self._activate_row(yy))
                header.bind("<MouseWheel>", self._on_mousewheel)
                for x in range(table.cols()):
                    text, bg = cell_payload(y, x, stacked=True)
                    self._label(y + 1, x + 1, text, bg=bg, table=name, y=y, x=x)
            self._mark_active_row()
            self._mark_selected_cell()
            if self._chart_row is not None:
                shown = self._chart_row
                self.after_idle(lambda y=shown: self._scroll_row_into_view(y))
            return

        # 1D / 2D: one horizontal strip (tuner-style), plus an A row when diffing
        n = table.cols() if table.rows() == 1 else table.rows()
        axis_name = (table.y_axis.name if table.y_axis else None) or "Axis"
        self._label(0, 0, axis_name, bg=AXIS_BG, bold=True)
        for i in range(n):
            axis = table.y_axis.label(i) if table.y_axis and i < len(table.y_axis.scaled) else str(i)
            self._label(0, i + 1, axis, bg=AXIS_BG, bold=True)

        def row_of(decoded: DecodedTable, r: int, tag: str, as_diff: bool) -> None:
            self._label(r, 0, tag, bg=HEADER_BG, bold=True)
            lo, hi = _value_range(decoded)
            for i in range(n):
                y, x = (0, i) if decoded.rows() == 1 else (i, 0)
                if y >= decoded.rows() or x >= decoded.cols():
                    self._label(r, i + 1, "")
                    continue
                if as_diff:
                    text, bg = cell_payload(y, x)
                else:
                    text = format_scaled(
                        decoded.data_scaled[y][x], decoded.definition.format, decoded.data_errors[y][x]
                    )
                    bg = heatmap_color(decoded.data_scaled[y][x], lo, hi)
                    if self._cell_changed(y, x) and show_diff and tag == "B" and ta is not None and tb is not None:
                        a_num = ta.data_scaled[y][x] if y < ta.rows() and x < ta.cols() else None
                        b_num = tb.data_scaled[y][x] if y < tb.rows() and x < tb.cols() else None
                        if a_num is not None and b_num is not None and b_num > a_num:
                            bg = UP_BG
                        elif a_num is not None and b_num is not None and b_num < a_num:
                            bg = DOWN_BG
                        else:
                            bg = CHANGED_BG
                self._label(r, i + 1, text, bg=bg, table=name, y=y, x=x)

        if show_diff and ta is not None and tb is not None:
            row_of(ta, 1, "A", as_diff=False)
            row_of(tb, 2, "B", as_diff=False)
            self._label(3, 0, "Δ", bg=HEADER_BG, bold=True)
            for i in range(n):
                y, x = (0, i) if table.rows() == 1 else (i, 0)
                if (
                    y >= ta.rows()
                    or x >= ta.cols()
                    or y >= tb.rows()
                    or x >= tb.cols()
                ):
                    self._label(3, i + 1, "")
                    continue
                changed = ta.data_raw[y][x] != tb.data_raw[y][x]
                a_num = ta.data_scaled[y][x]
                b_num = tb.data_scaled[y][x]
                text = signed_delta(a_num, b_num, table.definition.format)
                if not changed:
                    bg = "#FFFFFF"
                    text = "0"
                elif a_num is not None and b_num is not None and b_num > a_num:
                    bg = UP_BG
                elif a_num is not None and b_num is not None and b_num < a_num:
                    bg = DOWN_BG
                else:
                    bg = CHANGED_BG
                self._label(3, i + 1, text, bg=bg, table=name, y=y, x=x)
        else:
            tag = "A" if self.view_mode.get() == "a" else ("B" if self.view_mode.get() == "b" else "Value")
            if self.rom_b is None:
                tag = "Value"
            row_of(table, 1, tag, as_diff=False)

    def _big(
        self,
        r: int,
        c: int,
        text: str,
        bg: str,
        *,
        table: str | None = None,
        y: int | None = None,
        x: int | None = None,
    ) -> None:
        fg = text_on(bg)
        lbl = tk.Label(
            self.grid_frame,
            text=text,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 28, "bold"),
            padx=18,
            pady=14,
            relief="solid",
            bd=1,
        )
        lbl.grid(row=r, column=c, sticky="nsew")
        if table is not None and y is not None and x is not None:
            lbl.bind("<Button-1>", lambda _e, yy=y, xx=x: self._select_cell(yy, xx, take_focus=True))

    def _render_scalar(self, name: str, table: DecodedTable, ta: DecodedTable | None, tb: DecodedTable | None) -> None:
        units = table.units or ""

        def shown(dec: DecodedTable | None) -> tuple[str, float | None]:
            if dec is None or not dec.data_scaled or not dec.data_scaled[0]:
                return "n/a", None
            text = format_scaled(dec.data_scaled[0][0], dec.definition.format, dec.data_errors[0][0])
            return f"{text} {units}".strip(), dec.data_scaled[0][0]

        if ta is not None and tb is not None and ta.data_raw and tb.data_raw:
            a_txt, a_num = shown(ta)
            b_txt, b_num = shown(tb)
            changed = ta.data_raw[0][0] != tb.data_raw[0][0]
            if changed and a_num is not None and b_num is not None and b_num > a_num:
                bg = UP_BG
            elif changed and a_num is not None and b_num is not None and b_num < a_num:
                bg = DOWN_BG
            elif changed:
                bg = CHANGED_BG
            else:
                bg = "#FFFFFF"
            delta = signed_delta(a_num, b_num, table.definition.format)
            self._big(0, 0, "Was", HEADER_BG)
            self._big(0, 1, a_txt, "#FFFFFF")
            self._big(1, 0, "Now", HEADER_BG)
            self._big(1, 1, b_txt, bg, table=name, y=0, x=0)
            self._big(2, 0, "Change", HEADER_BG)
            self._big(2, 1, f"{delta} {units}".strip() if changed else "no change", bg)
            self._select_cell(0, 0)
            return
        text, _num = shown(table)
        self._big(0, 0, text, "#E8F6FB", table=name, y=0, x=0)
        self._select_cell(0, 0)

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set(f"Copied: {text}")

    def _copy_address(self) -> None:
        table = self._display_table()
        if table is None or table.definition.storageaddress is None:
            return
        self._copy_text(f"0x{table.definition.storageaddress:X}")

    def _table_as_text(self) -> str:
        table = self._display_table()
        if table is None:
            return ""
        lines = [
            table.name,
            f"Category: {table.category}",
            f"Address: 0x{table.definition.storageaddress:X}"
            if table.definition.storageaddress is not None
            else "Address: n/a",
            f"Type: {table.definition.table_type}  Units: {table.units or '-'}",
            table.definition.description,
            "",
        ]
        kind = (table.definition.table_type or "").lower()
        if kind == "3d":
            xhdr = [""]
            if table.x_axis:
                xhdr += [table.x_axis.label(i) for i in range(table.cols())]
            else:
                xhdr += [str(i) for i in range(table.cols())]
            lines.append("\t".join(xhdr))
            for y, row in enumerate(table.data_scaled):
                ylab = table.y_axis.label(y) if table.y_axis else str(y)
                cells = [format_scaled(v, table.definition.format, table.data_errors[y][i]) for i, v in enumerate(row)]
                lines.append("\t".join([ylab] + cells))
        else:
            n = table.cols() if table.rows() == 1 else table.rows()
            axis = []
            vals = []
            for i in range(n):
                y, x = (0, i) if table.rows() == 1 else (i, 0)
                axis.append(table.y_axis.label(i) if table.y_axis and i < len(table.y_axis.scaled) else str(i))
                vals.append(format_scaled(table.data_scaled[y][x], table.definition.format, table.data_errors[y][x]))
            lines.append("\t".join(["axis"] + axis))
            lines.append("\t".join(["value"] + vals))
        return "\n".join(lines) + "\n"

    def _copy_table(self) -> None:
        text = self._table_as_text()
        if not text.strip():
            messagebox.showinfo("Copy table", "Select a table first.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set("Table copied as tab-separated text.")

    def _export_current_table(self) -> None:
        text = self._table_as_text()
        if not text.strip() or not self._current_table:
            messagebox.showinfo("Export table", "Select a table first.")
            return
        from datetime import datetime

        from ecu_compare.safety import safe_filename

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ROOT / "data" / "reports" / f"{stamp}_{safe_filename(self._current_table)}.txt"
        write_text(path, text)
        self.status.set(f"Wrote {path}")
        messagebox.showinfo("Export table", f"Wrote {path}")

    def _edit_cell(self, y: int, x: int) -> None:
        if not self.edit_enabled or self.rom_b is None or not self._current_table:
            return
        if is_immobilizer_table(self._current_table):
            messagebox.showerror("Locked", "Immobilizer tables cannot be edited.")
            return
        table = self.rom_b.tables[self._current_table]
        if y >= table.rows() or x >= table.cols():
            return
        current = table.data_scaled[y][x]
        prompt = f"New scaled value for {self._current_table} [{y},{x}]"
        answer = simpledialog.askstring(
            "Edit cell", prompt, initialvalue="" if current is None else str(current)
        )
        if answer is None or answer.strip() == "":
            return
        addr = table.cell_address(y, x)
        if addr is None:
            messagebox.showerror("Edit failed", "Table has no storage address")
            return
        width = len(table.cell_raw_bytes(self.rom_b.data, y, x))
        if width <= 0:
            messagebox.showerror("Edit failed", "Table cell has no bytes")
            return
        previous = self.rom_b.data[addr : addr + width]
        try:
            value = float(answer.strip())
            warning = apply_cell(self.rom_b, self._current_table, y, x, value)
        except (EditError, ValueError) as exc:
            messagebox.showerror("Edit failed", str(exc))
            return
        stored = self.rom_b.tables[self._current_table].data_scaled[y][x]
        note = f"{self._current_table} [{y},{x}] -> {stored if stored is not None else value}"
        self._undo.append((addr, previous, note))
        self.pending_notes.append(note)
        if self.rom_a is not None:
            self.result = compare_roms(self.rom_a, self.rom_b)
            self._compare_refresh_summary()
        self._render_table(self._current_table)
        if warning:
            self.status.set(warning + "  Not written to disk yet.")
            messagebox.showwarning("Value adjusted", warning)
        else:
            self.status.set("Cell updated in memory. Use Extra settings → Save edited copy to write a new .bin. Ctrl+Z undoes it.")

    def _undo_edit(self, _event: tk.Event | None = None):
        widget = self.focus_get()
        if isinstance(widget, (tk.Entry, ttk.Entry)):
            return None
        if not self._undo or self.rom_b is None:
            self.status.set("Nothing to undo.")
            return "break"
        addr, previous, note = self._undo.pop()
        try:
            replace_bytes(self.rom_b, addr, previous)
        except EditError as exc:
            messagebox.showerror("Undo failed", str(exc))
            return "break"
        for index in range(len(self.pending_notes) - 1, -1, -1):
            if self.pending_notes[index] == note:
                del self.pending_notes[index]
                break
        if self.rom_a is not None:
            self.result = compare_roms(self.rom_a, self.rom_b)
            self._compare_refresh_summary()
        if self._current_table:
            self._render_table(self._current_table)
        self.status.set("Undid the last edit in memory.")
        return "break"

    def _sync_desc_wrap(self) -> None:
        pane = getattr(self, "right_pane", None)
        if pane is None or not hasattr(self, "desc_label"):
            return
        width = pane.winfo_width()
        if width < 240:
            return
        target = width - 12
        if target == getattr(self, "_desc_width", None):
            return
        self._desc_width = target
        self.desc_label.configure(wraplength=target)

    def _compare_refresh_summary(self) -> None:
        r = self.result
        if not r:
            return
        size = min(r.rom_a.size, r.rom_b.size) or 1
        pct = 100.0 * r.differing_bytes / size
        self.summary.set(
            f"{r.differing_bytes} bytes differ ({pct:.4f}%)  |  "
            f"{r.tables_changed} named tables  |  "
            f"{len(r.mapped_changes)} named cells  |  "
            f"{r.unmapped_count} unmapped bytes"
        )
        self._fill_table_list()

    def _save_edited(self) -> None:
        if not self.edit_enabled:
            messagebox.showinfo("Save", "Enable map editing under Extra settings first.")
            return
        if self.rom_b is None:
            messagebox.showwarning("Save", "Load File B first (edits apply to B).")
            return
        if not messagebox.askokcancel("Save edited copy", DANGER_WARNING + "\n\n" + CHECKSUM_WARNING):
            return
        try:
            bin_path, note_path = export_edited(self.rom_b, self.pending_notes)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo("Saved", f"Wrote:\n{bin_path}\n\nNotes:\n{note_path}")

    def _export(self) -> None:
        if self.result is None:
            messagebox.showwarning("Export", "Run Compare first.")
            return
        try:
            path = write_report(self.result)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status.set(f"Report written: {path}")
        messagebox.showinfo("Report", f"Wrote {path}")

    def _open_reports(self) -> None:
        folder = ROOT / "data" / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # type: ignore[attr-defined]

    def _restore_geometry(self) -> None:
        state = _load_state()
        geo = state.get("geometry")
        if isinstance(geo, str) and "x" in geo:
            try:
                self.geometry(geo)
            except tk.TclError:
                pass
        if "named_only" in state:
            self.named_only.set(bool(state["named_only"]))
        sash = state.get("sash")
        if isinstance(sash, int):
            self.after_idle(lambda pos=sash: self._restore_sash(pos))

    def _restore_sash(self, pos: int) -> None:
        try:
            self.paned.sashpos(0, pos)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        payload = {
            "geometry": self.geometry(),
            "last_table": self._current_table,
            "named_only": bool(self.named_only.get()),
            "file_a": str(self.rom_a.path) if self.rom_a else None,
            "file_b": str(self.rom_b.path) if self.rom_b else None,
            "sash": self._sash_pos(),
        }
        if self._defs_chosen:
            payload["defs"] = str(self.defs_path)
        _save_state(payload)
        self.destroy()

    def _sash_pos(self) -> int | None:
        try:
            return int(self.paned.sashpos(0))
        except (tk.TclError, AttributeError):
            return None


def main() -> None:
    enable_windows_dpi()
    if not DEFS_PATH.is_file():
        raise SystemExit(f"Definition file missing: {DEFS_PATH}")
    app = EcuCompareApp()
    app.mainloop()


if __name__ == "__main__":
    main()
