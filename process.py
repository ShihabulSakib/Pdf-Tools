#!/usr/bin/env python3
"""
process.py — PDF N-Up Layout Processor
=======================================
CLI backend that reproduces the HTML Layout Studio geometry with pixel-perfect
fidelity and dynamic, high-fidelity terminal progress bar tracking.

All layout math is a direct translation of the JavaScript engine in pdf-layout-studio.html.

Compatible with:
  • Linux & macOS (Bash, Zsh, Fish)
  • Windows (PowerShell, Command Prompt, Windows Terminal)

Usage:
  # Single file
  python process.py config.json input.pdf
  python process.py config.json input.pdf output.pdf

  # Batch folder
  python process.py config.json input_folder/
  python process.py config.json input_folder/ output_folder/

  # Layout info
  python process.py config.json --info
"""

import sys
import os
import json
import math
import time
import shutil
import argparse
from pathlib import Path
from terminal_progress import DynamicProgressBar as SharedProgressBar

# Enable VT100 ANSI sequences on Windows 10/11 Command Prompt and PowerShell
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hOut = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        out_mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(hOut, ctypes.byref(out_mode))
        kernel32.SetConsoleMode(hOut, out_mode.value | 0x0004)
    except Exception:
        os.system("")

try:
    import fitz   # PyMuPDF
except ImportError:
    fitz = None

VERSION = "1.3.0"

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS  — MUST MATCH pdf-layout-studio.html EXACTLY
# ══════════════════════════════════════════════════════════════════════════════

