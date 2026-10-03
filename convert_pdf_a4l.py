#!/usr/bin/env python3
"""
convert_pdf_a4l.py — PDF to A4 Landscape Batch Converter
=========================================================
Converts PDF files to A4 Landscape format (842 × 595 pt, 297 × 210 mm)
with dynamic, high-fidelity terminal progress bar display.

Compatible with:
  - Linux / macOS Bash & Zsh
  - Windows PowerShell & Command Prompt
"""

import sys
import os
import time
import shutil
import argparse
import subprocess
from pathlib import Path

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

# ══════════════════════════════════════════════════════════════════════════════
# TERMINAL STYLING & PROGRESS BAR
# ══════════════════════════════════════════════════════════════════════════════

class TermStyle:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    CYAN    = "\033[36m"
    BR_CYAN = "\033[96m"
    GREEN   = "\033[32m"
    BR_GRN  = "\033[92m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"
    BR_RED  = "\033[91m"
    MAGENTA = "\033[35m"
    GRAY    = "\033[90m"
    BG_DARK = "\033[48;5;236m"
    CLEAR_LN= "\033[K"

    @classmethod
    def strip_if_no_color(cls, enabled=True):
        if not enabled:
            for k in dir(cls):
                if k.isupper() and isinstance(getattr(cls, k), str):
                    setattr(cls, k, "")


