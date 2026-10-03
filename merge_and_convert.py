#!/usr/bin/env python3
"""
merge_and_convert.py — PDF Folder Batch Merger and A4 Landscape Converter
========================================================================
Processes PDFs on a per-folder basis:
  • 2+ PDFs in a folder → Merges losslessly → Converts to A4 Landscape
  • 1  PDF  in a folder → Converts directly to A4 Landscape
  • Corrupt PDF auto-repair / sanitization fallback

Features dynamic, high-fidelity terminal progress bar with accurate ETA,
status metrics, and box-drawing UI for Windows PowerShell, CMD, Bash, and Zsh.
"""

import sys
import os
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict

# Enable ANSI escape sequences on Windows 10/11 Command Prompt and PowerShell
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hOut = kernel32.GetStdHandle(-11)
        out_mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(hOut, ctypes.byref(out_mode))
        kernel32.SetConsoleMode(hOut, out_mode.value | 0x0004)
    except Exception:
        os.system("")

# ══════════════════════════════════════════════════════════════════════════════
# TERMINAL UI & PROGRESS BAR
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
    def __init__(self, total: int, title: str = "Processing", width: int = 26, use_color: bool = True):
        self.total = max(1, total)
        self.current = 0
        self.title = title
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

    def update(self, current: int, item_name: str = "", stage: str = "PROCESS"):
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
            time_str = f"{S.DIM}[{elapsed_str} < {eta_str}, {rate:4.1f}/s]{S.RESET}"
            stage_str = f"{S.BR_YEL}[{stage}]{S.RESET}"
        else:
            bar_track = f"[{'█' * filled}{'░' * empty}]"
            pct_str = f"{percent:5.1f}%"
            cnt_str = f"{self.current}/{self.total}"
            time_str = f"[{elapsed_str} < {eta_str}, {rate:4.1f}/s]"
            stage_str = f"[{stage}]"

        fixed_overhead = len(self.title) + self.bar_width + len(time_str) + len(stage) + 35
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

    def complete(self, msg: str = "Done"):
        if self.is_tty:
            elapsed = self.format_time(time.time() - self.start_time)
            S = Style
            full_bar = "█" * self.bar_width
            sys.stdout.write(
                f"\r{S.CLEAR_LN} {S.BOLD}{self.title}{S.RESET} "
                f"{S.BR_GRN}{full_bar}{S.RESET} {S.BOLD}{S.BR_GRN}100.0%{S.RESET} "
                f"({self.total}/{self.total}) {S.GREEN}✔ {msg}{S.RESET} in {elapsed}\n"
            )
            sys.stdout.flush()


# ══════════════════════════════════════════════════════════════════════════════
# PDF REPAIR, MERGE, AND CONVERSION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def get_ghostscript_bin():
    for name in ["gswin64c", "gswin32c", "gs"]:
        p = shutil.which(name)
        if p:
            return p
    return None


def get_qpdf_bin():
    return shutil.which("qpdf")


