#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  merge_and_convert.sh                                                ║
# ║  Folder PDF Merger & A4 Landscape Converter with Dynamic Progress    ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── Terminal Styling ───────────────────────────────────────────────────
RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[0;33m'; CYN=$'\033[0;36m'
BLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'; CLR=$'\033[K'
BR_CYN=$'\033[1;36m'; BR_GRN=$'\033[1;32m'; BR_YEL=$'\033[1;33m'

INPUT_ROOT="${1:-.}"
INPUT_ROOT="$(cd "$INPUT_ROOT" && pwd)"
OUTPUT_ROOT="$INPUT_ROOT/output"

echo "${CYN}╭────────────────────────────────────────────────────────────────────────╮${RST}"
echo "${CYN}│${RST}  ${BLD}${BR_CYN}PDF Folder Merger & A4 Landscape Converter (Bash)${RST}                     ${CYN}│${RST}"
echo "${CYN}│${RST}  • Merges multi-PDF folders losslessly → Converts to A4L               ${CYN}│${RST}"
echo "${CYN}│${RST}  • Converts single-PDF folders directly to A4L                         ${CYN}│${RST}"
echo "${CYN}╰────────────────────────────────────────────────────────────────────────╯${RST}"

# Check tools
missing=0
for tool in gs; do
    if ! command -v "$tool" &>/dev/null; then
        echo "${RED}Error: Required tool not found: $tool${RST}" >&2
        missing=1
    fi
done
[[ $missing -eq 1 ]] && exit 1

HAS_QPDF=1
if ! command -v qpdf &>/dev/null; then
    HAS_QPDF=0
fi

mkdir -p "$OUTPUT_ROOT"

# Scan PDFs
mapfile -d '' ALL_PDFS < <(
    find "$INPUT_ROOT" \
        -path "$OUTPUT_ROOT" -prune \
        -o -type f -name "*.pdf" -print0 \
    | sort -z
)

TOTAL_FILES=${#ALL_PDFS[@]}
if [[ $TOTAL_FILES -eq 0 ]]; then
    echo "${YEL}No PDF files found under:${RST} $INPUT_ROOT"
    exit 0
fi

# Collect unique folders
declare -A SEEN_DIRS
FOLDER_LIST=()
for f in "${ALL_PDFS[@]}"; do
    dir="$(dirname "$f")"
    if [[ -z "${SEEN_DIRS[$dir]+_}" ]]; then
        SEEN_DIRS[$dir]=1
        FOLDER_LIST+=("$dir")
    fi
done

TOTAL_FOLDERS=${#FOLDER_LIST[@]}
echo " ${GRN}●${RST} Found ${BLD}$TOTAL_FILES${RST} PDF(s) across ${BLD}$TOTAL_FOLDERS${RST} folder(s). Processing…"$'\n'

# Cleanup trap
cleanup() {
    find "$OUTPUT_ROOT" -name "_tmp_merge_*.pdf" -delete 2>/dev/null || true
    find "$OUTPUT_ROOT" -name "_tmp_clean_*.pdf" -delete 2>/dev/null || true
}
trap cleanup EXIT

START_TIME=$(date +%s)
BAR_WIDTH=26
CURRENT_FOLDER=0
TOTAL_MERGED=0
TOTAL_CONVERTED=0
TOTAL_FAILED=0
TOTAL_SANITIZED=0

format_time() {
    local secs=$1
    local m=$((secs / 60))
    local s=$((secs % 60))
    printf "%02d:%02d" "$m" "$s"
}

sanitize_pdf() {
    local infile="$1"
    local outfile="$2"
    gs -dNOPAUSE -dBATCH -dQUIET -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 \
       -dPrinted=false -sOutputFile="$outfile" "$infile" 2>/dev/null
}

for folder in "${FOLDER_LIST[@]}"; do
    (( CURRENT_FOLDER++ )) || true

    NOW=$(date +%s)
    ELAPSED=$(( NOW - START_TIME ))
    [[ $ELAPSED -eq 0 ]] && ELAPSED=1

    RATE_X100=$(( CURRENT_FOLDER * 100 / ELAPSED ))
    REMAINING=$(( TOTAL_FOLDERS - CURRENT_FOLDER ))
    if [[ $RATE_X100 -gt 0 ]]; then
        ETA_SECS=$(( REMAINING * 100 / RATE_X100 ))
    else
        ETA_SECS=0
    fi
    ELAPSED_FMT=$(format_time "$ELAPSED")
    ETA_FMT=$(format_time "$ETA_SECS")

    PERCENT=$(( CURRENT_FOLDER * 100 / TOTAL_FOLDERS ))
    FILLED=$(( CURRENT_FOLDER * BAR_WIDTH / TOTAL_FOLDERS ))
    EMPTY=$(( BAR_WIDTH - FILLED ))

    BAR_FILL=$(printf "%0.s█" $(seq 1 $FILLED 2>/dev/null) || true)
    BAR_EMPTY=$(printf "%0.s░" $(seq 1 $EMPTY 2>/dev/null) || true)

    # Collect PDFs in this folder
    mapfile -d '' FOLDER_PDFS < <(
        find "$folder" -maxdepth 1 \
            -path "$OUTPUT_ROOT" -prune \
            -o -type f -name "*.pdf" -print0 \
        | sort -z
    )
    COUNT=${#FOLDER_PDFS[@]}
    [[ $COUNT -eq 0 ]] && continue

    reldir="${folder#$INPUT_ROOT}"
    reldir="${reldir#/}"
    if [[ -z "$reldir" ]]; then
        outdir="$OUTPUT_ROOT"
        disp_folder="root"
    else
        outdir="$OUTPUT_ROOT/$reldir"
        disp_folder="$(basename "$folder")"
    fi
    mkdir -p "$outdir"

    first_pdf="${FOLDER_PDFS[0]}"
    first_name="$(basename "$first_pdf" .pdf)"

    if [[ $COUNT -gt 1 ]]; then
        # Multi-PDF: Merge & Convert
        printf "\r${CLR} ${BLD}Batch Progress${RST} ${BR_CYN}%s${RST}${DIM}%s${RST} ${BR_GRN}%3d%%${RST} (${BLD}%d/%d${RST}) ${DIM}[%s < %s]${RST} ${BR_YEL}[MERGING %d PDFs]${RST} %s" \
            "$BAR_FILL" "$BAR_EMPTY" "$PERCENT" "$CURRENT_FOLDER" "$TOTAL_FOLDERS" "$ELAPSED_FMT" "$ETA_FMT" "$COUNT" "$disp_folder"

        TMP_MERGED="$outdir/_tmp_merge_${first_name}.pdf"
        OUTFILE="$outdir/${first_name}_merged_A4L.pdf"

        MERGE_OK=0
        if [[ $HAS_QPDF -eq 1 ]]; then
            QPDF_ARGS=(--empty --pages)
            for f in "${FOLDER_PDFS[@]}"; do
                QPDF_ARGS+=("$f" "1-z")
            done
            QPDF_ARGS+=(-- "$TMP_MERGED")

            if qpdf "${QPDF_ARGS[@]}" 2>/dev/null; then
                MERGE_OK=1
            else
                # Repair via Ghostscript
                SAN_PDFS=()
                ALL_SAN=1
                for f in "${FOLDER_PDFS[@]}"; do
                    tmp_clean="$outdir/_tmp_clean_$(basename "$f")"
                    if sanitize_pdf "$f" "$tmp_clean"; then
                        SAN_PDFS+=("$tmp_clean")
                        (( TOTAL_SANITIZED++ )) || true
                    else
                        ALL_SAN=0; break
                    fi
                done

                if [[ $ALL_SAN -eq 1 ]]; then
                    RETRY_ARGS=(--empty --pages)
                    for sf in "${SAN_PDFS[@]}"; do RETRY_ARGS+=("$sf" "1-z"); done
                    RETRY_ARGS+=(-- "$TMP_MERGED")
                    if qpdf "${RETRY_ARGS[@]}" 2>/dev/null; then
                        MERGE_OK=1
                    fi
                fi
                for sf in "${SAN_PDFS[@]}"; do rm -f "$sf"; done
            fi
        fi

        # Fallback to Ghostscript merge if qpdf failed or not installed
        if [[ $MERGE_OK -eq 0 ]]; then
            if gs -dNOPAUSE -dBATCH -dQUIET -sDEVICE=pdfwrite -sOutputFile="$TMP_MERGED" "${FOLDER_PDFS[@]}" 2>/dev/null; then
                MERGE_OK=1
            fi
        fi

        if [[ $MERGE_OK -eq 1 ]]; then
            (( TOTAL_MERGED++ )) || true
            # Convert merged to A4L
            printf "\r${CLR} ${BLD}Batch Progress${RST} ${BR_CYN}%s${RST}${DIM}%s${RST} ${BR_GRN}%3d%%${RST} (${BLD}%d/%d${RST}) ${DIM}[%s < %s]${RST} ${YEL}[CONVERTING A4L]${RST} %s" \
                "$BAR_FILL" "$BAR_EMPTY" "$PERCENT" "$CURRENT_FOLDER" "$TOTAL_FOLDERS" "$ELAPSED_FMT" "$ETA_FMT" "$disp_folder"

            if gs -dNOPAUSE -dBATCH -dQUIET -sDEVICE=pdfwrite \
                  -dPDFFitPage -dFIXEDMEDIA -dDEVICEWIDTHPOINTS=842 -dDEVICEHEIGHTPOINTS=595 \
                  -dAutoRotatePages=/None -sOutputFile="$OUTFILE" "$TMP_MERGED" 2>/dev/null; then
                (( TOTAL_CONVERTED++ )) || true
            else
                (( TOTAL_FAILED++ )) || true
            fi
            rm -f "$TMP_MERGED"
        else
            (( TOTAL_FAILED++ )) || true
            rm -f "$TMP_MERGED"
        fi

    else
        # Single PDF: Direct convert
        printf "\r${CLR} ${BLD}Batch Progress${RST} ${BR_CYN}%s${RST}${DIM}%s${RST} ${BR_GRN}%3d%%${RST} (${BLD}%d/%d${RST}) ${DIM}[%s < %s]${RST} ${YEL}[CONVERTING]${RST} %s" \
            "$BAR_FILL" "$BAR_EMPTY" "$PERCENT" "$CURRENT_FOLDER" "$TOTAL_FOLDERS" "$ELAPSED_FMT" "$ETA_FMT" "$disp_folder"

        OUTFILE="$outdir/${first_name}_A4L.pdf"
        if gs -dNOPAUSE -dBATCH -dQUIET -sDEVICE=pdfwrite \
              -dPDFFitPage -dFIXEDMEDIA -dDEVICEWIDTHPOINTS=842 -dDEVICEHEIGHTPOINTS=595 \
              -dAutoRotatePages=/None -sOutputFile="$OUTFILE" "$first_pdf" 2>/dev/null; then
            (( TOTAL_CONVERTED++ )) || true
        else
            (( TOTAL_FAILED++ )) || true
        fi
    fi
done

TOTAL_ELAPSED=$(( $(date +%s) - START_TIME ))
TOTAL_FMT=$(format_time "$TOTAL_ELAPSED")
FULL_BAR=$(printf "%0.s█" $(seq 1 $BAR_WIDTH))

printf "\r${CLR} ${BLD}Batch Progress${RST} ${BR_GRN}%s${RST} ${BR_GRN}100%%${RST} (${TOTAL_FOLDERS}/${TOTAL_FOLDERS}) ${GRN}✔ Complete${RST} in %s\n" \
    "$FULL_BAR" "$TOTAL_FMT"

echo ""
echo "${BLD}┌─ Process Summary ─────────────────────────────────────────────────────┐${RST}"
echo "${BLD}│${RST}  Total Folders Scanned  : ${BR_CYN}$TOTAL_FOLDERS${RST}"
echo "${BLD}│${RST}  Total Input PDFs       : ${BR_CYN}$TOTAL_FILES${RST}"
echo "${BLD}│${RST}  Folders Merged         : ${BR_GRN}$TOTAL_MERGED${RST}"
echo "${BLD}│${RST}  Files Converted (A4L)  : ${BR_GRN}$TOTAL_CONVERTED${RST}"
if [[ $TOTAL_SANITIZED -gt 0 ]]; then
    echo "${BLD}│${RST}  PDFs Auto-Repaired     : ${BR_YEL}$TOTAL_SANITIZED${RST}"
fi
echo "${BLD}│${RST}  Failed Operations      : $([[ $TOTAL_FAILED -eq 0 ]] && echo "${GRN}0${RST}" || echo "${RED}$TOTAL_FAILED${RST}")"
echo "${BLD}│${RST}  Total Elapsed Time     : ${YEL}${TOTAL_ELAPSED}s${RST}"
echo "${BLD}│${RST}  Output Directory       : ${DIM}$OUTPUT_ROOT${RST}"
echo "${BLD}└───────────────────────────────────────────────────────────────────────┘${RST}"
echo ""
