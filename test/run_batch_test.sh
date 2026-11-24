#!/bin/bash

set -euo pipefail

if [ "$(hostname)" != "binarysunset" ]; then
    echo "Error: this script must be run on Binary Sunset (current host: $(hostname))" >&2
    exit 1
fi

rm -f denoise.log

denoise "./job-orders/JobOrder.s1a-batch.xml"
denoise "./job-orders/JobOrder.s1b-batch.xml"
denoise "./job-orders/JobOrder.s1c-batch.xml"
denoise "./job-orders/JobOrder.s1d-batch.xml"

INPUT="denoise.log"
OUTPUT="errors-batch.csv"

echo "folder,filename,mae,max" > "$OUTPUT"

last_folder=""
last_file=""
mae=""
max=""

while IFS= read -r line; do
    if [[ "$line" =~ Written\ output\ to\ .*/([^/]+)/([^/]+\.xml) ]]; then
        last_folder="${BASH_REMATCH[1]}"
        last_file="${BASH_REMATCH[2]}"
        mae=""
        max=""
    elif [[ "$line" =~ [Mm]ean\ absolute\ error\ on\ denoising:\ ([0-9eE+\.\-]+)\ dB ]]; then
        mae="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ Maximum\ absolute\ error\ on\ denoising:\ ([0-9eE+\.\-]+)\ dB ]]; then
        max="${BASH_REMATCH[1]}"
        if [[ -n "$last_folder" && -n "$last_file" ]]; then
            echo "${last_folder},${last_file},${mae},${max}" >> "$OUTPUT"
            last_folder=""
            last_file=""
        fi
    fi
done < "$INPUT"

echo "Done. See $OUTPUT; generate the figures with scripts/plot_validation.py."
