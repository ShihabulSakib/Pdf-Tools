# PDF Layout Studio

A comprehensive two-layer system for pixel-perfect PDF N-up layout generation, with an end-to-end workflow to scrape YouTube playlists, download PDFs from Google Drive, merge/convert them, and apply N-up page imposition.

| Layer | File | Role |
|---|---|---|
| 1 — Browser | `pdf-layout-studio.html` / `index.html` | Visual preview + config editor with live preview |
| 2 — CLI | `process.py` | Batch PDF processor (PyMuPDF) for N-up layout generation |
| 3 — Scraping | `scrape_playlist.py` | Scrapes YouTube playlists for Google Drive PDF links |
| 4 — Download | `download_pdfs.py` | Downloads Drive files in playlist order with sanitization |
| 5 — Batch Ops | `merge_and_convert.py` / `merge_and_convert.sh` | Merges folders of PDFs & converts to A4 Landscape |
| 6 — Conversion | `convert_pdf_a4l.py` / `convert_pdf_a4l.sh` | Batch converts PDFs to A4 Landscape |
| Config | `config.json`, `layout-config.json` | Shared layout definitions |
| Server | `server.js` | Optional Express server to serve HTML tools |

> **Design principle:** "Preview defines truth. Backend reproduces it exactly."
> Both browser and CLI layers implement identical layout formulas for pixel-for-pixel accuracy.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Complete End-to-End Workflow](#complete-end-to-end-workflow)
- [Configuration Reference](#configuration-reference)
- [Layout Math](#layout-math)
- [Grayscale / Invert](#grayscale--invert)
- [Verification Method](#verification-method)
- [Troubleshooting Guide](#troubleshooting-guide)
- [Requirements](#requirements)
- [File Overview](#file-overview)
- [Notes & Best Practices](#notes--best-practices)

---

## Quick Start

### 1. Environment Setup

**Linux / macOS:**
```bash
chmod +x setup.sh
./setup.sh
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
Set-ExecutionPolicy -Scope CurrentUser Bypass -Force
.\setup.ps1
.\.venv\Scripts\Activate.ps1
```

### 2. HTML Preview Tool (Instant)

Open `pdf-layout-studio.html` directly in any modern browser (Chrome/Firefox/Edge). No server required.

- Drag & drop a PDF onto the canvas, or click **Open PDF**
- Tweak layout settings in the sidebar
- Live preview updates
- Click **Export JSON** → save as `config.json`

Alternatively, run `npm run dev` and open http://localhost:3000 (uses `server.js`).

### 3. Python CLI (Single File)

```bash
python process.py config.json input.pdf
python process.py config.json input.pdf output.pdf
python process.py config.json input_folder/ output_folder/
python process.py config.json --info  # Show layout math
```

---

## Prerequisites

| Tool | Purpose | Install (Ubuntu/Debian) | Install (macOS) | Install (Windows) |
|---|---|---|---|---|
| Python ≥ 3.10 | Core runtime | `sudo apt install python3 python3-venv` | `brew install python` | [python.org](https://www.python.org/) |
| Ghostscript (`gs`) | PDF conversion/merging operations | `sudo apt install ghostscript` | `brew install ghostscript` | [Ghostscript](https://www.ghostscript.com/) |
| qpdf (optional) | Lossless PDF merging (recommended) | `sudo apt install qpdf` | `brew install qpdf` | [qpdf](https://qpdf.sourceforge.io/) |
| yt-dlp/deps | Scraping (auto-installed via requirements) | — | — | — |

---

## Complete End-to-End Workflow

This toolkit supports a full pipeline from YouTube playlists to processed N-up PDFs.

### Step 1: Scrape YouTube Playlist for Drive Links

Extracts Google Drive PDF links from video descriptions (and comments as fallback). Preserves strict playlist order.

```bash
python scrape_playlist.py "<PLAYLIST_URL>" -o links.json
python scrape_playlist.py "<PLAYLIST_URL>" -o links.json --resume  # Skip already processed
```

**Features:**
- Processes videos strictly in playlist order (index guaranteed)
- Unicode/Bangla-aware (NFC normalization, proper grapheme handling)
- Stores every video entry even if no links found (prevents index shifting)
- Outputs JSON with metadata, timestamps, per-video Drive links

**Output:** `links.json` containing playlist info + array of videos with `drive_links[]`.

---

### Step 2: Download PDFs from Drive Links

Downloads all Drive files in playlist order, naming them sequentially for easy sorting/printing.

```bash
python download_pdfs.py links.json -d chem_pdfs
python download_pdfs.py links.json -d chem_pdfs --from 10 --to 25  # Range selection
```

**Naming convention:** `Lecture 001 - <video title>.pdf` (zero-padded by total count). If multiple files per video: `Lecture 001 - Title (2).pdf`.

**Features:**
- Serial processing (one-by-one, order guaranteed)
- Skips already downloaded valid PDFs (idempotent)
- Filesystem-safe Unicode filename sanitization
- Grapheme-cluster aware truncation (prevents cutting Bangla conjuncts)
- Validates PDFs (magic bytes check), detects HTML/quota pages
- Fallback direct download if gdown fails
- Comprehensive error logging per file

**Output:** PDFs in `chem_pdfs/` in exact lecture order.

---

### Step 3: Merge Folders & Convert to A4 Landscape

For folder-organized PDFs (common after download), merge multi-PDF folders losslessly and convert everything to A4 Landscape (842×595 pt).

**Bash (recommended, fast):**
```bash
chmod +x merge_and_convert.sh
./merge_and_convert.sh ./chem_pdfs
```

**Python (cross-platform, detailed progress):**
```bash
python merge_and_convert.py ./chem_pdfs
python merge_and_convert.py ./chem_pdfs ./custom_output
```

**Behavior:**
- Groups PDFs by folder
- If a folder has ≥ 2 PDFs: merges losslessly (uses qpdf if available, Ghostscript fallback) → converts to A4L
- If a folder has 1 PDF: converts directly to A4L
- Outputs to `<input>/output/` by default
- Dynamic progress bar with ETA, success/fail counts, elapsed time

---

### Step 4: Convert Flat PDFs to A4 Landscape (Batch)

Convert all PDFs under a directory tree to A4 Landscape without merging.

**Bash:**
```bash
chmod +x convert_pdf_a4l.sh
./convert_pdf_a4l.sh ./pdfs
```

**Python:**
```bash
python convert_pdf_a4l.py ./pdfs
python convert_pdf_a4l.py ./pdfs ./a4l_output --no-color
```

**Target:** A4 Landscape = 842 × 595 points (297 × 210 mm). Preserves page content, scales to fit page.

---

### Step 5: Apply N-up Layout (Pixel-Perfect Imposition)

Generate N-up layouts using the CLI processor. Uses identical math as HTML preview.

```bash
# Single file
python process.py config.json report.pdf report_nup.pdf

# Batch folder (processes all PDFs)
python process.py config.json ./chem_pdfs/
python process.py config.json ./chem_pdfs/ ./nup_output/

# Show computed layout info
python process.py config.json --info
```

**Features:**
- Dynamic terminal progress with per-file tracking, ETA, throughput
- Cross-platform (Windows CMD/PS + Linux/macOS)
- Identical formulas to HTML preview (guaranteed match)
- Optional grayscale/invert with optimized numpy fast-path
- Rasterization at quality×72 DPI when filters applied

---

### Step 6: Visual Preview & Tweak

- Open `pdf-layout-studio.html` in browser
- Load a sample PDF + your `config.json`
- Adjust layout in real-time until satisfied
- Export updated config → re-run Step 5

Optional: serve via Express: `npm run dev` → http://localhost:3000

---

## Configuration Reference

### config.json (Example)

```json
{
  "paper": "a4",
  "orientation": "landscape",
  "nup": 4,
  "layout": {
    "cols": 2,
    "rows": 2
  },
  "margin": 18,
  "gap": 6,
  "grayscale": false,
  "invert": false,
  "border": true,
  "quality": 2,
  "version": "1.0"
}
```

### layout-config.json (Alternate)

```json
{
  "paper": "a4",
  "orientation": "landscape",
  "nup": 6,
  "layout": {"cols": 3, "rows": 2},
  "margin": 0,
  "gap": 0,
  "hAlign": "center",
  "vAlign": "center",
  "grayscale": true,
  "invert": true,
  "border": false,
  "quality": 2,
  "version": "1.0"
}
```

| Field | Values | Description |
|---|---|---|
| `paper` | `a4,a3,a5,letter,legal,tabloid` | Paper size |
| `orientation` | `portrait,landscape` | Paper orientation |
| `nup` | `1,2,4,6,8,9,12,16` | Preset N-up (auto sets cols/rows) |
| `layout.cols/rows` | int | Explicit grid (overrides nup if both set) |
| `margin` | pt | Outer margin (points, 1pt=1/72") |
| `gap` | pt | Gap between cells |
| `hAlign,vAlign` | `left,center,right,top,middle,bottom` | Cell alignment (HTML tool) |
| `grayscale` | bool | Convert to grayscale |
| `invert` | bool | Invert colors |
| `border` | bool | Draw cell border lines |
| `quality` | 1-4 | Render/raster DPI multiplier (see below) |

### Paper Sizes (Points)

| Name | Portrait (pt) | Landscape (pt) |
|---|---|---|
| a4 | 595×842 | 842×595 |
| a3 | 842×1191 | 1191×842 |
| a5 | 420×595 | 595×420 |
| letter | 612×792 | 792×612 |
| legal | 612×1008 | 1008×612 |
| tabloid | 792×1224 | 1224×792 |

### N-Up Presets

| nup | cols | rows | cells/sheet |
|---|---|---|---|
| 1 | 1 | 1 | 1 |
| 2 | 2 | 1 | 2 |
| 4 | 2 | 2 | 4 |
| 6 | 3 | 2 | 6 |
| 8 | 4 | 2 | 8 |
| 9 | 3 | 3 | 9 |
| 12 | 4 | 3 | 12 |
| 16 | 4 | 4 | 16 |

Custom grids (e.g. 5×3) supported via `layout.cols`/`layout.rows`.

---

## Layout Math

Both layers implement these formulas identically:

```text
cell_width  = (paper_width  - 2×margin - (cols-1)×gap) / cols
cell_height = (paper_height - 2×margin - (rows-1)×gap) / rows

cell_x[col] = margin + col × (cell_width  + gap)
cell_y[row] = margin + row × (cell_height + gap)
```

Each source page is scaled to fit its cell while preserving aspect ratio:

```text
scale    = min(cell_width  / src_width,
               cell_height / src_height)
scaled_w = src_width  × scale
scaled_h = src_height × scale
offset_x = (cell_width  - scaled_w) / 2   ← centres horizontally
offset_y = (cell_height - scaled_h) / 2   ← centres vertically
```

This is **exactly** the same in `computeLayout()`/`fitInCell()` (HTML) and `compute_layout()`/`fit_in_cell()` (Python).

---

## Grayscale / Invert

When `grayscale` or `invert` is enabled:

- **HTML:** Applies CSS `filter: grayscale(100%)` / `invert(100%)` on canvas
- **Python:** Rasterises N-up output at `quality × 72` DPI using PyMuPDF's `csGRAY` colorspace, rebuilds as image PDF

Raster DPI matches HTML render scale for visual consistency:

| `quality` | Raster DPI | Use case |
|---|---|---|
| 1 | 72 dpi | Draft / thumbnail |
| 2 | 144 dpi | Screen / web (default) |
| 3 | 216 dpi | Print-ready |
| 4 | 288 dpi | High-quality print |

> **Note:** With numpy installed, grayscale/invert uses optimized fast-path; without it falls back to per-byte Python loop (significantly slower for large page counts).

---

## Verification Method

To confirm CLI output matches preview pixel-for-pixel:

1. Open `pdf-layout-studio.html` and load your PDF
2. Configure layout and note cell sizes in status bar
3. Export `config.json`
4. Run: `python process.py config.json your.pdf`
5. Visually compare output PDF with HTML preview
6. Positions/sizes match within < 0.01 pt due to floating-point rounding

---

## Troubleshooting Guide

### Environment & Dependencies

**Q1. Ghostscript (`gs`) not found**  
- **Ubuntu/Debian:** `sudo apt install ghostscript`  
- **macOS:** `brew install ghostscript`  
- **Windows:** Install Ghostscript, ensure `gswin64c`/`gswin32c` in PATH  
- **Fix:** Conversion/merge scripts require `gs`. Check: `gs --version`

**Q2. qpdf not found (merge warnings)**  
- **Symptom:** `merge_and_convert.sh` warns qpdf missing; falls back to Ghostscript  
- **Impact:** Ghostscript fallback is safe but qpdf gives cleaner lossless merges  
- **Fix:** Install qpdf per platform. Check: `qpdf --version`

**Q3. Python module import failures**  
- Recreate venv: `rm -rf .venv && ./setup.sh` (Linux/macOS) or `rmdir /s /q .venv && .\setup.ps1` (Windows)  
- Activate venv and `pip install -r requirements.txt`  
- Check: `.venv/bin/python -c "import fitz, numpy; print('ok')"`

**Q4. Missing rich/tqdm style warnings during setup**  
- Harmless; setup_progress.py falls back to plain text renderer  
- Install: `pip install rich>=13.0`

### Scraping (scrape_playlist.py)

**Q5. YouTube playlist scraping is slow or blocks**  
- yt-dlp may need updates: `pip install -U yt-dlp`  
- Some playlists require authentication? Usually public playlists work  
- Rate limiting: script processes sequentially (safe). If blocked, wait and retry with `--resume`  
- Use `--resume` to continue from last successful video

**Q6. Drive links not found in description**  
- Script also checks pinned/uploader comments as fallback  
- Some videos may not have public Drive links; JSON will record `status`/`error` and continue (order preserved)

### Downloading (download_pdfs.py)

**Q7. "Drive download quota exceeded" / "too many users" HTML page**  
- **Cause:** Google Drive temporary quota when many downloads hit same file  
- **Symptom:** Downloaded file is HTML (sniff detects `html`), script explains cause  
- **Fix:** Wait ~24 hours and retry. Files already downloaded remain skipped.  
- **Workaround:** Download manually via browser, rename to match naming convention

**Q8. "Request access" / "needs permission" / sign-in page**  
- **Cause:** Drive file not shared as "Anyone with the link"  
- **Fix:** Re-share file with "Anyone with the link" (Viewer) in Google Drive, update links.json or re-scrape

**Q9. gdown fails to parse large files / virus scan page**  
- Script has fallback: uses `drive.usercontent.google.com/download` direct endpoint  
- If both fail, HTML explanation is logged with reason

**Q10. Unicode/Bangla filenames get truncated or mangled**  
- Uses `pathvalidate.sanitize_filename` + NFC normalization + grapheme-cluster truncation (`regex \\X`)  
- `MAX_NAME_BYTES=240` leaves headroom under 255-byte ext4 limit  
- Filenames preserve Bangla characters correctly

**Q11. Corrupt/zero-byte downloads**  
- Validation: checks file exists, size>0, magic bytes start with `%PDF`  
- Invalid files are not treated as "done" (won't be skipped on retry)  
- Retry by deleting bad file and re-running

### Conversion & Merging

**Q12. PDF merge fails with Ghostscript fallback**  
- qpdf preferred for lossless merging; Ghostscript recompresses  
- If both fail, check PDF isn't password-protected/corrupt  
- Script attempts repair via `qpdf --linearize`/sanitization where applicable

**Q13. "output" directory has temp files (_tmp_*.pdf)**  
- Normal during processing; cleanup trap removes them on exit (success or failure)  
- If interrupted hard (SIGKILL), temp files may remain - safe to delete

**Q14. Large PDFs cause slow grayscale/invert**  
- Install numpy: `pip install numpy>=1.24` for fast-path  
- Reduce `quality` (1-2) for drafts, increase (3-4) only for final print  
- Memory: rasterization at high DPI uses more RAM; consider splitting very large docs

**Q15. Windows: ANSI colors don't show in CMD/PowerShell**  
- Scripts auto-enable VT100 on Windows 10/11 via ctypes  
- If colors garbled, use `--no-color` flag (Python tools accept it)  
- Windows Terminal recommended for best rendering

### General

**Q16. Process.py: Preview doesn't match output?**  
- Verify identical `config.json` used  
- Check paper/orientation match exactly  
- Floating point < 0.01pt is expected (rounding)  
- Ensure no transforms applied in other tools

**Q17. Batch processing skips some files?**  
- Check file extensions (.pdf case-sensitive on some filesystems; tools look for `.pdf`)  
- Output directory is pruned (`-path "$OUTPUT_ROOT" -prune`) to avoid reprocessing

**Q18. Order wrong after download?**  
- Filenames start with `Lecture 001`, `Lecture 002`... Sort by name = playlist order  
- `index` field in links.json preserves original playlist position

---

## Requirements

| Component | Requirement |
|---|---| 
| HTML Tools | Modern browser (Chrome/Firefox/Edge), JavaScript enabled |
| Python CLI | Python ≥ 3.10 |
| Core Python | `pymupdf>=1.23`, `numpy>=1.24` |
| Scraping/Download | `yt-dlp>=2024.0.0`, `regex>=2023.0.0`, `gdown>=4.7.0`, `pathvalidate>=3.0.0` |
| Setup | `rich>=13.0` (optional but recommended) |
| System | `ghostscript` (required), `qpdf` (recommended) |

---

## File Overview

```text
Pdf-Tools/
├── pdf-layout-studio.html   # Browser tool (standalone, no server)
├── index.html               # Alternate browser UI (served by server.js)
├── process.py               # Python CLI: N-up layout processor
├── merge_and_convert.py     # Python: folder merge + A4L conversion (rich UI)
├── merge_and_convert.sh     # Bash: folder merge + A4L conversion (styled)
├── convert_pdf_a4l.py       # Python: batch A4L conversion
├── convert_pdf_a4l.sh       # Bash: batch A4L conversion
├── scrape_playlist.py       # Scrape YouTube playlist → links.json
├── download_pdfs.py         # Download Drive links → PDFs (ordered)
├── setup.sh                 # Linux/macOS one-shot setup
├── setup.ps1                # Windows PowerShell setup
├── setup_progress.py        # Setup progress renderer (rich/plain)
├── server.js                # Express server (serves HTML tools)
├── config.json              # Default layout config (4-up A4 landscape)
├── layout-config.json       # Alternate layout (6-up, grayscale+invert)
├── metadata.json            # App metadata
├── package.json             # Node deps (express) + scripts
├── requirements.txt         # Python dependencies (merged)
├── .env.example             # Server env vars
├── .gitignore               # Ignore patterns
├── README.md                # This documentation
└── chem_pdfs/               # Example: downloaded lecture PDFs (28 files)
    └── output/              # Example: merged+converted N-up outputs (28 files)
```

---

## Notes & Best Practices

- **Order Guarantee:** All workflow tools preserve playlist/video order. Index never shifts even on failures.
- **Idempotent:** Download/conversion tools skip already valid existing outputs.
- **Unicode-Safe:** Full Bangla/Unicode support throughout (filenames, parsing, JSON).
- **Cross-Platform:** All Python/Bash tools tested logic works on Linux/macOS/Windows (Bash via WSL/Git Bash or use Python equivalents).
- **"Preview defines truth":** HTML and CLI share identical math—use preview to validate before batch processing.
- **Security:** Never commit secrets/keys. Scripts are read-only where possible.
- **Data Files:** `chem_pdfs/` and `links.json` are included as working examples of the complete pipeline.

---

*Built with pixel-perfect precision. For issues or improvements, see commit history.*
