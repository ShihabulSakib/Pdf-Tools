#!/usr/bin/env python3
"""
process.py — PDF N-Up Layout Processor
=======================================
CLI backend that reproduces the HTML Layout Studio geometry with pixel-perfect
fidelity.  All layout math is a direct translation of the JavaScript engine.

Usage
-----
# Single file
python process.py config.json input.pdf
python process.py config.json input.pdf output.pdf

# Folder batch
python process.py config.json input_folder/
python process.py config.json input_folder/ output_folder/

Requirements
------------
    pip install pymupdf          # PyMuPDF ≥ 1.23

Design principle
----------------
"Preview defines truth.  Backend reproduces it exactly."

Every constant, formula, and branch in this file mirrors the JavaScript in
pdf-layout-studio.html.  When the HTML shows a page at position (x, y) with
size (w, h) on the sheet, this script places it at the SAME position.
"""

import sys
import json
import math
import argparse
import shutil
from pathlib import Path

try:
    import fitz   # PyMuPDF
except ImportError:
    sys.exit(
        "PyMuPDF is required.  Install with:\n"
        "    pip install pymupdf"
    )

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS  — MUST MATCH pdf-layout-studio.html EXACTLY
# ══════════════════════════════════════════════════════════════════════════════

VERSION = "1.1.0"

# Paper sizes in PDF points (1 pt = 1/72 inch).
# Mirror of PAPER_SIZES in HTML tool.
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "a4":      (595.0,  842.0),
    "a3":      (842.0, 1191.0),
    "a5":      (420.0,  595.0),
    "letter":  (612.0,  792.0),
    "legal":   (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
}

# Standard N-up presets: N → (cols, rows).
# Mirror of NUP_PRESETS in HTML tool.
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

# Valid alignment values — mirrors HTML tog-btn data-val attributes
VALID_H_ALIGN = {"left", "center", "right"}
VALID_V_ALIGN = {"top",  "center", "bottom"}

# Config defaults (used when a key is absent)
DEFAULT_CONFIG: dict = {
    "paper":       "a4",
    "orientation": "portrait",
    "nup":         4,
    "layout":      {"cols": 2, "rows": 2},
    "margin":      18.0,
    "gap":         6.0,
    "hAlign":      "center",   # horizontal alignment within cell
    "vAlign":      "center",   # vertical   alignment within cell
    "grayscale":   False,
    "invert":      False,
    "border":      False,
    "quality":     2.0,
}

PT_TO_MM = 0.3528   # 1 pt → mm (informational, not used in math)


# ══════════════════════════════════════════════════════════════════════════════
# LAYOUT ENGINE  — mirrors computeLayout() + fitInCell() in HTML tool
# ══════════════════════════════════════════════════════════════════════════════

