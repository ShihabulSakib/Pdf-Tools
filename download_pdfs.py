#!/usr/bin/env python3
"""
download_pdfs.py  --  Step 2 of 2

Reads links.json (made by scrape_playlist.py) and downloads the Drive files
ONE BY ONE, strictly in playlist order, into a dedicated folder.

File names:   Lecture 001 - <video title>.pdf
  * The number is the video's position in the playlist, so sorting the folder
    by name == the exact lecture order (good for printing in sequence).
  * If a video has more than one file:  "... Title.pdf", "... Title (2).pdf"

ORDER GUARANTEE
  * Pure serial loop -- no threads, no async. #1 is finished before #2 starts.
  * Failures never reorder anything; they are logged and the loop moves on.
    Re-running skips files that are already downloaded and valid.

Unicode / Bangla handling
  * `pathvalidate` -> Unicode-aware, filesystem-safe filename sanitising
  * `regex` (\\X)   -> truncates over-long names on grapheme-cluster boundaries,
                      so Bangla conjuncts/vowel signs are never cut in half
                      (Bangla chars are 3 bytes each in UTF-8; ext4 limit is
                      255 BYTES, not characters)
  * `unicodedata`  -> NFC normalisation

Install (arm64 Ubuntu):
    python3 -m venv ~/venv && source ~/venv/bin/activate
    pip install -U gdown pathvalidate regex

Usage:
    python3 download_pdfs.py links.json -d chem_pdfs
    python3 download_pdfs.py links.json -d chem_pdfs --from 10 --to 25
"""

import argparse
import json
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

import gdown
import regex as re
from pathvalidate import sanitize_filename
from terminal_progress import DynamicProgressBar

MAX_NAME_BYTES = 240          # leave headroom under the 255-byte limit


# ----------------------------------------------------------------------------
# filename helpers
# ----------------------------------------------------------------------------
def truncate_bytes(text: str, max_bytes: int) -> str:
    """Cut text to <= max_bytes (UTF-8) without splitting a grapheme cluster."""
    out, size = [], 0
    for g in re.findall(r"\X", text):
        b = len(g.encode("utf-8"))
        if size + b > max_bytes:
            break
        out.append(g)
        size += b
    return "".join(out).rstrip(" .-_")


def build_name(index: int, width: int, title: str, n: int, ext: str) -> str:
    title = unicodedata.normalize("NFC", title or "")
    title = re.sub(r"[\p{C}]+", " ", title)            # strip control chars
    title = re.sub(r"\s+", " ", title).strip()
    prefix = f"Lecture {index:0{width}d} - "
    suffix = f" ({n})" if n > 1 else ""
    budget = MAX_NAME_BYTES - len((prefix + suffix + ext).encode("utf-8"))
    title = truncate_bytes(title, max(budget, 20))
    name = sanitize_filename(prefix + title + suffix, replacement_text="_")
    return name + ext


