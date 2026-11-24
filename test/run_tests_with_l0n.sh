#!/bin/bash
set -euo pipefail

rm -f denoise.log

# S1A
denoise job-orders/JobOrder.s1a-ew-grd-l0n.xml
denoise job-orders/JobOrder.s1a-ew-grd.xml
denoise job-orders/JobOrder.s1a-ew-slc-l0n.xml
denoise job-orders/JobOrder.s1a-ew-slc.xml
denoise job-orders/JobOrder.s1a-iw-grd-l0n.xml
denoise job-orders/JobOrder.s1a-iw-grd.xml
denoise job-orders/JobOrder.s1a-iw-slc-l0n.xml
denoise job-orders/JobOrder.s1a-iw-slc.xml

# S1B
# JobOrder.s1b.xml

# S1C
denoise job-orders/JobOrder.s1c-ew-grd-l0n.xml
denoise job-orders/JobOrder.s1c-ew-grd.xml
denoise job-orders/JobOrder.s1c-ew-slc-l0n.xml
denoise job-orders/JobOrder.s1c-ew-slc.xml
denoise job-orders/JobOrder.s1c-iw-grd-l0n.xml
denoise job-orders/JobOrder.s1c-iw-grd.xml
denoise job-orders/JobOrder.s1c-iw-slc-l0n.xml
denoise job-orders/JobOrder.s1c-iw-slc.xml
denoise job-orders/JobOrder.s1c-sm-grd-l0n.xml
denoise job-orders/JobOrder.s1c-sm-grd.xml
denoise job-orders/JobOrder.s1c-sm-slc-l0n.xml
denoise job-orders/JobOrder.s1c-sm-slc.xml
denoise job-orders/JobOrder.s1c-wv-slc-l0n.xml
denoise job-orders/JobOrder.s1c-wv-slc.xml

# S1D
denoise job-orders/JobOrder.s1d-ew-grd-l0n.xml
denoise job-orders/JobOrder.s1d-ew-grd.xml
denoise job-orders/JobOrder.s1d-ew-slc-l0n.xml
denoise job-orders/JobOrder.s1d-ew-slc.xml
denoise job-orders/JobOrder.s1d-iw-grd-l0n.xml
denoise job-orders/JobOrder.s1d-iw-grd.xml
denoise job-orders/JobOrder.s1d-iw-slc-l0n.xml
denoise job-orders/JobOrder.s1d-iw-slc.xml

INPUT="denoise.log"
OUTPUT="errors-with-l0n.csv"

# Runs with and without L0N share the same output folders, so each run is told
# apart by its PID: only runs fed with an L0N product log L0N decoding messages.
python3 - "$INPUT" "$OUTPUT" <<'EOF'
import re
import sys

import pandas as pd

log_path, csv_path = sys.argv[1:3]

pid_re = re.compile(r" (\d+\.\d+\.\d+\S*) \[(\d+)\] \[[A-Z]\] (.*)$")
out_re = re.compile(r"Written output to .*/(([^/_]+)_([^/_]+)_([A-Z]{3})\w_1\w\w([HV])_(\d{4})(\d\d)(\d\d)T[^/]*)/([^/]+\.xml)")
file_re = re.compile(r"denoise-[^-]+-([^-]+)-[^-]+-([hv]{2})-")
mae_re = re.compile(r"[Mm]ean absolute error on denoising: ([0-9eE+.\-]+) dB")
max_re = re.compile(r"Maximum absolute error on denoising: ([0-9eE+.\-]+) dB")

l0n_pids = set()
rows = []
current = {}
with open(log_path) as f:
    for line in f:
        m = pid_re.search(line)
        if not m:
            continue
        version, pid, msg = m.groups()
        if "L0N file" in msg or "Decoding raw packets" in msg:
            l0n_pids.add(pid)
        elif m := out_re.search(msg):
            product, mission, mode, prod_type, pol, year, month, day, filename = m.groups()
            subswath, channel = file_re.match(filename).groups()
            # stripmap products are named after the beam (S1..S6): grouped as SM, with the beam as subswath
            if re.fullmatch(r"S[1-6]", mode):
                mode, subswath = "SM", subswath.upper()
            elif subswath == mode.lower():
                subswath = "merged"
            else:
                subswath = subswath.upper()
            current[pid] = {
                "Product": product,
                "Date of acquisition": f"{year}/{month}/{day}",
                "Mission": mission,
                "Mode": mode,
                "Type": prod_type,
                "Dual polarization": channel.upper(),
                "Polarization": pol,
                "Subswath": subswath,
                "with L0N?": "yes" if pid in l0n_pids else "no",
                "Version": version,
            }
        elif (m := mae_re.search(msg)) and pid in current:
            current[pid]["MAE [dB]"] = float(m.group(1))
        elif (m := max_re.search(msg)) and pid in current:
            row = current.pop(pid)
            row["Max error [dB]"] = float(m.group(1))
            rows.append(row)

COLUMNS = ["Product", "Date of acquisition", "Mission", "Mode", "Type", "Dual polarization",
           "Polarization", "Subswath", "with L0N?", "MAE [dB]", "Max error [dB]", "Version"]
df = pd.DataFrame(rows, columns=COLUMNS)
df.to_csv(csv_path, index=False, float_format="%.2E")

# Mean of the per-file MAE for each mission/mode/type, with and without L0N
summary = df.groupby(["Mission", "Mode", "Type", "with L0N?"])["MAE [dB]"].mean().unstack("with L0N?")
print(summary.to_string(float_format="%.2e"))
EOF

echo "Done. See $OUTPUT; generate the figures with scripts/plot_validation.py."