def compute_layout(config: dict) -> dict:
    """
    Compute N-up sheet geometry.  All values in PDF points.

    Formula (identical to HTML tool computeLayout()):
    ─────────────────────────────────────────────────
        cell_width  = (paper_width  - 2*margin - (cols-1)*gap) / cols
        cell_height = (paper_height - 2*margin - (rows-1)*gap) / rows

        cell_x[col] = margin + col * (cell_width  + gap)
        cell_y[row] = margin + row * (cell_height + gap)

    Parameters
    ----------
    config : validated config dict

    Returns
    -------
    dict with keys:
        paper_w, paper_h : float   — sheet dimensions in points
        cols, rows       : int
        cell_w, cell_h   : float   — cell dimensions in points
        cells            : list[dict]  — each dict has x, y, w, h
    """
    paper_key = str(config.get("paper", "a4")).lower()
    if paper_key not in PAPER_SIZES:
        raise ValueError(
            f"Unknown paper: '{paper_key}'.  "
            f"Valid: {sorted(PAPER_SIZES)}"
        )

    pw, ph = PAPER_SIZES[paper_key]

    orientation = str(config.get("orientation", "portrait")).lower()
    if orientation == "landscape":
        pw, ph = ph, pw   # swap width ↔ height

    layout_block = config.get("layout", {})
    cols   = int(layout_block.get("cols", 1))
    rows   = int(layout_block.get("rows", 1))
    margin = float(config.get("margin", DEFAULT_CONFIG["margin"]))
    gap    = float(config.get("gap",    DEFAULT_CONFIG["gap"]))

    if cols < 1 or rows < 1:
        raise ValueError(f"Invalid grid: {cols}×{rows} — both must be ≥ 1")

    # ── EXACT FORMULA (mirrors HTML computeLayout) ────────────────────────────
    cell_w = (pw - 2.0 * margin - (cols - 1) * gap) / cols
    cell_h = (ph - 2.0 * margin - (rows - 1) * gap) / rows

    if cell_w <= 0.0 or cell_h <= 0.0:
        raise ValueError(
            f"Cell dimensions non-positive: {cell_w:.2f} × {cell_h:.2f} pt.\n"
            f"  margin={margin} pt,  gap={gap} pt,  grid={cols}×{rows}\n"
            f"  → Reduce margin or gap."
        )

    cells = []
    for row in range(rows):
        for col in range(cols):
            x = margin + col * (cell_w + gap)
            y = margin + row * (cell_h + gap)
            cells.append({"x": x, "y": y, "w": cell_w, "h": cell_h})

    return {
        "paper_w": pw,   "paper_h": ph,
        "cols": cols,    "rows": rows,
        "cell_w": cell_w, "cell_h": cell_h,
        "cells": cells,
    }


def fit_in_cell(
    src_w:  float,
    src_h:  float,
    cell:   dict,
    h_align: str = "center",
    v_align: str = "center",
) -> fitz.Rect:
    """
    Aspect-ratio-preserving fit of a source page into a cell.

    Formula (mirrors updated HTML tool fitInCell()):
    ─────────────────────────────────────────────────
        scale    = min(cell_w / src_w, cell_h / src_h)
        scaled_w = src_w * scale
        scaled_h = src_h * scale

        h_align:  'left'   → offset_x = 0
                  'center' → offset_x = (cell_w - scaled_w) / 2
                  'right'  → offset_x =  cell_w - scaled_w

        v_align:  'top'    → offset_y = 0
                  'center' → offset_y = (cell_h - scaled_h) / 2
                  'bottom' → offset_y =  cell_h - scaled_h

    Parameters
    ----------
    src_w, src_h : source page dimensions in points
    cell         : dict with x, y, w, h  (all in points)
    h_align      : horizontal placement — 'left' | 'center' | 'right'
    v_align      : vertical   placement — 'top'  | 'center' | 'bottom'

    Returns
    -------
    fitz.Rect for show_pdf_page() target rectangle
    """
    scale    = min(cell["w"] / src_w, cell["h"] / src_h)
    scaled_w = src_w * scale
    scaled_h = src_h * scale

    # Horizontal offset
    if   h_align == "left":   offset_x = 0.0
    elif h_align == "right":  offset_x = cell["w"] - scaled_w
    else:                     offset_x = (cell["w"] - scaled_w) / 2.0   # center

    # Vertical offset
    if   v_align == "top":    offset_y = 0.0
    elif v_align == "bottom": offset_y = cell["h"] - scaled_h
    else:                     offset_y = (cell["h"] - scaled_h) / 2.0   # center

    x0 = cell["x"] + offset_x
    y0 = cell["y"] + offset_y
    return fitz.Rect(x0, y0, x0 + scaled_w, y0 + scaled_h)


# ══════════════════════════════════════════════════════════════════════════════
# COLOR TRANSFORM ENGINE  — mirrors applyColorTransform() in HTML tool EXACTLY
# ══════════════════════════════════════════════════════════════════════════════