class DynamicProgressBar:
    """
    High-fidelity dynamic progress bar designed for Linux, macOS, and Windows.
    Displays percentage, animated blocks, counter, rate (files/sec), elapsed/ETA,
    and the current active item with truncation to prevent line wrapping.
    """
    def __init__(self, total: int, title: str = "Converting", width: int = 26, use_color: bool = True):
        self.total = max(1, total)
        self.current = 0
        self.title = title
        self.bar_width = width
        self.start_time = time.time()
        self.last_update_time = 0
        self.use_color = use_color and sys.stdout.isatty()
        self.is_tty = sys.stdout.isatty()

    def format_time(self, seconds: float) -> str:
        s = int(seconds)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def update(self, current: int, item_name: str = "", status_tag: str = "CONVERTING"):
        self.current = current
        now = time.time()
        # Rate limit updates slightly to avoid terminal flicker, but always render first/last
        if self.is_tty and now - self.last_update_time < 0.04 and current < self.total:
            return
        self.last_update_time = now

        fraction = min(1.0, self.current / self.total)
        percent = fraction * 100.0

        # Timing calculations
        elapsed = now - self.start_time
        rate = self.current / elapsed if elapsed > 0.05 else 0.0
        remaining_secs = (self.total - self.current) / rate if rate > 0 else 0.0

        elapsed_str = self.format_time(elapsed)
        eta_str = self.format_time(remaining_secs)

        # Build progress bar track
        filled_len = int(self.bar_width * fraction)
        empty_len = self.bar_width - filled_len
        fill_chars = "█" * filled_len
        empty_chars = "░" * empty_len

        # Terminal width adaptation
        term_width = shutil.get_terminal_size((80, 20)).columns

        if self.use_color:
            S = TermStyle
            bar_display = f"{S.BR_CYAN}{fill_chars}{S.GRAY}{empty_chars}{S.RESET}"
            tag_display = f"{S.YELLOW}[{status_tag}]{S.RESET}"
            pct_display = f"{S.BOLD}{S.BR_GRN}{percent:5.1f}%{S.RESET}"
            count_display = f"{S.BOLD}{self.current}/{self.total}{S.RESET}"
            time_display = f"{S.DIM}[{elapsed_str} < {eta_str}, {rate:4.1f} f/s]{S.RESET}"
        else:
            bar_display = f"[{fill_chars}{empty_chars}]"
            tag_display = f"[{status_tag}]"
            pct_display = f"{percent:5.1f}%"
            count_display = f"{self.current}/{self.total}"
            time_display = f"[{elapsed_str} < {eta_str}, {rate:4.1f} f/s]"

        # Truncate filename to prevent terminal line spillover
        fixed_part_len = 1 + len(self.title) + 2 + self.bar_width + 8 + len(str(self.current)) + len(str(self.total)) + 3 + len(time_display) + len(status_tag) + 6
        avail_for_name = max(10, term_width - fixed_part_len - 15)
        
        display_name = item_name
        if len(display_name) > avail_for_name:
            display_name = "…" + display_name[-(avail_for_name - 1):]

        if self.is_tty:
            line = f"\r{TermStyle.CLEAR_LN} {TermStyle.BOLD}{self.title}{TermStyle.RESET} {bar_display} {pct_display} ({count_display}) {time_display} {tag_display} {display_name}"
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            # Non-interactive mode (e.g., CI / piped output)
            if self.current == 1 or self.current % max(1, self.total // 10) == 0 or self.current == self.total:
                sys.stdout.write(f"[{self.title}] {percent:5.1f}% ({self.current}/{self.total}) - {item_name}\n")
                sys.stdout.flush()

    def complete(self, message: str = "Complete"):
        if self.is_tty:
            now = time.time()
            elapsed_str = self.format_time(now - self.start_time)
            full_bar = "█" * self.bar_width
            S = TermStyle
            sys.stdout.write(
                f"\r{S.CLEAR_LN} {S.BOLD}{self.title}{S.RESET} "
                f"{S.BR_GRN}{full_bar}{S.RESET} {S.BOLD}{S.BR_GRN}100.0%{S.RESET} "
                f"({self.total}/{self.total}) {S.GREEN}✔ {message}{S.RESET} in {elapsed_str}\n"
            )
            sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════════════════
# PDF CONVERSION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def format_size(bytes_val: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:3.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"


def convert_single_pdf(src_path: Path, dst_path: Path, width_pt: int = 842, height_pt: int = 595) -> bool:
    """
    Converts src_path PDF to A4 Landscape (842 x 595 pt) at dst_path.
    Tries PyMuPDF (fitz) if installed, otherwise uses Ghostscript (gs).
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    # Strategy 1: PyMuPDF if available
    try:
        import fitz
        src_doc = fitz.open(str(src_path))
        out_doc = fitz.open()

        for page in src_doc:
            out_page = out_doc.new_page(width=width_pt, height=height_pt)
            src_rect = page.rect
            scale = min(width_pt / src_rect.width, height_pt / src_rect.height)
            scaled_w = src_rect.width * scale
            scaled_h = src_rect.height * scale
            ox = (width_pt - scaled_w) / 2.0
            oy = (height_pt - scaled_h) / 2.0
            fit_rect = fitz.Rect(ox, oy, ox + scaled_w, oy + scaled_h)
            out_page.show_pdf_page(fit_rect, src_doc, page.number, keep_proportion=False, overlay=True)

        out_doc.save(str(dst_path), garbage=4, deflate=True, clean=True)
        src_doc.close()
        out_doc.close()
        return True
    except ImportError:
        pass
    except Exception as e:
        # Fall back to Ghostscript if PyMuPDF encountered errors
        pass

    # Strategy 2: Ghostscript (cross-platform standard)
    gs_cmd = "gswin64c" if shutil.which("gswin64c") else ("gswin32c" if shutil.which("gswin32c") else "gs")
    if not shutil.which(gs_cmd):
        raise RuntimeError("Neither PyMuPDF ('pip install pymupdf') nor Ghostscript ('gs') was found in PATH.")

    cmd = [
        gs_cmd,
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dPDFFitPage",
        "-dFIXEDMEDIA",
        f"-dDEVICEWIDTHPOINTS={width_pt}",
        f"-dDEVICEHEIGHTPOINTS={height_pt}",
        "-dAutoRotatePages=/None",
        f"-sOutputFile={dst_path}",
        str(src_path),
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0


# ══════════════════════════════════════════════════════════════════════════════
# CLI BANNER & MAIN
# ══════════════════════════════════════════════════════════════════════════════

def print_banner():
    S = TermStyle
    print(f"{S.CYAN}╭────────────────────────────────────────────────────────────────────────╮{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  {S.BOLD}{S.BR_CYAN}PDF to A4 Landscape Batch Converter{S.RESET}                          {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  Target Dimensions: {S.YELLOW}842 × 595 pt{S.RESET} (A4 Landscape · 297 × 210 mm)      {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}╰────────────────────────────────────────────────────────────────────────╯{S.RESET}")


def print_summary(total: int, succeeded: int, failed: int, elapsed: float, out_dir: Path):
    S = TermStyle
    rate = succeeded / elapsed if elapsed > 0 else 0
    print(f"\n{S.BOLD}┌─ Conversion Summary ──────────────────────────────────────────────────┐{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Total Scanned        : {S.BR_CYAN}{total}{S.RESET} PDF(s)")
    print(f"{S.BOLD}│{S.RESET}  Successfully Converted: {S.BR_GRN}{succeeded}{S.RESET}")
    if failed > 0:
        print(f"{S.BOLD}│{S.RESET}  Failed               : {S.BR_RED}{failed}{S.RESET}")
    else:
        print(f"{S.BOLD}│{S.RESET}  Failed               : {S.GREEN}0{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Elapsed Time         : {S.YELLOW}{elapsed:.2f}s{S.RESET} ({rate:.1f} files/sec)")
    print(f"{S.BOLD}│{S.RESET}  Output Directory     : {S.DIM}{out_dir.resolve()}{S.RESET}")
    print(f"{S.BOLD}└───────────────────────────────────────────────────────────────────────┘{S.RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        prog="convert_pdf_a4l.py",
        description="Batch convert PDF files to A4 Landscape with dynamic terminal progress bar."
    )
    parser.add_argument("input", nargs="?", default=".", help="Input root directory or single PDF file (default: .)")
    parser.add_argument("output", nargs="?", default="./output", help="Output root directory (default: ./output)")
    parser.add_argument("--suffix", default="_A4L", help="Suffix for output filenames (default: _A4L)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = parser.parse_args()

    if args.no_color:
        TermStyle.strip_if_no_color(False)

    print_banner()

    input_path = Path(args.input).resolve()
    output_root = Path(args.output).resolve()

    if not input_path.exists():
        print(f"{TermStyle.RED}Error:{TermStyle.RESET} Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    # Discover PDFs
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            print(f"{TermStyle.RED}Error:{TermStyle.RESET} Input file is not a PDF: {input_path}", file=sys.stderr)
            return 1
        pdf_files = [input_path]
        base_dir = input_path.parent
    else:
        base_dir = input_path
        # Recursively find all PDFs excluding output directory
        all_pdfs = sorted(input_path.glob("**/*.pdf"))
        pdf_files = [
            p for p in all_pdfs
            if not str(p.resolve()).startswith(str(output_root))
        ]

    total_files = len(pdf_files)
    if total_files == 0:
        print(f"{TermStyle.YELLOW}No PDF files found to convert in:{TermStyle.RESET} {input_path}")
        return 0

    print(f" {TermStyle.GREEN}●{TermStyle.RESET} Found {TermStyle.BOLD}{total_files}{TermStyle.RESET} PDF file(s). Beginning conversion…\n")

    pbar = DynamicProgressBar(total=total_files, title="Converting A4L", width=28, use_color=not args.no_color)
    start_time = time.time()
    succeeded = 0
    failed = 0

    for idx, pdf in enumerate(pdf_files, 1):
        rel_path = pdf.relative_to(base_dir) if pdf != base_dir else Path(pdf.name)
        out_dir = output_root / rel_path.parent
        out_file = out_dir / f"{pdf.stem}{args.suffix}.pdf"

        pbar.update(current=idx, item_name=pdf.name, status_tag="CONVERTING")

        try:
            ok = convert_single_pdf(pdf, out_file)
            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1

    pbar.complete("All files processed")
    total_elapsed = time.time() - start_time
    print_summary(total_files, succeeded, failed, total_elapsed, output_root)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
