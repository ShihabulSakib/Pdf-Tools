#!/usr/bin/env python3
"""
scrape_playlist.py  --  Step 1 of 2

Scans every video of a YouTube playlist (in playlist order), pulls out the
Google Drive file links from the description (and, if missing there, from the
pinned / uploader comments) and writes them to a JSON file.

ORDER GUARANTEE
  * Videos are processed strictly one after another, in playlist order.
  * Every video gets an entry in the JSON -- even if it has no link or fails --
    so the serial number ("index" = position in the playlist) never shifts.

Unicode / Bangla handling
  * `regex`        -> Unicode-aware regex (\\p{...} classes, Bangla danda '।'
                      right after a URL, control-char stripping)
  * `unicodedata`  -> NFC normalisation so Bangla conjuncts/vowel signs are
                      stored in one canonical form
  * JSON is written with ensure_ascii=False + UTF-8 (Bangla stays readable)

Install (arm64 Ubuntu):
    python3 -m venv ~/venv && source ~/venv/bin/activate
    pip install -U yt-dlp regex

Usage:
    python3 scrape_playlist.py "<playlist url>" -o links.json
    python3 scrape_playlist.py "<playlist url>" -o links.json --resume
"""

import argparse
import json
import os
import sys
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import regex as re
from yt_dlp import YoutubeDL

# ----------------------------------------------------------------------------
# Unicode helpers
# ----------------------------------------------------------------------------
CONTROL_CHARS = re.compile(r"[\p{C}]+")          # any control/format chars
URL_RE = re.compile(r"https?://(?:drive|docs)\.google\.com/[^\s<>\"'\[\]()]+")
# characters that often stick to the end of a URL (incl. Bangla danda ।  ॥)
TRAILING_JUNK = ".,;:!?)]}'\"।॥…"


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    s = CONTROL_CHARS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------------------
# Drive link parsing
# ----------------------------------------------------------------------------
def classify(url: str):
    """Return (kind, id) where kind in file|folder|gdoc|unknown."""
    url = url.rstrip(TRAILING_JUNK)
    m = re.search(r"/file/d/([\w-]+)", url)
    if m:
        return "file", m.group(1), url
    m = re.search(r"/folders/([\w-]+)", url)
    if m:
        return "folder", m.group(1), url
    m = re.search(r"/(?:document|presentation|spreadsheets|forms)/d/([\w-]+)", url)
    if m:
        return "gdoc", m.group(1), url
    path = urlparse(url).path
    if path.endswith(("/open", "/uc")):
        m = re.search(r"[?&]id=([\w-]+)", url)
        if m:
            return "file", m.group(1), url
    return "unknown", None, url


def extract_links(text: str, source: str):
    files, skipped = [], []
    for raw in URL_RE.findall(text or ""):
        kind, fid, url = classify(raw)
        if kind == "file":
            files.append({
                "file_id": fid,
                "download_url": f"https://drive.google.com/uc?id={fid}",
                "original_url": url,
                "source": source,
            })
        else:
            skipped.append({"kind": kind, "url": url, "source": source})
    return files, skipped


def dedupe(items, key):
    seen, out = set(), []
    for it in items:
        k = it[key]
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def normalize_playlist_url(raw: str) -> str:
    """Strip shell-escape backslashes/quotes and rebuild a canonical playlist URL."""
    raw = raw.replace("\\", "").strip().strip("'\"")
    m = re.search(r"[?&]list=([\w-]+)", raw)
    if m:
        return f"https://www.youtube.com/playlist?list={m.group(1)}"
    return raw


# ----------------------------------------------------------------------------
# yt-dlp wrappers
# ----------------------------------------------------------------------------
def list_playlist(url: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or [])]
    return clean_text(info.get("title") or ""), entries


def fetch_video(video_url: str, with_comments: bool, max_comments: int):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if with_comments:
        opts["getcomments"] = True
        opts["extractor_args"] = {
            "youtube": {
                "max_comments": [f"{max_comments},{max_comments},0,0"],
                "comment_sort": ["top"],
            }
        }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(video_url, download=False)


def fetch_with_retry(video_url, with_comments, max_comments, retries=3):
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fetch_video(video_url, with_comments, max_comments)
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 3 * attempt
            print(f"    ! attempt {attempt}/{retries} failed: {e}", file=sys.stderr)
            if attempt < retries:
                time.sleep(wait)
    raise last