PAPER_SIZES: dict[str, tuple[float, float]] = {
    "a4":      (595.0,  842.0),
    "a3":      (842.0, 1191.0),
    "a5":      (420.0,  595.0),
    "letter":  (612.0,  792.0),
    "legal":   (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
}

NUP_PRESETS: dict[int, tuple[int, int]] = {
    1:  (1, 1),
    2:  (2, 1),
    4:  (2, 2),
    6:  (3, 2),
    8:  (4, 2),
    9:  (3, 3),
    12: (4, 3),
    16: (4, 4),
}

VALID_H_ALIGN = {"left", "center", "right"}
VALID_V_ALIGN = {"top",  "center", "bottom"}

DEFAULT_CONFIG: dict = {
    "paper":       "a4",
    "orientation": "portrait",
    "nup":         4,
    "layout":      {"cols": 2, "rows": 2},
    "margin":      18.0,
    "gap":         6.0,
    "hAlign":      "center",
    "vAlign":      "center",
    "grayscale":   False,
    "invert":      False,
    "border":      False,
    "quality":     2.0,
}

PT_TO_MM = 0.3528

# ══════════════════════════════════════════════════════════════════════════════
# TERMINAL UI & DYNAMIC PROGRESS BAR
# ══════════════════════════════════════════════════════════════════════════════

class Style:
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    CYAN     = "\033[36m"
    BR_CYAN  = "\033[96m"
    GREEN    = "\033[32m"
    BR_GRN   = "\033[92m"
    YELLOW   = "\033[33m"
    BR_YEL   = "\033[93m"
    RED      = "\033[31m"
    BR_RED   = "\033[91m"
    MAGENTA  = "\033[35m"
    GRAY     = "\033[90m"
    CLEAR_LN = "\033[K"

    @classmethod
    def strip_colors(cls):
        for k in dir(cls):
            if k.isupper() and isinstance(getattr(cls, k), str):
                setattr(cls, k, "")


class DynamicProgressBar:
    """
    High-fidelity dynamic progress bar designed for Linux, macOS, and Windows.
    Displays percentage, animated blocks, counter, rate (files/sec or sheets/sec),
    elapsed & ETA, and truncated item description.
    """
    def __init__(self, total: int, title: str = "Imposition", unit: str = "files", width: int = 26, use_color: bool = True):
        self.total = max(1, total)
        self.current = 0
        self.title = title
        self.unit = unit
        self.bar_width = width
        self.start_time = time.time()
        self.last_update = 0
        self.is_tty = sys.stdout.isatty()
        self.use_color = use_color and self.is_tty

    def format_time(self, seconds: float) -> str:
        s = int(seconds)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def update(self, current: int, item_name: str = "", stage: str = "PROCESSING"):
        self.current = current
        now = time.time()
        if self.is_tty and (now - self.last_update < 0.04) and current < self.total:
            return
        self.last_update = now

        fraction = min(1.0, self.current / self.total)
        percent = fraction * 100.0

        elapsed = now - self.start_time
        rate = self.current / elapsed if elapsed > 0.05 else 0.0
        remaining = (self.total - self.current) / rate if rate > 0 else 0.0

        elapsed_str = self.format_time(elapsed)
        eta_str = self.format_time(remaining)

        filled = int(self.bar_width * fraction)
        empty = self.bar_width - filled

        term_cols = shutil.get_terminal_size((80, 20)).columns

        if self.use_color:
            S = Style
            bar_track = f"{S.BR_CYAN}{'█' * filled}{S.GRAY}{'░' * empty}{S.RESET}"
            pct_str = f"{S.BOLD}{S.BR_GRN}{percent:5.1f}%{S.RESET}"
            cnt_str = f"{S.BOLD}{self.current}/{self.total}{S.RESET}"
            time_str = f"{S.DIM}[{elapsed_str} < {eta_str}, {rate:4.1f} {self.unit[0]}/s]{S.RESET}"
            stage_str = f"{S.BR_YEL}[{stage}]{S.RESET}"
        else:
            bar_track = f"[{'█' * filled}{'░' * empty}]"
            pct_str = f"{percent:5.1f}%"
            cnt_str = f"{self.current}/{self.total}"
            time_str = f"[{elapsed_str} < {eta_str}, {rate:4.1f} {self.unit[0]}/s]"
            stage_str = f"[{stage}]"

        fixed_overhead = len(self.title) + self.bar_width + len(time_str) + len(stage) + 38
        max_name_len = max(10, term_cols - fixed_overhead)
        disp_name = item_name
        if len(disp_name) > max_name_len:
            disp_name = "…" + disp_name[-(max_name_len - 1):]

        if self.is_tty:
            sys.stdout.write(
                f"\r{Style.CLEAR_LN} {Style.BOLD}{self.title}{Style.RESET} "
                f"{bar_track} {pct_str} ({cnt_str}) {time_str} {stage_str} {disp_name}"
            )
            sys.stdout.flush()
        else:
            if self.current == 1 or self.current % max(1, self.total // 10) == 0 or self.current == self.total:
                sys.stdout.write(f"[{self.title}] {percent:5.1f}% ({self.current}/{self.total}) [{stage}] {item_name}\n")
                sys.stdout.flush()

    def complete(self, message: str = "Completed"):
        if self.is_tty:
            elapsed = self.format_time(time.time() - self.start_time)
            S = Style
            full_bar = "█" * self.bar_width
            sys.stdout.write(
                f"\r{S.CLEAR_LN} {S.BOLD}{self.title}{S.RESET} "
                f"{S.BR_GRN}{full_bar}{S.RESET} {S.BOLD}{S.BR_GRN}100.0%{S.RESET} "
                f"({self.total}/{self.total}) {S.GREEN}✔ {message}{S.RESET} in {elapsed}\n"
            )
            sys.stdout.flush()


DynamicProgressBar = SharedProgressBar


# ══════════════════════════════════════════════════════════════════════════════
# LAYOUT ENGINE — mirrors computeLayout() + fitInCell() in HTML tool
# ══════════════════════════════════════════════════════════════════════════════

def compute_layout(config: dict) -> dict:
    paper_key = str(config.get("paper", "a4")).lower()
    if paper_key not in PAPER_SIZES:
        raise ValueError(f"Unknown paper: '{paper_key}'. Valid: {sorted(PAPER_SIZES)}")

    pw, ph = PAPER_SIZES[paper_key]

    orientation = str(config.get("orientation", "portrait")).lower()
    if orientation == "landscape":
        pw, ph = ph, pw

    layout_block = config.get("layout", {})
    cols   = int(layout_block.get("cols", 1))
    rows   = int(layout_block.get("rows", 1))
    margin = float(config.get("margin", DEFAULT_CONFIG["margin"]))
    gap    = float(config.get("gap",    DEFAULT_CONFIG["gap"]))

    if cols < 1 or rows < 1:
        raise ValueError(f"Invalid grid: {cols}×{rows} — both must be ≥ 1")

    cell_w = (pw - 2.0 * margin - (cols - 1) * gap) / cols
    cell_h = (ph - 2.0 * margin - (rows - 1) * gap) / rows

    if cell_w <= 0.0 or cell_h <= 0.0:
        raise ValueError(
            f"Cell dimensions non-positive: {cell_w:.2f} × {cell_h:.2f} pt.\n"
            f"  margin={margin} pt, gap={gap} pt, grid={cols}×{rows}\n"
            f"  → Reduce margin or gap."
        )

    cells = []
    for row in range(rows):
        for col in range(cols):
            x = margin + col * (cell_w + gap)
            y = margin + row * (cell_h + gap)
            cells.append({"x": x, "y": y, "w": cell_w, "h": cell_h})

    return {
        "paper_w": pw,    "paper_h": ph,
        "cols": cols,     "rows": rows,
        "cell_w": cell_w, "cell_h": cell_h,
        "cells": cells,
    }


def fit_in_cell(
    src_w:   float,
    src_h:   float,
    cell:    dict,
    h_align: str = "center",
    v_align: str = "center",
):
    scale    = min(cell["w"] / src_w, cell["h"] / src_h)
    scaled_w = src_w * scale
    scaled_h = src_h * scale

    if h_align == "left":
        offset_x = 0.0
    elif h_align == "right":
        offset_x = cell["w"] - scaled_w
    else:
        offset_x = (cell["w"] - scaled_w) / 2.0

    if v_align == "top":
        offset_y = 0.0
    elif v_align == "bottom":
        offset_y = cell["h"] - scaled_h
    else:
        offset_y = (cell["h"] - scaled_h) / 2.0

    x0 = cell["x"] + offset_x
    y0 = cell["y"] + offset_y

    if fitz:
        return fitz.Rect(x0, y0, x0 + scaled_w, y0 + scaled_h)
    return (x0, y0, x0 + scaled_w, y0 + scaled_h)


# ══════════════════════════════════════════════════════════════════════════════
# COLOR TRANSFORM ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def transform_pixmap(pix, do_gray: bool, do_invert: bool):
    if not do_gray and not do_invert:
        return pix

    if pix.colorspace != fitz.csRGB or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)

    w, h = pix.width, pix.height

    try:
        import numpy as np
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, w, 3).copy()
        if do_gray:
            gray = (
                0.2126 * arr[:, :, 0].astype(np.float32) +
                0.7152 * arr[:, :, 1].astype(np.float32) +
                0.0722 * arr[:, :, 2].astype(np.float32)
            ).round().astype(np.uint8)
            arr[:, :, 0] = gray
            arr[:, :, 1] = gray
            arr[:, :, 2] = gray
        if do_invert:
            arr = 255 - arr
        return fitz.Pixmap(fitz.csRGB, w, h, arr.tobytes(), False)
    except ImportError:
        pass

    # Pure Python fallback
    samples = bytearray(pix.samples)
    for i in range(0, len(samples), 3):
        r, g, b = samples[i], samples[i + 1], samples[i + 2]
        if do_gray:
            gray = int(0.2126 * r + 0.7152 * g + 0.0722 * b + 0.5)
            r = g = b = gray
        if do_invert:
            r = 255 - r
            g = 255 - g
            b = 255 - b
        samples[i] = r
        samples[i + 1] = g
        samples[i + 2] = b

    return fitz.Pixmap(fitz.csRGB, w, h, bytes(samples), False)