def transform_pixmap(
    pix:       fitz.Pixmap,
    do_gray:   bool,
    do_invert: bool,
) -> fitz.Pixmap:
    """
    Apply grayscale and/or invert to a fitz.Pixmap using pixel-level math.

    Returns the original pixmap unchanged when both flags are False (fast path).

    Operation order (must match HTML applyColorTransform()):
    ────────────────────────────────────────────────────────
      1. Grayscale — luminance formula:
             gray = round(0.2126*R + 0.7152*G + 0.0722*B)
         Identical coefficients to HTML tool and ITU-R BT.709 standard.
      2. Invert — r = 255-r, g = 255-g, b = 255-b

    Parameters
    ----------
    pix       : source pixmap (any colorspace; converted to RGB internally)
    do_gray   : apply luminance grayscale
    do_invert : invert pixel values

    Returns
    -------
    New fitz.Pixmap with transformed pixels (RGB colorspace)
    """
    if not do_gray and not do_invert:
        return pix   # fast path — no copy

    # Normalise to RGB (3 bytes/pixel, no alpha)
    if pix.colorspace != fitz.csRGB or pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)   # drop alpha

    w, h = pix.width, pix.height

    # ── numpy path (fast) ─────────────────────────────────────────────────────
    try:
        import numpy as np

        # View pixel data as a writable (h × w × 3) uint8 array
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, w, 3).copy()

        # ── Step 1: Grayscale via luminance formula ────────────────────────────
        # Coefficients 0.2126 / 0.7152 / 0.0722 match HTML applyColorTransform()
        if do_gray:
            gray = (  0.2126 * arr[:, :, 0].astype(np.float32)
                    + 0.7152 * arr[:, :, 1].astype(np.float32)
                    + 0.0722 * arr[:, :, 2].astype(np.float32)
                   ).round().astype(np.uint8)
            arr[:, :, 0] = gray
            arr[:, :, 1] = gray
            arr[:, :, 2] = gray

        # ── Step 2: Invert ─────────────────────────────────────────────────────
        if do_invert:
            arr = 255 - arr

        return fitz.Pixmap(fitz.csRGB, w, h, arr.tobytes(), False)

    except ImportError:
        pass   # fall through to pure-Python path

    # ── Pure-Python fallback (no numpy) ───────────────────────────────────────
    samples = bytearray(pix.samples)   # mutable copy

    for i in range(0, len(samples), 3):
        r, g, b = samples[i], samples[i + 1], samples[i + 2]

        # Step 1 — luminance grayscale
        if do_gray:
            gray = int(0.2126 * r + 0.7152 * g + 0.0722 * b + 0.5)
            r = g = b = gray

        # Step 2 — invert
        if do_invert:
            r = 255 - r
            g = 255 - g
            b = 255 - b

        samples[i] = r; samples[i + 1] = g; samples[i + 2] = b

    return fitz.Pixmap(fitz.csRGB, w, h, bytes(samples), False)


def make_transformed_page_doc(
    src_doc:   fitz.Document,
    page_idx:  int,
    config:    dict,
    quality:   float,
) -> fitz.Document:
    """
    Rasterise one source page, apply color transform, and return a
    single-page fitz.Document containing the result as an embedded image.

    This temporary document is passed to show_pdf_page() so the transformed
    content is placed into the layout sheet at exactly the right position.
    The caller is responsible for closing the returned document.

    Parameters
    ----------
    src_doc   : source PDF document
    page_idx  : 0-based page index in src_doc
    config    : validated config dict
    quality   : rasterisation scale factor (quality × 72 DPI)
    """
    src_page = src_doc[page_idx]
    w        = src_page.rect.width
    h        = src_page.rect.height

    # Rasterise at quality×72 DPI — same scale as HTML tool's PDF.js render
    matrix = fitz.Matrix(quality, quality)
    pix    = src_page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)

    # Apply pixel transform (grayscale first, then invert — matches HTML order)
    do_gray   = bool(config.get("grayscale", False))
    do_invert = bool(config.get("invert",    False))
    pix       = transform_pixmap(pix, do_gray, do_invert)

    # Wrap in a single-page PDF at original point dimensions
    img_doc  = fitz.open()
    img_page = img_doc.new_page(width=w, height=h)
    img_page.insert_image(fitz.Rect(0, 0, w, h), pixmap=pix)
    return img_doc