# ----------------------------------------------------------------------------
# JSON IO (atomic)
# ----------------------------------------------------------------------------
def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_existing(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {v["video_id"]: v for v in data.get("videos", []) if v.get("status") == "ok"}
    except Exception:  # noqa: BLE001
        return {}


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Scrape Drive links from a YouTube playlist (order preserved).")
    ap.add_argument("playlist", help="YouTube playlist URL")
    ap.add_argument("-o", "--output", default="links.json", help="output JSON (default: links.json)")
    ap.add_argument("--resume", action="store_true",
                    help="reuse already-scraped OK entries from an existing JSON")
    ap.add_argument("--no-comments", action="store_true",
                    help="never look at comments (description only)")
    ap.add_argument("--max-comments", type=int, default=50,
                    help="top comments to scan when description has no link (default 50)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between videos (default 1.0)")
    args = ap.parse_args()

    args.playlist = normalize_playlist_url(args.playlist)
    print(f"Reading playlist ... {args.playlist}")
    playlist_title, entries = list_playlist(args.playlist)
    total = len(entries)
    print(f"Playlist: {playlist_title or '(untitled)'}  --  {total} videos\n")

    previous = load_existing(args.output) if args.resume else {}
    result = {
        "playlist_url": args.playlist,
        "playlist_title": playlist_title,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_videos": total,
        "videos": [],
    }

    for idx, entry in enumerate(entries, start=1):          # idx = serial number
        vid = (entry or {}).get("id") or ""
        flat_title = clean_text((entry or {}).get("title") or "")
        video_url = f"https://www.youtube.com/watch?v={vid}" if vid else ""

        # resume: keep old OK entry but refresh its index (position in playlist)
        if vid in previous:
            rec = dict(previous[vid])
            rec["index"] = idx
            result["videos"].append(rec)
            print(f"[{idx:03d}/{total}] (cached) {rec['title']}")
            continue

        rec = {
            "index": idx,
            "video_id": vid,
            "title": flat_title,
            "video_url": video_url,
            "status": "ok",
            "drive_links": [],
            "skipped_links": [],
            "error": None,
        }
        print(f"[{idx:03d}/{total}] {flat_title}")

        if not vid:
            rec["status"] = "error"
            rec["error"] = "playlist entry has no video id (deleted/private?)"
            result["videos"].append(rec)
            save_json(args.output, result)
            continue

        try:
            # pass 1: description only (fast)
            info = fetch_with_retry(video_url, with_comments=False, max_comments=0)
            rec["title"] = clean_text(info.get("title") or flat_title)
            files, skipped = extract_links(info.get("description") or "", "description")

            # pass 2: pinned / uploader comments, only if description had nothing
            if not files and not args.no_comments:
                info2 = fetch_with_retry(video_url, with_comments=True, max_comments=args.max_comments)
                comments = info2.get("comments") or []
                # pinned + uploader comments first, then everything else
                prio = [c for c in comments if c.get("is_pinned") or c.get("author_is_uploader")]
                for c in prio:
                    src = "pinned_comment" if c.get("is_pinned") else "uploader_comment"
                    f2, s2 = extract_links(c.get("text") or "", src)
                    files += f2
                    skipped += s2
                if not files:   # last resort: any other comment
                    for c in comments:
                        if c in prio:
                            continue
                        f2, s2 = extract_links(c.get("text") or "", "comment")
                        files += f2
                        skipped += s2

            rec["drive_links"] = dedupe(files, "file_id")
            rec["skipped_links"] = dedupe(skipped, "url")

            if rec["drive_links"]:
                for l in rec["drive_links"]:
                    print(f"      -> {l['file_id']}  ({l['source']})")
            else:
                print("      -> no Drive file link found")
            for s in rec["skipped_links"]:
                print(f"      (skipped {s['kind']} link: {s['url']})")

        except Exception as e:  # noqa: BLE001
            rec["status"] = "error"
            rec["error"] = str(e)
            print(f"      !! failed: {e}", file=sys.stderr)

        result["videos"].append(rec)
        save_json(args.output, result)          # checkpoint after every video
        time.sleep(args.delay)

    # final write (also covers the all-cached case)
    result["videos"].sort(key=lambda v: v["index"])
    save_json(args.output, result)

    ok = sum(1 for v in result["videos"] if v["status"] == "ok")
    with_links = sum(1 for v in result["videos"] if v["drive_links"])
    errs = [v for v in result["videos"] if v["status"] != "ok"]
    no_link = [v for v in result["videos"] if v["status"] == "ok" and not v["drive_links"]]

    print("\n================ SUMMARY ================")
    print(f"Videos           : {total}")
    print(f"Scraped OK       : {ok}")
    print(f"With Drive links : {with_links}")
    if no_link:
        print("No link found at : " + ", ".join(f"#{v['index']}" for v in no_link))
    if errs:
        print("Errors at        : " + ", ".join(f"#{v['index']}" for v in errs)
              + "   (re-run with --resume to retry only these)")
    print(f"Saved to         : {args.output}")


if __name__ == "__main__":
    main()