def sanitize_pdf(infile: Path, outfile: Path) -> bool:
    """
    Repair broken/malformed PDF structure by running it through Ghostscript pdfwrite.
    Preserves all vector data and images while reconstructing the xref and catalog tables.
    """
    gs_bin = get_ghostscript_bin()
    if not gs_bin:
        return False

    cmd = [
        gs_bin,
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dPrinted=false",
        f"-sOutputFile={outfile}",
        str(infile)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and outfile.exists() and outfile.stat().st_size > 0


def merge_pdf_files(pdf_list: list[Path], merged_out: Path) -> tuple[bool, bool]:
    """
    Merges multiple PDF files into merged_out losslessly.
    Returns: (success: bool, was_sanitized: bool)
    """
    merged_out.parent.mkdir(parents=True, exist_ok=True)
    was_sanitized = False

    # Strategy 1: PyMuPDF if installed
    try:
        import fitz
        out_doc = fitz.open()
        for p in pdf_list:
            doc = fitz.open(str(p))
            out_doc.insert_pdf(doc)
            doc.close()
        out_doc.save(str(merged_out), garbage=4, deflate=True)
        out_doc.close()
        return True, False
    except ImportError:
        pass
    except Exception:
        # If PyMuPDF failed, fall through to qpdf / gs
        pass

    # Strategy 2: qpdf (lossless zero re-encoding)
    qpdf_bin = get_qpdf_bin()
    if qpdf_bin:
        args = [qpdf_bin, "--empty", "--pages"]
        for p in pdf_list:
            args.extend([str(p), "1-z"])
        args.extend(["--", str(merged_out)])

        res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            return True, False

        # If qpdf failed due to malformed dictionary keys, attempt Ghostscript repair on each file
        temp_sanitized = []
        all_repaired = True
        for p in pdf_list:
            repaired_copy = merged_out.parent / f"_tmp_clean_{p.name}"
            if sanitize_pdf(p, repaired_copy):
                temp_sanitized.append(repaired_copy)
            else:
                all_repaired = False
                break

        if all_repaired:
            retry_args = [qpdf_bin, "--empty", "--pages"]
            for rp in temp_sanitized:
                retry_args.extend([str(rp), "1-z"])
            retry_args.extend(["--", str(merged_out)])

            retry_res = subprocess.run(retry_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for rp in temp_sanitized:
                if rp.exists():
                    try: rp.unlink()
                    except: pass

            if retry_res.returncode == 0:
                return True, True

    # Strategy 3: Ghostscript pdfwrite merge
    gs_bin = get_ghostscript_bin()
    if gs_bin:
        cmd = [
            gs_bin,
            "-dNOPAUSE",
            "-dBATCH",
            "-dQUIET",
            "-sDEVICE=pdfwrite",
            f"-sOutputFile={merged_out}",
        ] + [str(p) for p in pdf_list]

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode == 0, was_sanitized

    return False, False


def convert_to_a4l(infile: Path, outfile: Path) -> bool:
    """Converts a PDF to A4 Landscape (842 x 595 pt)"""
    outfile.parent.mkdir(parents=True, exist_ok=True)

    # Strategy 1: PyMuPDF
    try:
        import fitz
        src = fitz.open(str(infile))
        out = fitz.open()
        for page in src:
            p = out.new_page(width=842.0, height=595.0)
            rect = page.rect
            scale = min(842.0 / rect.width, 595.0 / rect.height)
            sw, sh = rect.width * scale, rect.height * scale
            ox, oy = (842.0 - sw) / 2.0, (595.0 - sh) / 2.0
            p.show_pdf_page(fitz.Rect(ox, oy, ox + sw, oy + sh), src, page.number, keep_proportion=False, overlay=True)
        out.save(str(outfile), garbage=4, deflate=True)
        src.close()
        out.close()
        return True
    except ImportError:
        pass
    except Exception:
        pass

    # Strategy 2: Ghostscript
    gs_bin = get_ghostscript_bin()
    if not gs_bin:
        return False

    cmd = [
        gs_bin,
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dPDFFitPage",
        "-dFIXEDMEDIA",
        "-dDEVICEWIDTHPOINTS=842",
        "-dDEVICEHEIGHTPOINTS=595",
        "-dAutoRotatePages=/None",
        f"-sOutputFile={outfile}",
        str(infile)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0 and outfile.exists()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PROCESSOR
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="merge_and_convert.py",
        description="Lossless PDF folder batch merger and A4 Landscape converter with dynamic terminal progress bar."
    )
    parser.add_argument("input", nargs="?", default=".", help="Root directory containing PDF folders (default: .)")
    parser.add_argument("output", nargs="?", default="./output", help="Output directory root (default: ./output)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color codes")
    args = parser.parse_args()

    if args.no_color:
        Style.strip_colors()

    S = Style
    print(f"{S.CYAN}╭────────────────────────────────────────────────────────────────────────╮{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  {S.BOLD}{S.BR_CYAN}PDF Folder Merger & A4 Landscape Converter{S.RESET}                            {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  • Multi-PDF folders : Merged losslessly → Converted to A4L             {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}│{S.RESET}  • Single-PDF folders: Converted directly to A4L                        {S.CYAN}│{S.RESET}")
    print(f"{S.CYAN}╰────────────────────────────────────────────────────────────────────────╯{S.RESET}")

    input_root = Path(args.input).resolve()
    output_root = Path(args.output).resolve()

    if not input_root.exists():
        print(f"{S.RED}Error:{S.RESET} Input root does not exist: {input_root}", file=sys.stderr)
        return 1

    # Discover and group PDFs by directory
    all_pdfs = sorted(input_root.glob("**/*.pdf"))
    valid_pdfs = [p for p in all_pdfs if not str(p.resolve()).startswith(str(output_root))]

    folder_map = defaultdict(list)
    for p in valid_pdfs:
        folder_map[p.parent].append(p)

    total_folders = len(folder_map)
    total_files = len(valid_pdfs)

    if total_folders == 0:
        print(f"{S.YELLOW}No PDF files found to process in:{S.RESET} {input_root}")
        return 0

    print(f" {S.GREEN}●{S.RESET} Found {S.BOLD}{total_files}{S.RESET} PDF(s) in {S.BOLD}{total_folders}{S.RESET} folder(s). Starting execution…\n")

    pbar = DynamicProgressBar(total=total_folders, title="Batch Progress", width=26, use_color=not args.no_color)
    start_time = time.time()

    stat_merged = 0
    stat_converted = 0
    stat_sanitized = 0
    stat_failed = 0

    for idx, (folder, pdfs) in enumerate(folder_map.items(), 1):
        rel_dir = folder.relative_to(input_root) if folder != input_root else Path("")
        dest_dir = output_root / rel_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        folder_name = folder.name if folder != input_root else "root"
        count = len(pdfs)
        first_pdf = pdfs[0]

        if count > 1:
            pbar.update(current=idx, item_name=f"{folder_name}/ ({count} PDFs)", stage="MERGING")
            tmp_merged = dest_dir / f"_tmp_merge_{first_pdf.stem}.pdf"
            final_out = dest_dir / f"{first_pdf.stem}_merged_A4L.pdf"

            # Merge
            ok_merge, was_sanitized = merge_pdf_files(pdfs, tmp_merged)
            if was_sanitized:
                stat_sanitized += len(pdfs)

            if ok_merge:
                stat_merged += 1
                pbar.update(current=idx, item_name=f"{folder_name}/ → {final_out.name}", stage="CONVERTING")
                ok_convert = convert_to_a4l(tmp_merged, final_out)
                if ok_convert:
                    stat_converted += 1
                else:
                    stat_failed += 1
                if tmp_merged.exists():
                    try: tmp_merged.unlink()
                    except: pass
            else:
                stat_failed += 1
                if tmp_merged.exists():
                    try: tmp_merged.unlink()
                    except: pass

        else:
            # Single PDF
            pbar.update(current=idx, item_name=f"{folder_name}/{first_pdf.name}", stage="CONVERTING")
            final_out = dest_dir / f"{first_pdf.stem}_A4L.pdf"
            ok = convert_to_a4l(first_pdf, final_out)
            if ok:
                stat_converted += 1
            else:
                stat_failed += 1

    pbar.complete("All folders processed")
    elapsed = time.time() - start_time

    # Summary Card
    print(f"\n{S.BOLD}┌─ Process Summary ─────────────────────────────────────────────────────┐{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Total Folders Scanned  : {S.BR_CYAN}{total_folders}{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Total Input PDFs       : {S.BR_CYAN}{total_files}{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Folders Merged         : {S.BR_GRN}{stat_merged}{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Files Converted (A4L)  : {S.BR_GRN}{stat_converted}{S.RESET}")
    if stat_sanitized > 0:
        print(f"{S.BOLD}│{S.RESET}  PDFs Auto-Repaired     : {S.BR_YEL}{stat_sanitized}{S.RESET}")
    if stat_failed > 0:
        print(f"{S.BOLD}│{S.RESET}  Failed Operations      : {S.BR_RED}{stat_failed}{S.RESET}")
    else:
        print(f"{S.BOLD}│{S.RESET}  Failed Operations      : {S.GREEN}0{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Total Elapsed Time     : {S.YELLOW}{elapsed:.2f}s{S.RESET}")
    print(f"{S.BOLD}│{S.RESET}  Output Directory       : {S.DIM}{output_root}{S.RESET}")
    print(f"{S.BOLD}└───────────────────────────────────────────────────────────────────────┘{S.RESET}\n")

    return 0 if stat_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
