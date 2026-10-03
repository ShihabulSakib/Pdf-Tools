#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  convert_pdf_a4l.sh                                                  ║
# ║  Converts PDFs to A4 Landscape (842×595 pt) with Dynamic Progress Bar║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── Terminal Styling ───────────────────────────────────────────────────
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[0;33m'; CYN=$'\033[0;36m'
BLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'; CLR=$'\033[K'
BR_CYN=$'\033[1;36m'; BR_GRN=$'\033[1;32m'

INPUT_ROOT="${1:-.}"
INPUT_ROOT="$(cd "$INPUT_ROOT" && pwd)"
OUTPUT_ROOT="$INPUT_ROOT/output"

echo "${CYN}╭────────────────────────────────────────────────────────────────────────╮${RST}"
echo "${CYN}│${RST}  ${BLD}${BR_CYN}PDF to A4 Landscape Batch Converter (Bash)${RST}                           ${CYN}│${RST}"
echo "${CYN}│${RST}  Target Dimensions: ${YEL}842 × 595 pt${RST} (297 × 210 mm)                               ${CYN}│${RST}"
echo "${CYN}╰────────────────────────────────────────────────────────────────────────╯${RST}"

if ! command -v gs &>/dev/null; then
    echo "${RED}Error:${RST} Ghostscript ('gs') is required but not installed." >&2
    exit 1
fi

mkdir -p "$OUTPUT_ROOT"

# Scan PDFs excluding output directory
mapfile -d '' PDF_FILES < <(
    find "$INPUT_ROOT" \
        -path "$OUTPUT_ROOT" -prune \
        -o -type f -name "*.pdf" -print0 \
    | sort -z
)

TOTAL=${#PDF_FILES[@]}
if [[ $TOTAL -eq 0 ]]; then
    echo "${YEL}No PDF files found to convert in:${RST} $INPUT_ROOT"
    exit 0
fi

echo " ${GRN}●${RST} Found ${BLD}$TOTAL${RST} PDF(s). Starting conversion with dynamic progress…"$'\n'

START_TIME=$(date +%s)
BAR_WIDTH=26
CURRENT=0
SUCCEEDED=0
FAILED=0

format_time() {
    local secs=$1
    local m=$((secs / 60))
    local s=$((secs % 60))
    printf "%02d:%02d" "$m" "$s"
}

for infile in "${PDF_FILES[@]}"; do
    (( CURRENT++ )) || true
    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_TIME ))
    [[ $ELAPSED -eq 0 ]] && ELAPSED=1
    
    # ETA calculation
    RATE_X100=$(( CURRENT * 100 / ELAPSED ))
    REMAINING_ITEMS=$(( TOTAL - CURRENT ))
    if [[ $RATE_X100 -gt 0 ]]; then
        ETA_SECS=$(( REMAINING_ITEMS * 100 / RATE_X100 ))
    else
        ETA_SECS=0
    fi
    ELAPSED_FMT=$(format_time "$ELAPSED")
    ETA_FMT=$(format_time "$ETA_SECS")

    # Percentage & Bar
    PERCENT=$(( CURRENT * 100 / TOTAL ))
    FILLED=$(( CURRENT * BAR_WIDTH / TOTAL ))
    EMPTY=$(( BAR_WIDTH - FILLED ))

    BAR_FILL=$(printf "%0.s█" $(seq 1 $FILLED 2>/dev/null) || true)
    BAR_EMPTY=$(printf "%0.s░" $(seq 1 $EMPTY 2>/dev/null) || true)

    FNAME=$(basename "$infile")
    # Truncate if long
    DISP_FNAME="$FNAME"
    if [[ ${#DISP_FNAME} -gt 28 ]]; then
        DISP_FNAME="…${DISP_FNAME: -27}"
    fi

    # Print dynamic progress line (in-place)
    printf "\r${CLR} ${BLD}Converting A4L${RST} ${BR_CYN}%s${RST}${DIM}%s${RST} ${BR_GRN}%3d%%${RST} (${BLD}%d/%d${RST}) ${DIM}[%s < %s]${RST} ${YEL}[CONVERTING]${RST} %s" \
        "$BAR_FILL" "$BAR_EMPTY" "$PERCENT" "$CURRENT" "$TOTAL" "$ELAPSED_FMT" "$ETA_FMT" "$DISP_FNAME"

    # Setup paths
    relpath="${infile#$INPUT_ROOT/}"
    dirpath="$(dirname "$relpath")"
    name="${FNAME%.pdf}"
    
    if [[ "$dirpath" == "." ]]; then
        outdir="$OUTPUT_ROOT"
    else
        outdir="$OUTPUT_ROOT/$dirpath"
    fi
    mkdir -p "$outdir"
    outfile="$outdir/${name}_A4L.pdf"

    # Execute Ghostscript
    if gs -dNOPAUSE -dBATCH -dQUIET -sDEVICE=pdfwrite \
          -dPDFFitPage \
          -dFIXEDMEDIA \
          -dDEVICEWIDTHPOINTS=842 \
          -dDEVICEHEIGHTPOINTS=595 \
          -dAutoRotatePages=/None \
          -sOutputFile="$outfile" \
          "$infile" 2>/dev/null; then
        (( SUCCEEDED++ )) || true
    else
        (( FAILED++ )) || true
    fi
done

TOTAL_ELAPSED=$(( $(date +%s) - START_TIME ))
TOTAL_FMT=$(format_time "$TOTAL_ELAPSED")
FULL_BAR=$(printf "%0.s█" $(seq 1 $BAR_WIDTH))

printf "\r${CLR} ${BLD}Converting A4L${RST} ${BR_GRN}%s${RST} ${BR_GRN}100%%${RST} (${TOTAL}/${TOTAL}) ${GRN}✔ Complete${RST} in %s\n" \
    "$FULL_BAR" "$TOTAL_FMT"

echo ""
echo "${BLD}┌─ Conversion Summary ──────────────────────────────────────────────────┐${RST}"
echo "${BLD}│${RST}  Total Files          : ${BR_CYN}$TOTAL${RST}"
echo "${BLD}│${RST}  Successfully Converted: ${BR_GRN}$SUCCEEDED${RST}"
echo "${BLD}│${RST}  Failed               : $([[ $FAILED -eq 0 ]] && echo "${GRN}0${RST}" || echo "${RED}$FAILED${RST}")"
echo "${BLD}│${RST}  Elapsed Time         : ${YEL}${TOTAL_ELAPSED}s${RST}"
echo "${BLD}│${RST}  Output Directory     : ${DIM}$OUTPUT_ROOT${RST}"
echo "${BLD}└───────────────────────────────────────────────────────────────────────┘${RST}"
echo ""