# ----------------------------------------------------------------------------
# download helpers
# ----------------------------------------------------------------------------
def sniff(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(16)
    if head.startswith(b"%PDF"):
        return "pdf"
    low = head.lower()
    if low.startswith((b"<!doc", b"<html")):
        return "html"
    if head.startswith(b"PK"):
        return "zip"          # docx / pptx / zip
    return "bin"


def is_valid_pdf(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0 and sniff(path) == "pdf"
    except OSError:
        return False


class QuotaOrPermissionError(RuntimeError):
    """Drive refused the download for a reason retrying won't fix."""


def _explain_html(path: Path) -> str:
    """Read the HTML page Drive sent back and say WHY it refused."""
    with open(path, "rb") as f:
        text = f.read(300_000).decode("utf-8", errors="ignore").lower()
    if "too many users" in text or "quota" in text or "can't view or download this file at this time" in text:
        return ("Drive download quota exceeded for this file (too many downloads recently). "
                "This is temporary -- usually clears within ~24h.")
    if "request access" in text or "you need access" in text or "sign in" in text:
        return "File is not shared as 'Anyone with the link' (needs permission)."
    return "Drive returned an HTML page instead of the file (unknown reason)."


def direct_download(file_id: str, part: Path):
    """Fallback: Drive's direct-download endpoint (skips the virus-scan page
    that gdown sometimes fails to parse for bigger files)."""
    url = (f"https://drive.usercontent.google.com/download"
           f"?id={file_id}&export=download&confirm=t")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux aarch64)"})
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if part.stat().st_size == 0:
        raise RuntimeError("direct download returned an empty file")
    if sniff(part) == "html":
        raise QuotaOrPermissionError(_explain_html(part))


def _first_line(e: Exception) -> str:
    lines = [l.strip() for l in str(e).splitlines() if l.strip()]
    return lines[0] if lines else repr(e)


def download_one(file_id: str, dest_pdf: Path, retries: int, retry_wait: float):
    """Returns (final_path, note). Raises on final failure."""
    # gdown appends a random string + ".part" to whatever name it is given, which
    # overflows the 255-byte filename limit with long Bangla titles. So download
    # to a SHORT temp name and only rename to the long final name at the end.
    part = dest_pdf.parent / ".dl_current.part"

    def clean():
        for stale in dest_pdf.parent.glob(".dl_current.part*"):
            stale.unlink(missing_ok=True)

    clean()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            clean()
            try:
                out = gdown.download(id=file_id, output=str(part), quiet=False, use_cookies=False)
                if not out or not part.exists() or part.stat().st_size == 0:
                    raise RuntimeError("gdown returned no file")
            except Exception as gdown_err:  # noqa: BLE001
                print(f"    gdown failed ({_first_line(gdown_err)}) -> trying direct download ...",
                      file=sys.stderr)
                clean()
                direct_download(file_id, part)

            kind = sniff(part)
            if kind == "html":
                raise QuotaOrPermissionError(_explain_html(part))
            if kind == "pdf":
                part.replace(dest_pdf)
                return dest_pdf, None
            # not a PDF: keep it, but with an honest extension
            alt = dest_pdf.with_suffix("." + ("zip" if kind == "zip" else "bin"))
            part.replace(alt)
            return alt, f"not a PDF (looks like {kind}) -> saved as {alt.name}"
        except QuotaOrPermissionError as e:
            clean()
            print(f"    ! {e}", file=sys.stderr)
            raise                      # retrying right away would not help
        except Exception as e:  # noqa: BLE001
            last_err = e
            clean()
            print(f"    ! attempt {attempt}/{retries} failed: {_first_line(e)}", file=sys.stderr)
            if attempt < retries:
                time.sleep(retry_wait * attempt)
    raise last_err


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Serially download Drive PDFs listed in links.json.")
    ap.add_argument("json_file", help="JSON produced by scrape_playlist.py")
    ap.add_argument("-d", "--dir", default="pdfs", help="output folder (default: pdfs)")
    ap.add_argument("--from", dest="start", type=int, default=1, help="first lecture number to download")
    ap.add_argument("--to", dest="end", type=int, default=10**9, help="last lecture number to download")
    ap.add_argument("--retry-failed", action="store_true",
                    help="download only the lectures listed in <folder>/failed.json")
    ap.add_argument("--retries", type=int, default=3, help="attempts per file (default 3)")
    ap.add_argument("--retry-wait", type=float, default=5.0, help="base wait between retries, seconds")
    ap.add_argument("--delay", type=float, default=1.5, help="pause between files, seconds (default 1.5)")
    args = ap.parse_args()

    with open(args.json_file, encoding="utf-8") as f:
        data = json.load(f)

    videos = sorted(data["videos"], key=lambda v: v["index"])      # enforce serial order
    total = data.get("total_videos", len(videos))
    width = max(3, len(str(total)))

    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    only = None
    failed_path = out_dir / "failed.json"
    if args.retry_failed:
        if not failed_path.exists():
            sys.exit(f"{failed_path} not found -- nothing to retry.")
        with open(failed_path, encoding="utf-8") as f:
            only = {item["index"] for item in json.load(f)}
        print(f"Retrying lectures: {', '.join(str(i) for i in sorted(only))}\n")

    done = skipped_existing = 0
    failed, no_link = [], []
    selected = [
        v for v in videos
        if args.start <= v["index"] <= args.end
        and (only is None or v["index"] in only)
    ]
    total_links = sum(len(v.get("drive_links") or []) for v in selected)
    progress = DynamicProgressBar(total_links, "Downloading", unit="files") if total_links else None
    completed = 0

    for v in selected:                    # still strictly in playlist order
        idx = v["index"]

        links = v.get("drive_links") or []
        if not links:
            no_link.append(idx)
            print(f"[{idx:0{width}d}] no Drive link -- skipping  ({v.get('title','')})")
            continue

        for n, link in enumerate(links, start=1):          # serial, in JSON order
            name = build_name(idx, width, v.get("title", ""), n, ".pdf")
            dest = out_dir / name
            label = f"[{idx:0{width}d}] {name}"

            if is_valid_pdf(dest):
                skipped_existing += 1
                print(f"{label}  (already downloaded)")
                completed += 1
                if progress:
                    progress.update(completed, label, "SKIPPED")
                continue

            print(f"{label}\n    downloading {link['file_id']} ...")
            succeeded = False
            try:
                with progress.activity(completed, label, "DOWNLOADING"):
                    final, note = download_one(link["file_id"], dest, args.retries, args.retry_wait)
                done += 1
                succeeded = True
                print(f"    ok -> {final.name}" + (f"   [{note}]" if note else ""))
            except Exception as e:  # noqa: BLE001
                failed.append({"index": idx, "title": v.get("title"), "file_id": link["file_id"],
                               "url": link.get("original_url"), "error": str(e)})
                print(f"    !! FAILED: {e}", file=sys.stderr)

            completed += 1
            progress.update(completed, label, "DONE" if succeeded else "FAILED")
            time.sleep(args.delay)

    if progress:
        progress.complete("Finished")

    # ---- summary ------------------------------------------------------------
    print("\n================ SUMMARY ================")
    print(f"Downloaded now     : {done}")
    print(f"Already present    : {skipped_existing}")
    print(f"Videos w/o link    : {len(no_link)}" + (f"  ({', '.join('#'+str(i) for i in no_link)})" if no_link else ""))
    print(f"Failed             : {len(failed)}")
    print(f"Folder             : {out_dir.resolve()}")

    full_scope = args.start <= 1 and args.end >= 10**9 and only is None
    if failed:
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        print(f"Failure details    : {failed_path}")
        print("Retry only these with:  python3 download_pdfs.py "
              f"{args.json_file} -d {args.dir} --retry-failed")
        sys.exit(1)
    elif (only is not None or full_scope) and failed_path.exists():
        failed_path.unlink()              # everything in scope succeeded
        print("All previously failed files are now downloaded (failed.json removed).")


if __name__ == "__main__":
    main()