def make_transformed_page_doc(src_doc, page_idx: int, config: dict, quality: float):
    src_page = src_doc[page_idx]
    w = src_page.rect.width
    h = src_page.rect.height

    matrix = fitz.Matrix(quality, quality)
    pix = src_page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)

    do_gray = bool(config.get("grayscale", False))
    do_invert = bool(config.get("invert", False))
    pix = transform_pixmap(pix, do_gray, do_invert)

    img_doc = fitz.open()
    img_page = img_doc.new_page(width=w, height=h)
    img_page.insert_image(fitz.Rect(0, 0, w, h), pixmap=pix)
    return img_doc


# ══════════════════════════════════════════════════════════════════════════════
# CORE PDF PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def process_pdf(
    src_path: Path,
    out_path: Path,
    config: dict,
    sheet_progress_cb = None,
) -> tuple[int, int]:
    """
    Process one PDF: build N-up layout.
    Calls sheet_progress_cb(sheet_idx, total_sheets, page_range_str) if provided.
    Returns: (num_source_pages, num_output_sheets)
    """
    if not fitz:
        raise RuntimeError("PyMuPDF ('fitz') is required. Install via: pip install pymupdf")

    src_doc = fitz.open(str(src_path))
    out_doc = fitz.open()
    layout = compute_layout(config)

    cells_per_sheet = layout["cols"] * layout["rows"]
    num_pages = src_doc.page_count
    num_sheets = math.ceil(num_pages / cells_per_sheet) if num_pages else 1

    do_border = bool(config.get("border", False))
    border_color = (0.75, 0.75, 0.75)
    border_width = 0.5

    do_transform = bool(config.get("grayscale", False)) or bool(config.get("invert", False))
    quality = float(config.get("quality", DEFAULT_CONFIG["quality"]))

    for sheet_idx in range(num_sheets):
        start_p = sheet_idx * cells_per_sheet + 1
        end_p = min(num_pages, (sheet_idx + 1) * cells_per_sheet)
        range_str = f"p{start_p}-{end_p}/{num_pages}"

        out_page = out_doc.new_page(
            width=layout["paper_w"],
            height=layout["paper_h"],
        )

        for cell_idx, cell in enumerate(layout["cells"]):
            page_idx = sheet_idx * cells_per_sheet + cell_idx
            if page_idx >= num_pages:
                break

            src_page = src_doc[page_idx]
            src_rect = src_page.rect

            fit_rect = fit_in_cell(
                src_rect.width, src_rect.height, cell,
                h_align=config.get("hAlign", "center"),
                v_align=config.get("vAlign", "center"),
            )

            if do_transform:
                tmp_doc = make_transformed_page_doc(src_doc, page_idx, config, quality)
                out_page.show_pdf_page(fit_rect, tmp_doc, 0, keep_proportion=False, overlay=True)
                tmp_doc.close()
            else:
                out_page.show_pdf_page(fit_rect, src_doc, page_idx, keep_proportion=False, overlay=True)

            if do_border:
                cell_rect = fitz.Rect(cell["x"], cell["y"], cell["x"] + cell["w"], cell["y"] + cell["h"])
                out_page.draw_rect(cell_rect, color=border_color, width=border_width)

        if sheet_progress_cb:
            sheet_progress_cb(sheet_idx + 1, num_sheets, range_str)

    src_doc.close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(str(out_path), garbage=4, deflate=True, clean=True)
    out_doc.close()

    return num_pages, num_sheets


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG LOADER & PRINTER
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cfg = dict(DEFAULT_CONFIG)
    for key in ("paper", "orientation", "margin", "gap", "hAlign", "vAlign", "grayscale", "invert", "border", "quality"):
        if key in raw:
            cfg[key] = raw[key]

    if "layout" in raw and "cols" in raw["layout"] and "rows" in raw["layout"]:
        cfg["layout"] = {
            "cols": int(raw["layout"]["cols"]),
            "rows": int(raw["layout"]["rows"]),
        }
    elif "nup" in raw:
        nup = int(raw["nup"])
        if nup not in NUP_PRESETS:
            raise ValueError(f"nup={nup} not recognized. Standard: {sorted(NUP_PRESETS)}")
        cfg["layout"] = {"cols": NUP_PRESETS[nup][0], "rows": NUP_PRESETS[nup][1]}
        cfg["nup"] = nup

    cfg["margin"]  = float(cfg["margin"])
    cfg["gap"]     = float(cfg["gap"])
    cfg["quality"] = float(cfg["quality"])

    if cfg.get("hAlign", "center") not in VALID_H_ALIGN:
        raise ValueError(f"Invalid hAlign: {cfg['hAlign']}")
    if cfg.get("vAlign", "center") not in VALID_V_ALIGN:
        raise ValueError(f"Invalid vAlign: {cfg['vAlign']}")

    return cfg