# ══════════════════════════════════════════════════════════════════════════════
# CORE PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def process_pdf(
    src_path:  Path,
    out_path:  Path,
    config:    dict,
    *,
    verbose:   bool = True,
) -> None:
    """
    Process one PDF file: build N-up layout with optional color transforms
    applied per source page BEFORE placement, then save.

    Layout algorithm
    ────────────────
    1. Compute sheet geometry via compute_layout()
    2. Determine whether color transforms are needed
    3. For each output sheet:
       a. Create a blank page of the target paper size
       b. For each cell (left-to-right, top-to-bottom):
          i.   Load source page
          ii.  If grayscale or invert:
                 rasterise → transform_pixmap() → wrap in temp doc
               Else:
                 use src_doc directly (vector path, fast)
          iii. Compute fit rect via fit_in_cell()
          iv.  Stamp page via show_pdf_page()
          v.   Optionally draw cell border
    4. Save with garbage collection + deflate compression

    Color transforms are always applied BEFORE placement so the result
    matches the HTML preview exactly.  The HTML tool's applyColorTransform()
    and this function's transform_pixmap() use identical formulas.

    Parameters
    ----------
    src_path : path to input PDF
    out_path : where to save the output PDF
    config   : validated config dict
    verbose  : print progress lines
    """
    if verbose:
        print(f"  ► {src_path.name}")

    src_doc = fitz.open(str(src_path))
    out_doc = fitz.open()
    layout  = compute_layout(config)

    cells_per_sheet = layout["cols"] * layout["rows"]
    num_pages       = src_doc.page_count
    num_sheets      = math.ceil(num_pages / cells_per_sheet) if num_pages else 1

    do_border    = bool(config.get("border", False))
    border_color = (0.75, 0.75, 0.75)
    border_width = 0.5

    # Pre-compute transform flags once — avoids repeated dict lookups
    do_transform = (bool(config.get("grayscale", False)) or
                    bool(config.get("invert",    False)))
    quality      = float(config.get("quality", DEFAULT_CONFIG["quality"]))

    for sheet_idx in range(num_sheets):
        # ── Create output sheet ──────────────────────────────────────────────
        out_page = out_doc.new_page(
            width  = layout["paper_w"],
            height = layout["paper_h"],
        )

        for cell_idx, cell in enumerate(layout["cells"]):
            page_idx = sheet_idx * cells_per_sheet + cell_idx
            if page_idx >= num_pages:
                break

            src_page = src_doc[page_idx]
            src_rect = src_page.rect   # natural dimensions in points

            # ── fit_in_cell mirrors HTML fitInCell() exactly ─────────────────
            fit_rect = fit_in_cell(
                src_rect.width, src_rect.height, cell,
                h_align=config.get("hAlign", "center"),
                v_align=config.get("vAlign", "center"),
            )

            # ── Place page — transform path or fast vector path ──────────────
            if do_transform:
                # Rasterise source page → pixel transform → temporary PDF.
                # The transform (grayscale then invert) is applied BEFORE
                # placement, so it appears correctly in the layout.
                # This matches the HTML tool's applyColorTransform() call
                # which runs before ctx.drawImage().
                tmp_doc = make_transformed_page_doc(
                    src_doc, page_idx, config, quality
                )
                out_page.show_pdf_page(
                    fit_rect, tmp_doc, 0,
                    keep_proportion=False,
                    overlay=True,
                )
                tmp_doc.close()
            else:
                # Fast vector path — no rasterisation, full PDF quality
                out_page.show_pdf_page(
                    fit_rect, src_doc, page_idx,
                    keep_proportion=False,
                    overlay=True,
                )

            # ── Optional cell border ──────────────────────────────────────────
            if do_border:
                cell_rect = fitz.Rect(
                    cell["x"],             cell["y"],
                    cell["x"] + cell["w"], cell["y"] + cell["h"],
                )
                out_page.draw_rect(
                    cell_rect,
                    color = border_color,
                    width = border_width,
                )

    src_doc.close()

    # ── Save ─────────────────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(
        str(out_path),
        garbage=4,       # remove unused objects
        deflate=True,    # compress streams
        clean=True,      # sanitise content streams
    )
    out_doc.close()

    if verbose:
        sheets_str = f"{num_sheets} sheet{'s' if num_sheets != 1 else ''}"
        print(f"    ✓ {num_pages} pages → {sheets_str} → {out_path.name}")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: Path) -> dict:
    """Load and validate a JSON config, filling defaults for missing keys."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cfg = dict(DEFAULT_CONFIG)  # start from defaults

    # Merge top-level keys
    for key in ("paper", "orientation", "margin", "gap",
                "hAlign", "vAlign",
                "grayscale", "invert", "border", "quality"):
        if key in raw:
            cfg[key] = raw[key]

    # Layout block: prefer explicit layout over nup
    if "layout" in raw and "cols" in raw["layout"] and "rows" in raw["layout"]:
        cfg["layout"] = {
            "cols": int(raw["layout"]["cols"]),
            "rows": int(raw["layout"]["rows"]),
        }
    elif "nup" in raw:
        nup = int(raw["nup"])
        if nup not in NUP_PRESETS:
            raise ValueError(
                f"nup={nup} is not a standard preset. "
                f"Valid: {sorted(NUP_PRESETS)}.  "
                f"Use 'layout' {{cols, rows}} for custom grids."
            )
        cfg["layout"] = {"cols": NUP_PRESETS[nup][0], "rows": NUP_PRESETS[nup][1]}
        cfg["nup"]    = nup

    # Type coercions
    cfg["margin"]  = float(cfg["margin"])
    cfg["gap"]     = float(cfg["gap"])
    cfg["quality"] = float(cfg["quality"])

    # Alignment validation
    if cfg.get("hAlign", "center") not in VALID_H_ALIGN:
        raise ValueError(
            f"hAlign='{cfg['hAlign']}' is invalid.  "
            f"Valid: {sorted(VALID_H_ALIGN)}"
        )
    if cfg.get("vAlign", "center") not in VALID_V_ALIGN:
        raise ValueError(
            f"vAlign='{cfg['vAlign']}' is invalid.  "
            f"Valid: {sorted(VALID_V_ALIGN)}"
        )

    # Sanity checks
    if cfg["margin"] < 0:
        raise ValueError(f"margin must be ≥ 0, got {cfg['margin']}")
    if cfg["gap"] < 0:
        raise ValueError(f"gap must be ≥ 0, got {cfg['gap']}")
    if cfg["quality"] <= 0:
        raise ValueError(f"quality must be > 0, got {cfg['quality']}")
    if cfg["layout"]["cols"] < 1 or cfg["layout"]["rows"] < 1:
        raise ValueError(f"cols and rows must be ≥ 1")

    return cfg


def print_config_summary(cfg: dict) -> None:
    """Print a human-readable summary of the active config."""
    layout = compute_layout(cfg)
    pt2mm  = lambda v: v * PT_TO_MM

    print("  Config summary:")
    print(f"    paper       : {cfg['paper'].upper()} {cfg['orientation']}")
    print(f"    sheet size  : {layout['paper_w']:.1f} × {layout['paper_h']:.1f} pt"
          f"  ({pt2mm(layout['paper_w']):.1f} × {pt2mm(layout['paper_h']):.1f} mm)")
    print(f"    grid        : {cfg['layout']['cols']} × {cfg['layout']['rows']}"
          f"  ({cfg['layout']['cols'] * cfg['layout']['rows']} cells/sheet)")
    print(f"    cell size   : {layout['cell_w']:.2f} × {layout['cell_h']:.2f} pt"
          f"  ({pt2mm(layout['cell_w']):.1f} × {pt2mm(layout['cell_h']):.1f} mm)")
    print(f"    margin      : {cfg['margin']} pt  ({pt2mm(cfg['margin']):.1f} mm)")
    print(f"    gap         : {cfg['gap']} pt  ({pt2mm(cfg['gap']):.1f} mm)")
    print(f"    h-align     : {cfg.get('hAlign', 'center')}")
    print(f"    v-align     : {cfg.get('vAlign', 'center')}")
    flags = []
    if cfg['grayscale']: flags.append("grayscale")
    if cfg['invert']:    flags.append("invert")
    if cfg['border']:    flags.append("border")
    print(f"    flags       : {', '.join(flags) or 'none'}")
    if cfg.get('grayscale') or cfg.get('invert'):
        print(f"    raster DPI  : {int(72 * cfg['quality'])} dpi  (quality={cfg['quality']}×)")


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog        = "process.py",
        description = "PDF N-Up Layout Processor — pairs with PDF Layout Studio HTML tool",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = """
