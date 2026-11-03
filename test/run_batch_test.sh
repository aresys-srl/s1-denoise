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
    elif [[ "$line" =~ Mean\ absolute\ error\ on\ denoising:\ ([0-9eE+\.\-]+)\ dB ]]; then
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

python3 - "$OUTPUT" "$(dirname "$0")/fonts" <<'PYEOF'
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

csv_path, font_dir = sys.argv[1:3]

for f in Path(font_dir).glob("*.ttf"):
    fm.fontManager.addfont(str(f))
plt.rcParams.update({"font.family": "IBM Plex Sans", "font.size": 12})

missions = ["S1A", "S1C", "S1D"]
modes = [("EW", "#2a78d6"), ("IW", "#eb6834")]
ink, grid = "#111111", "#e4e3de"

# Rows without a MAE are skipped in both plots
errors = defaultdict(lambda: defaultdict(list))
with open(csv_path) as fh:
    for row in csv.DictReader(fh):
        if row["mae"]:
            key = row["folder"][:3], row["folder"][4:6]
            errors["mae"][key].append(float(row["mae"]))
            errors["max"][key].append(float(row["max"]))


def plot(column, ylabel, title, out):
    data = {k: np.array(v) for k, v in errors[column].items()}
    groups = [data[m, mode] for m in missions for mode, _ in modes if (m, mode) in data]
    y_min = min(np.percentile(v, 5) for v in groups) / 6
    y_max = max(np.percentile(v, 95) for v in groups) * 3

    fig, ax = plt.subplots(figsize=(10, 6))
    for i, m in enumerate(missions):
        for j, (mode, color) in enumerate(modes):
            if (m, mode) not in data:
                continue
            v = data[m, mode]
            x = i + (j - 0.5) * 0.42
            ax.boxplot(v, positions=[x], widths=0.3, whis=(5, 95), showfliers=False,
                       patch_artist=True, zorder=3,
                       boxprops=dict(facecolor=color + "40", edgecolor=color, linewidth=1.8),
                       whiskerprops=dict(color=color, linewidth=1.5),
                       capprops=dict(color=color, linewidth=1.5),
                       medianprops=dict(color=ink, linewidth=2.2))
            ax.scatter(x, v.mean(), marker="D", s=40, color="white", edgecolor=ink,
                       linewidth=1.4, zorder=4)
            ax.text(x, np.percentile(v, 5) / 1.25, f"median\n{np.median(v):.1e}",
                    ha="center", va="top", fontsize=10, color=ink, linespacing=1.3)
            ax.text(x, -0.02, f"n = {v.size}", ha="center", va="top", fontsize=10,
                    color=ink, transform=ax.get_xaxis_transform())

    ax.set_yscale("log")
    ax.set_ylim(y_min, y_max)
    ax.yaxis.set_major_locator(mticker.LogLocator(numticks=10))
    ax.yaxis.set_minor_locator(mticker.NullLocator())
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0e}"))
    ax.set_xticks(range(len(missions)), missions, fontsize=14, color=ink)
    ax.set_xlim(-0.6, len(missions) - 0.4)
    ax.set_ylabel(ylabel, color=ink)
    ax.grid(axis="y", color=grid, zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#bdbcb5")
    ax.tick_params(axis="both", length=0, colors=ink)
    ax.tick_params(axis="x", pad=26)

    handles = [plt.Line2D([], [], marker="s", ls="", ms=11, mfc=c + "40", mec=c, mew=1.8,
                          label=m) for m, c in modes]
    handles += [plt.Line2D([], [], color=ink, lw=2.2, label="Median"),
                plt.Line2D([], [], marker="D", ls="", ms=8, mfc="white", mec=ink, mew=1.4,
                           label="Mean")]
    ax.legend(handles=handles, frameon=False, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0),
              fontsize=11, labelcolor=ink, handlelength=1.2, handletextpad=0.6,
              columnspacing=2.0)
    ax.set_title(title, loc="left", fontsize=16, fontweight=600, color=ink, pad=38)
    fig.text(0.01, 0.01, "Box: 25th–75th percentile · whiskers: 5th–95th percentile",
             fontsize=9.5, color=ink)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out, dpi=150)


plot("mae", "MAE [dB] (log scale)", "Noise MAE by mission and acquisition mode",
     "mae_by_mode.png")
plot("max", "Max error [dB] (log scale)", "Noise max error by mission and acquisition mode",
     "max_by_mode.png")
PYEOF

echo "Done. See $OUTPUT, mae_by_mode.png and max_by_mode.png."