def print_banner():
    S = Style
    print(f"{S.CYAN}╭────────────────────────────────────────────────────────────────────────╮{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  {S.BOLD}{S.BR_CYAN}PDF Layout Studio — CLI N-Up Imposition Engine (v{VERSION}){S.RESET}           {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  Pixel-perfect parity with web visual studio & dynamic progress bar     {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}╰────────────────────────────────────────────────────────────────────────╯{S.RESET}")


def print_config_summary(cfg: dict):
    layout = compute_layout(cfg)
    pt2mm = lambda v: v * PT_TO_MM
    S = Style
    print(f"{S.BOLD}┌─ Layout Configuration ────────────────────────────────────────────────┐{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Target Paper : {S.BR_CYAN}{cfg['paper'].upper()} {cfg['orientation']}{S.RESET} ({layout['paper_w']:.0f}×{layout['paper_h']:.0f} pt · {pt2mm(layout['paper_w']):.1f}×{pt2mm(layout['paper_h']):.1f} mm)")
    print(f"{S.BOLD}│{S.RESET}  Grid Setup   : {S.BR_GRN}{cfg['layout']['cols']} cols × {cfg['layout']['rows']} rows{S.RESET} ({cfg['layout']['cols']*cfg['layout']['rows']} cells/sheet)")
    print(f"{S.BOLD}│{S.RESET}  Cell Size    : {layout['cell_w']:.1f} × {layout['cell_h']:.1f} pt ({pt2mm(layout['cell_w']):.1f} × {pt2mm(layout['cell_h']):.1f} mm)")
    print(f"{S.BOLD}│{S.RESET}  Margins/Gaps : Margin={cfg['margin']} pt, Gap={cfg['gap']} pt")
    print(f"{S.BOLD}│{S.RESET}  Alignment    : Horizontal={cfg['hAlign']}, Vertical={cfg['vAlign']}")
    flags = []
    if cfg['grayscale']: flags.append("grayscale")
    if cfg['invert']:    flags.append("invert")
    if cfg['border']:    flags.append("border")
    print(f"{S.BOLD}│{S.RESET}  Color/Style  : {', '.join(flags) or 'Standard vector pass'}")
    print(f"{S.BOLD}└───────────────────────────────────────────────────────────────────────┘{S.RESET}\n")


