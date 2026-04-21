#!/usr/bin/env bash

INPUT_ROOT="."
OUTPUT_ROOT="./output"

find "$INPUT_ROOT" -type d -name output -prune -o -type f -name "*.pdf" -print | while read -r infile; do
    # Remove leading ./ for cleaner paths
    relpath="${infile#./}"

    # Get directory + filename
    dirpath="$(dirname "$relpath")"
    filename="$(basename "$relpath")"
    name="${filename%.pdf}"

    # Build output directory path
    outdir="$OUTPUT_ROOT/$dirpath"

    # Create directory if not exists
    mkdir -p "$outdir"

    # Output file name
    outfile="$outdir/${name}_A4L.pdf"

    echo "Processing: $infile → $outfile"

    gs -dNOPAUSE -dBATCH -sDEVICE=pdfwrite \
       -dPDFFitPage \
       -dFIXEDMEDIA \
       -dDEVICEWIDTHPOINTS=842 \
       -dDEVICEHEIGHTPOINTS=595 \
       -dAutoRotatePages=/None \
       -sOutputFile="$outfile" \
       "$infile"

done
