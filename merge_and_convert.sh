#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  merge_and_convert.sh                                                ║
# ║                                                                      ║
# ║  Processes PDFs per folder:                                          ║
# ║    • 2+ PDFs in a folder → merge losslessly (qpdf) → convert A4L   ║
# ║    • 1  PDF  in a folder → convert directly to A4L                  ║
# ║                                                                      ║
# ║  Output mirrors the input folder tree under ./output/               ║
# ║                                                                      ║
# ║  Usage:                                                              ║
# ║    ./merge_and_convert.sh                 # scan current directory   ║
# ║    ./merge_and_convert.sh /path/to/pdfs   # scan a specific path     ║
# ║                                                                      ║
# ║  Requires: qpdf, ghostscript (gs)                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ── Strict mode ────────────────────────────────────────────────────────
set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────
RED=$'\033[0;31m'; YEL=$'\033[0;33m'; GRN=$'\033[0;32m'
CYN=$'\033[0;36m'; BLD=$'\033[1m'; RST=$'\033[0m'
info()  { echo "${CYN}[INFO]${RST}  $*"; }
ok()    { echo "${GRN}[OK]${RST}    $*"; }
warn()  { echo "${YEL}[WARN]${RST}  $*"; }
err()   { echo "${RED}[ERROR]${RST} $*" >&2; }
die()   { err "$*"; exit 1; }
sep()   { echo "${BLD}──────────────────────────────────────────${RST}"; }

# ── Arguments ──────────────────────────────────────────────────────────
INPUT_ROOT="${1:-.}"
INPUT_ROOT="$(cd "$INPUT_ROOT" && pwd)"
OUTPUT_ROOT="$INPUT_ROOT/output"

# ── Dependency check ───────────────────────────────────────────────────
sep
echo "${BLD}merge_and_convert.sh${RST}"
sep
info "Checking required tools…"

missing=0
for tool in qpdf gs; do
    if ! command -v "$tool" &>/dev/null; then
        err "Required tool not found: ${BLD}$tool${RST}"
        missing=1
    else
        ver=$("$tool" --version 2>&1 | head -1)
        ok "$tool → $ver"
    fi
done
[[ $missing -eq 1 ]] && die "Install missing tools and re-run."

# ── Cleanup any leftover temp files on exit ────────────────────────────
cleanup() {
    find "$OUTPUT_ROOT" -name "_tmp_merge_*.pdf" -delete 2>/dev/null || true
    find "$OUTPUT_ROOT" -name "_tmp_clean_*.pdf" -delete 2>/dev/null || true
}
trap cleanup EXIT

# ── Create output root ─────────────────────────────────────────────────
mkdir -p "$OUTPUT_ROOT"

# ── Collect all unique directories that contain at least one PDF ───────
sep
info "Scanning for PDFs in: ${BLD}$INPUT_ROOT${RST}"
info "Excluding:            ${BLD}$OUTPUT_ROOT${RST}"

# Get all PDF files (null-delimited, sorted), excluding output/ by full path
mapfile -d '' ALL_PDFS < <(
    find "$INPUT_ROOT" \
        -path "$OUTPUT_ROOT" -prune \
        -o -type f -name "*.pdf" -print0 \
    | sort -z
)

