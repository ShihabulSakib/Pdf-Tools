# PDF Layout Studio

A two-layer system for pixel-perfect PDF N-up layout generation.

| Layer | File | Role |
|-------|------|------|
| 1 — Browser | `pdf-layout-studio.html` | Visual preview + config editor |
| 2 — CLI | `process.py` | Batch PDF processor (PyMuPDF) |
| Config | `config.json` | Shared layout definition |

> **Design principle:** "Preview defines truth. Backend reproduces it exactly."
> Both layers implement the same layout formulas, so the CLI output matches the
> HTML preview pixel-for-pixel.

---

## Quick Start

### 1. HTML Preview Tool

Open `pdf-layout-studio.html` in any modern browser (Chrome / Firefox / Edge).

- Drag-and-drop a PDF onto the canvas **or** click **Open PDF**
- Adjust layout settings in the left sidebar
- Watch the preview update live
- Click **Export JSON** → save as `config.json`

### 2. Python CLI

```bash
pip install pymupdf

# Single file — output auto-named input_nup.pdf
python process.py config.json report.pdf

# Single file — explicit output
python process.py config.json report.pdf output/report-4up.pdf

# Batch folder — outputs go into ./pdfs/output/
python process.py config.json ./pdfs/

# Batch folder — custom output dir
python process.py config.json ./pdfs/ ./output/

# Print layout info without processing
python process.py config.json --info
```

---

## Config Reference

```jsonc
{
  "paper":       "a4",        // a4 | a3 | a5 | letter | legal | tabloid
  "orientation": "landscape", // portrait | landscape
  "nup":         4,           // 1 | 2 | 4 | 6 | 8 | 9 | 12 | 16
  "layout": {
    "cols": 2,                // explicit grid — takes priority over nup
    "rows": 2
  },
  "margin":      18,          // outer margin in PDF points (1pt = 1/72 inch)
  "gap":         6,           // gap between cells in points
  "grayscale":   false,       // convert output to grayscale
  "invert":      false,       // invert all colours
  "border":      true,        // draw cell border lines
  "quality":     2            // PDF.js render scale / raster DPI multiplier
}
```

### Common Paper Sizes

| Name | Portrait (pt) | Landscape (pt) |
|------|--------------|----------------|
| a4 | 595 × 842 | 842 × 595 |
| a3 | 842 × 1191 | 1191 × 842 |
| a5 | 420 × 595 | 595 × 420 |
| letter | 612 × 792 | 792 × 612 |
| legal | 612 × 1008 | 1008 × 612 |
| tabloid | 792 × 1224 | 1224 × 792 |

### Standard N-Up Grids

| nup | cols | rows | cells/sheet |
|-----|------|------|-------------|
| 1 | 1 | 1 | 1 |
| 2 | 2 | 1 | 2 |
| 4 | 2 | 2 | 4 |
| 6 | 3 | 2 | 6 |
| 8 | 4 | 2 | 8 |
| 9 | 3 | 3 | 9 |
| 12 | 4 | 3 | 12 |
| 16 | 4 | 4 | 16 |

Custom grids (e.g. 5×3) are supported via the `layout.cols`/`layout.rows` keys.

---

## Layout Math

Both layers implement these formulas identically:

```
cell_width  = (paper_width  - 2×margin - (cols-1)×gap) / cols
cell_height = (paper_height - 2×margin - (rows-1)×gap) / rows

cell_x[col] = margin + col × (cell_width  + gap)
cell_y[row] = margin + row × (cell_height + gap)
```

Each source page is scaled to fit its cell while preserving aspect ratio:

```
scale    = min(cell_width  / src_width,
               cell_height / src_height)
scaled_w = src_width  × scale
scaled_h = src_height × scale
offset_x = (cell_width  - scaled_w) / 2   ← centres horizontally
offset_y = (cell_height - scaled_h) / 2   ← centres vertically
```

This is **exactly** the same code in both `computeLayout()` / `fitInCell()`
(HTML) and `compute_layout()` / `fit_in_cell()` (Python).

---

## Grayscale / Invert

When `grayscale` or `invert` is enabled:

- HTML: applies CSS `filter: grayscale(100%)` / `invert(100%)` on the canvas
- Python: rasterises the N-up output at `quality × 72` DPI using
  PyMuPDF's `csGRAY` colorspace, then rebuilds the PDF as an image document

The rasterisation DPI matches the HTML render scale so the visual output is
consistent.

| `quality` | Raster DPI | Use case |
|-----------|-----------|---------|
| 1 | 72 dpi | Draft / thumbnail |
| 2 | 144 dpi | Screen / web (default) |
| 3 | 216 dpi | Print-ready |
| 4 | 288 dpi | High-quality print |

---

## Presets (HTML Tool)

Presets are saved in `localStorage` and persist between browser sessions.

1. Configure the layout you want
2. Type a name in the **Presets** section
3. Click **Save**
4. Click any preset to restore its settings instantly

---

## Verification Method

To confirm the CLI output matches the preview:

1. Open `pdf-layout-studio.html` and load your PDF
2. Configure the layout and note the **cell size** in the status bar
3. Export `config.json`
4. Run: `python process.py config.json your.pdf`
5. Open the output PDF and compare cell positions visually

Because both tools use identical formulas, positions and sizes will match
within floating-point rounding (< 0.01 pt).

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| HTML tool | Modern browser with JavaScript; no server needed |
| Python CLI | Python ≥ 3.10, PyMuPDF (`pip install pymupdf`) |

---

## File Overview

```
pdf-layout-studio/
├── pdf-layout-studio.html   ← Browser tool (open directly)
├── process.py               ← Python CLI processor
├── config.json              ← Example configuration
└── README.md                ← This file
```