examples:
  # Single file, auto-named output (input_nup.pdf)
  python process.py config.json report.pdf

  # Single file, explicit output
  python process.py config.json report.pdf out/report-4up.pdf

  # Process entire folder
  python process.py config.json ./pdfs/

  # Folder → different output folder
  python process.py config.json ./pdfs/ ./out/

  # Print layout info without processing
  python process.py config.json --info
        """,
    )
    p.add_argument("config",  type=Path, help="path to JSON config file")
    p.add_argument("input",   type=Path, nargs="?",
                   help="input PDF file or folder of PDFs")
    p.add_argument("output",  type=Path, nargs="?",
                   help="output PDF file or folder (auto-generated if omitted)")
    p.add_argument("--info",  action="store_true",
                   help="print config summary and exit")
    p.add_argument("--suffix", default="_nup",
                   help="suffix appended to output filenames (default: _nup)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress per-file output")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p


def resolve_output_path(src: Path, out_dir: Path | None, suffix: str) -> Path:
    stem = src.stem + suffix + ".pdf"
    base = out_dir if out_dir else src.parent
    return base / stem


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    # ── Load config ──────────────────────────────────────────────────────────
    if not args.config.exists():
        print(f"Error: config not found: {args.config}", file=sys.stderr)
        return 1

    try:
        cfg = load_config(args.config)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Error in config: {e}", file=sys.stderr)
        return 1

    # ── --info mode ───────────────────────────────────────────────────────────
    if args.info:
        print(f"PDF Layout Studio — process.py {VERSION}")
        print(f"Config: {args.config}")
        print_config_summary(cfg)
        return 0

    if not args.input:
        parser.print_help()
        return 0

    if not args.input.exists():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 1

    verbose = not args.quiet

    # ── Resolve input / output lists ─────────────────────────────────────────
    if args.input.is_file():
        # Single file mode
        pdf_files = [args.input]

        if args.output:
            out_paths = [args.output]
        else:
            out_paths = [resolve_output_path(args.input, None, args.suffix)]

    else:
        # Folder mode
        pdf_files = sorted(args.input.glob("**/*.pdf"))
        if not pdf_files:
            print(f"No PDF files found in: {args.input}", file=sys.stderr)
            return 1

        out_dir = args.output if args.output else (args.input / "output")

        out_paths = [
            resolve_output_path(f, out_dir, args.suffix)
            for f in pdf_files
        ]

    # ── Process ──────────────────────────────────────────────────────────────
    if verbose:
        print(f"\nPDF Layout Studio — process.py {VERSION}")
        print(f"Config: {args.config}")
        print_config_summary(cfg)
        print(f"\nProcessing {len(pdf_files)} file(s)…\n")

    ok_count = 0
    err_count = 0

    for src, out in zip(pdf_files, out_paths):
        try:
            process_pdf(src, out, cfg, verbose=verbose)
            ok_count += 1
        except Exception as e:
            print(f"  ✗ {src.name}: {e}", file=sys.stderr)
            err_count += 1

    if verbose:
        print()
        if err_count == 0:
            print(f"Done — {ok_count} file(s) processed successfully.")
        else:
            print(f"Done — {ok_count} OK, {err_count} error(s).")

    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