def print_summary(total_files: int, succeeded: int, failed: int, elapsed: float, out_path: Path):
    S = Style
    rate = succeeded / elapsed if elapsed > 0 else 0
    print(f"\n{S.BOLD}┌─ Process Summary ─────────────────────────────────────────────────────┐{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Files Processed      : {S.BR_CYAN}{total_files}{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Successful           : {S.BR_GRN}{succeeded}{S.RESET}")
    if failed > 0:
        print(f"{S.BOLD}│{S.RESET}  Failed               : {S.BR_RED}{failed}{S.RESET}")
    else:
        print(f"{S.BOLD}│{S.RESET}  Failed               : {S.GREEN}0{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Elapsed Time         : {S.YELLOW}{elapsed:.2f}s{S.RESET} ({rate:.1f} files/sec)")
    print(f"{S.BOLD}│{S.RESET}  Output Location      : {S.DIM}{out_path.resolve()}{S.RESET}")
    print(f"{S.BOLD}└───────────────────────────────────────────────────────────────────────┘{S.RESET}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="process.py",
        description="PDF N-Up Layout Processor with Dynamic Terminal Progress Bar",
    )
    parser.add_argument("config", type=Path, help="Path to JSON config file")
    parser.add_argument("input", type=Path, nargs="?", help="Input PDF file or folder of PDFs")
    parser.add_argument("output", type=Path, nargs="?", help="Output PDF file or folder (auto-named if omitted)")
    parser.add_argument("--info", action="store_true", help="Print config summary and exit")
    parser.add_argument("--suffix", default="_nup", help="Suffix appended to output filenames (default: _nup)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    args = parser.parse_args(argv)

    if args.no_color:
        Style.strip_colors()

    print_banner()

    if not args.config.exists():
        print(f"{Style.RED}Error:{Style.RESET} Config file not found: {args.config}", file=sys.stderr)
        return 1

    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"{Style.RED}Error in config:{Style.RESET} {e}", file=sys.stderr)
        return 1

    if args.info:
        print_config_summary(cfg)
        return 0

    if not args.input:
        parser.print_help()
        return 0

    if not args.input.exists():
        print(f"{Style.RED}Error:{Style.RESET} Input path not found: {args.input}", file=sys.stderr)
        return 1

    if not fitz:
        print(f"{Style.RED}Error:{Style.RESET} PyMuPDF is required for process.py. Install via: pip install pymupdf", file=sys.stderr)
        return 1

    print_config_summary(cfg)

    # Resolve inputs and outputs
    if args.input.is_file():
        is_batch = False
        pdf_files = [args.input]
        out_paths = [args.output if args.output else (args.input.parent / f"{args.input.stem}{args.suffix}.pdf")]
    else:
        is_batch = True
        pdf_files = sorted(args.input.glob("**/*.pdf"))
        out_dir = args.output if args.output else (args.input / "output")
        out_paths = [out_dir / f"{f.stem}{args.suffix}.pdf" for f in pdf_files]

    total_files = len(pdf_files)
    if total_files == 0:
        print(f"{Style.YELLOW}No PDF files found in:{Style.RESET} {args.input}")
        return 0

    start_time = time.time()
    succeeded = 0
    failed = 0

    if is_batch:
        # Multi-file batch mode with file-level progress bar
        print(f" {Style.GREEN}●{Style.RESET} Found {Style.BOLD}{total_files}{Style.RESET} PDF file(s). Processing batch…\n")
        pbar = DynamicProgressBar(total=total_files, title="Batch Imposition", unit="files", width=28, use_color=not args.no_color)

        for idx, (src, dst) in enumerate(zip(pdf_files, out_paths), 1):
            pbar.update(current=idx - 1, item_name=src.name, stage="IMPOSING")

            def on_sheet(cur_sheet, tot_sheets, rng):
                pbar.update(current=idx - 1 + cur_sheet / tot_sheets,
                             item_name=f"{src.name} [{cur_sheet}/{tot_sheets}]", stage="IMPOSING")

            try:
                process_pdf(src, dst, cfg, sheet_progress_cb=on_sheet)
                succeeded += 1
            except Exception as e:
                failed += 1
            finally:
                pbar.update(current=idx, item_name=src.name, stage="FINISHED")

        pbar.complete("Batch complete")
        print_summary(total_files, succeeded, failed, time.time() - start_time, out_paths[0].parent)

    else:
        # Single-file mode with sheet-level progress bar
        src = pdf_files[0]
        dst = out_paths[0]
        print(f" {Style.GREEN}●{Style.RESET} Processing {Style.BOLD}{src.name}{Style.RESET} → {Style.BOLD}{dst.name}{Style.RESET}\n")

        # Estimate sheets
        try:
            d = fitz.open(str(src))
            total_pages = d.page_count
            d.close()
            layout = compute_layout(cfg)
            per_sheet = layout["cols"] * layout["rows"]
            total_sheets = max(1, math.ceil(total_pages / per_sheet))
        except Exception:
            total_sheets = 1

        pbar = DynamicProgressBar(total=total_sheets, title="Page Imposition", unit="sheets", width=28, use_color=not args.no_color)

        def on_sheet_single(cur_sheet, tot_sheets, rng):
            pbar.update(current=cur_sheet / tot_sheets,
                         item_name=f"{src.name} ({rng})", stage="STAMPING")

        try:
            process_pdf(src, dst, cfg, sheet_progress_cb=on_sheet_single)
            succeeded += 1
            pbar.complete(f"Generated {dst.name}")
        except Exception as e:
            failed += 1
            print(f"\n{Style.RED}Error processing {src.name}:{Style.RESET} {e}", file=sys.stderr)
            pbar.update(current=total_sheets, item_name=src.name, stage="FAILED")

        print_summary(1, succeeded, failed, time.time() - start_time, dst)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
