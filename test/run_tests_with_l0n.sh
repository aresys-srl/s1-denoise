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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

log_path, csv_path = sys.argv[1:3]

pid_re = re.compile(r" (\d+\.\d+\.\d+) \[(\d+)\] \[[A-Z]\] (.*)$")
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
            current[pid] = {
                "Product": product,
                "Date of acquisition": f"{year}/{month}/{day}",
                "Mission": mission,
                "Mode": mode,
                "Type": prod_type,
                "Dual polarization": channel.upper(),
                "Polarization": pol,
                "Subswath": "merged" if subswath == mode.lower() else subswath.upper(),
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

COLORS = {"no": "#2a78d6", "yes": "#eb6834"}


def bar_chart(data, title, path):
    fig, ax = plt.subplots(figsize=(max(6, 1.1 * len(data)), 4.5))
    x = range(len(data))
    width = 0.38
    for i, l0n in enumerate(["no", "yes"]):
        if l0n in data:
            ax.bar([k + (i - 0.5) * width for k in x], data[l0n], width * 0.95,
                   color=COLORS[l0n], label=l0n)
    ax.set_xticks(list(x))
    ax.set_xticklabels(["\n".join(k) if isinstance(k, tuple) else k for k in data.index])
    ax.set_ylabel("Mean absolute error [dB]")
    ax.set_title(title)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.2e"))
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(title="with L0N?", frameon=False, loc="center left", bbox_to_anchor=(1, 0.5))
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved {path}")


for product in ("GRD", "SLC"):
    if product in summary.index.get_level_values("Type"):
        data = summary.xs(product, level="Type")
        bar_chart(data, f"{product} - MAE on denoising", f"mae_{product.lower()}.png")

all_data = summary.copy()
all_data.index = [f"{product}\n{mode}\n{mission}" for mission, mode, product in summary.index]
bar_chart(all_data, "GRD vs SLC - MAE on denoising", "mae_all.png")
EOF

echo "Done. See $OUTPUT and mae_*.png."