TOTAL_FILES=${#ALL_PDFS[@]}
[[ $TOTAL_FILES -eq 0 ]] && die "No PDF files found under $INPUT_ROOT"

# Collect unique folders (preserving order)
declare -A SEEN_DIRS
FOLDER_LIST=()
for f in "${ALL_PDFS[@]}"; do
    dir="$(dirname "$f")"
    if [[ -z "${SEEN_DIRS[$dir]+_}" ]]; then
        SEEN_DIRS[$dir]=1
        FOLDER_LIST+=("$dir")
    fi
done

info "Found ${BLD}$TOTAL_FILES${RST} PDF(s) across ${BLD}${#FOLDER_LIST[@]}${RST} folder(s):"
for f in "${ALL_PDFS[@]}"; do
    echo "    📄  ${f#$INPUT_ROOT/}"
done

# ── Counters for summary ───────────────────────────────────────────────
TOTAL_MERGED=0
TOTAL_CONVERTED=0
TOTAL_FAILED=0
TOTAL_SANITIZED=0

# ══════════════════════════════════════════════════════════════════════
# FUNCTION: sanitize_pdf
#
# Called ONLY when qpdf fails — runs the broken PDF through Ghostscript
# pdfwrite, which re-bakes the entire PDF structure from scratch while
# preserving all image data and content. This fixes malformed internals
# such as duplicate dictionary keys caused by printing/editing software
# that re-saves PDFs incorrectly (e.g. Raspberry Pi printing software).
#
# Usage : sanitize_pdf <input.pdf> <output_sanitized.pdf>
# Returns: 0 on success, 1 on failure
# ══════════════════════════════════════════════════════════════════════
sanitize_pdf() {
    local infile="$1"
    local outfile="$2"
    local fname
    fname="$(basename "$infile")"

    warn "  ┌─ PDF structure problem detected in: ${BLD}$fname${RST}"
    warn "  │  qpdf reported malformed internals (e.g. duplicate dictionary keys)."
    warn "  │  This is typically caused by printing or editing software that"
    warn "  │  re-saved the PDF with an invalid internal structure."
    warn "  │  Attempting auto-repair via Ghostscript…"

    local gs_log
    gs_log=$(gs \
        -dNOPAUSE \
        -dBATCH \
        -sDEVICE=pdfwrite \
        -dCompatibilityLevel=1.4 \
        -dPrinted=false \
        -sOutputFile="$outfile" \
        "$infile" 2>&1)

    local gs_exit=$?

    if [[ $gs_exit -eq 0 ]]; then
        warn "  └─ ${GRN}Repair succeeded${RST}${YEL} — sanitized: $fname${RST}"
        return 0
    else
        err "  └─ Repair FAILED for: ${BLD}$fname${RST}"
        err "     Ghostscript output:"
        while IFS= read -r line; do
            err "       │  $line"
        done <<< "$gs_log"
        return 1
    fi
}

# ── Process each folder ────────────────────────────────────────────────
sep
echo "${BLD}Processing folders…${RST}"

for folder in "${FOLDER_LIST[@]}"; do

    # Collect PDFs in this folder only (maxdepth 1, not recursive, never output/)
    mapfile -d '' FOLDER_PDFS < <(
        find "$folder" -maxdepth 1 \
            -path "$OUTPUT_ROOT" -prune \
            -o -type f -name "*.pdf" -print0 \
        | sort -z
    )

    COUNT=${#FOLDER_PDFS[@]}
    [[ $COUNT -eq 0 ]] && continue

    # Mirror this folder's relative path into output/
    reldir="${folder#$INPUT_ROOT}"     # e.g. "" or "/projectA"
    reldir="${reldir#/}"               # strip leading slash
    if [[ -z "$reldir" ]]; then
        outdir="$OUTPUT_ROOT"          # root-level PDFs → output/
    else
        outdir="$OUTPUT_ROOT/$reldir"
    fi
    mkdir -p "$outdir"

    # Display label
    display_folder="${reldir:-.}"
    sep
    if [[ $COUNT -gt 1 ]]; then
        echo "${BLD}📁 $display_folder  ($COUNT PDFs — merge then convert)${RST}"
    else
        echo "${BLD}📁 $display_folder  (1 PDF — convert only)${RST}"
    fi

    # Output filename derived from the first PDF in this folder
    first_pdf="${FOLDER_PDFS[0]}"
    first_name="$(basename "$first_pdf" .pdf)"

    if [[ $COUNT -gt 1 ]]; then

        # ── Merge losslessly with qpdf ─────────────────────────────────
        TMP_MERGED="$outdir/_tmp_merge_${first_name}.pdf"
        OUTFILE="$outdir/${first_name}_merged_A4L.pdf"

        info "Merging $COUNT files using qpdf (lossless — zero re-encoding)…"
        QPDF_ARGS=(--empty --pages)
        for f in "${FOLDER_PDFS[@]}"; do
            echo "    ↳  $(basename "$f")"
            QPDF_ARGS+=("$f" "1-z")    # 1-z = all pages
        done
        QPDF_ARGS+=(-- "$TMP_MERGED")

        # Capture qpdf output — || true prevents set -e from killing the script
        # on a non-zero exit; we check QPDF_EXIT ourselves below
        QPDF_LOG=$(qpdf "${QPDF_ARGS[@]}" 2>&1) || true
        QPDF_EXIT=$?

        if [[ $QPDF_EXIT -eq 0 ]]; then
            # ── Clean merge — no issues ────────────────────────────────
            pages=$(qpdf --show-npages "$TMP_MERGED" 2>/dev/null || echo "?")
            ok "Merged → $pages total pages"
            (( TOTAL_MERGED++ )) || true

        else
            # ── qpdf failed — log what it said, then attempt repair ────
            err "qpdf failed for folder: ${BLD}$display_folder${RST}"
            err "qpdf output:"
            while IFS= read -r line; do
                err "   │  $line"
            done <<< "$QPDF_LOG"
            err "  ↳ Will now attempt to sanitize each PDF in this folder and retry."

            rm -f "$TMP_MERGED"

            # Sanitize every PDF in this folder into temp cleaned copies
            SANITIZED_PDFS=()
            SANITIZE_FAILED=0
            for f in "${FOLDER_PDFS[@]}"; do
                fname="$(basename "$f")"
                tmp_clean="$outdir/_tmp_clean_${fname}"
                if sanitize_pdf "$f" "$tmp_clean"; then
                    SANITIZED_PDFS+=("$tmp_clean")
                    (( TOTAL_SANITIZED++ )) || true
                else
                    err "Could not repair: ${BLD}$fname${RST} — aborting merge for this folder."
                    SANITIZE_FAILED=1
                    break
                fi
            done

            # Clean up sanitized temps and skip if any repair failed
            if [[ $SANITIZE_FAILED -eq 1 ]]; then
                for tmp in "${SANITIZED_PDFS[@]}"; do rm -f "$tmp"; done
                warn "Skipping folder: $display_folder — one or more PDFs could not be repaired."
                (( TOTAL_FAILED++ )) || true
                continue
            fi

            # Retry qpdf merge on the sanitized copies
            info "Retrying qpdf merge on repaired PDFs…"
            RETRY_ARGS=(--empty --pages)
            for f in "${SANITIZED_PDFS[@]}"; do
                RETRY_ARGS+=("$f" "1-z")
            done
            RETRY_ARGS+=(-- "$TMP_MERGED")

            # Same pattern — || true so set -e doesn't exit before we check RETRY_EXIT
            RETRY_LOG=$(qpdf "${RETRY_ARGS[@]}" 2>&1) || true
            RETRY_EXIT=$?

            # Clean up sanitized temps regardless of retry outcome
            for tmp in "${SANITIZED_PDFS[@]}"; do rm -f "$tmp"; done

            if [[ $RETRY_EXIT -eq 0 ]]; then
                pages=$(qpdf --show-npages "$TMP_MERGED" 2>/dev/null || echo "?")
                ok "Retry merge succeeded → $pages total pages"
                (( TOTAL_MERGED++ )) || true
            else
                err "Retry merge also failed for folder: ${BLD}$display_folder${RST}"
                err "qpdf retry output:"
                while IFS= read -r line; do
                    err "   │  $line"
                done <<< "$RETRY_LOG"
                warn "Skipping folder: $display_folder"
                rm -f "$TMP_MERGED"
                (( TOTAL_FAILED++ )) || true
                continue
            fi
        fi

        # ── Convert merged file → A4 Landscape ────────────────────────
        info "Converting merged file to A4 Landscape…"
        if gs \
            -dNOPAUSE \
            -dBATCH \
            -dQUIET \
            -sDEVICE=pdfwrite \
            -dPDFFitPage \
            -dFIXEDMEDIA \
            -dDEVICEWIDTHPOINTS=842 \
            -dDEVICEHEIGHTPOINTS=595 \
            -dAutoRotatePages=/None \
            -sOutputFile="$OUTFILE" \
            "$TMP_MERGED" 2>/dev/null; then
            SIZE=$(du -h "$OUTFILE" | cut -f1)
            ok "Output: output/${reldir:+$reldir/}${first_name}_merged_A4L.pdf  (${SIZE})"
            (( TOTAL_CONVERTED++ )) || true
        else
            warn "Ghostscript failed for merged file in: $display_folder"
            (( TOTAL_FAILED++ )) || true
        fi

        rm -f "$TMP_MERGED"

    else

        # ── Single PDF — convert directly ──────────────────────────────
        OUTFILE="$outdir/${first_name}_A4L.pdf"
        info "Converting: $(basename "$first_pdf") → ${first_name}_A4L.pdf"
        if gs \
            -dNOPAUSE \
            -dBATCH \
            -dQUIET \
            -sDEVICE=pdfwrite \
            -dPDFFitPage \
            -dFIXEDMEDIA \
            -dDEVICEWIDTHPOINTS=842 \
            -dDEVICEHEIGHTPOINTS=595 \
            -dAutoRotatePages=/None \
            -sOutputFile="$OUTFILE" \
            "$first_pdf" 2>/dev/null; then
            SIZE=$(du -h "$OUTFILE" | cut -f1)
            ok "Output: output/${reldir:+$reldir/}${first_name}_A4L.pdf  (${SIZE})"
            (( TOTAL_CONVERTED++ )) || true
        else
            warn "Ghostscript failed for: $(basename "$first_pdf")"
            (( TOTAL_FAILED++ )) || true
        fi

    fi

done

# ── Final summary ──────────────────────────────────────────────────────
sep
echo "${GRN}${BLD}✔ All done!${RST}"
echo ""
echo "  Total PDFs found  : $TOTAL_FILES"
echo "  Folders merged    : $TOTAL_MERGED"
echo "  PDFs auto-repaired: $TOTAL_SANITIZED"
echo "  Files converted   : $TOTAL_CONVERTED"
echo "  Failures          : $TOTAL_FAILED"
echo ""
echo "  ${BLD}Output tree:${RST}"
find "$OUTPUT_ROOT" -not -name "_tmp_*" | sort | while read -r line; do
    rel="${line#$INPUT_ROOT/}"
    depth=$(echo "$rel" | tr -cd '/' | wc -c)
    indent=$(printf '  %.0s' $(seq 1 "$depth"))
    base=$(basename "$line")
    if [[ -d "$line" ]]; then
        echo "  ${indent}📁 ${BLD}${base}/${RST}"
    else
        echo "  ${indent}📄 $base"
    fi
done
sep
