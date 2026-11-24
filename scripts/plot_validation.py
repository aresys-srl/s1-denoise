#!.venv/bin/python3

"""
Generate the validation figures of the technical note and of the documentation from the test results.

The figures are computed from the CSV files written by the test scripts, one row per denoise file:

- ``test/errors-with-l0n.csv`` (``test/run_tests_with_l0n.sh``, cross-type dataset): mean MAE per mission, mode and
  product type, with and without L0N products (``overall``, ``slc-errors``, ``grd-errors``);
- ``test/errors-batch.csv`` (``test/run_batch_test.sh``, one day analysis): distribution of the MAE and of the maximum
  error per mission and mode (``mae_by_mode``, ``max_by_mode``).

The figures are saved, with the same names, to each output directory (by default the image folders of the technical
note and of the documentation). Each figure is produced by a function registered in `FIGURES` together with the
dataset it reads, so that new figures can be added without changing the command line.
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CROSS_TYPE_CSV = REPO_DIR / "test" / "errors-with-l0n.csv"
DEFAULT_BATCH_CSV = REPO_DIR / "test" / "errors-batch.csv"
DEFAULT_OUTPUT_DIRS = [REPO_DIR / "technical_note" / "img", REPO_DIR / "docs" / "assets" / "images"]
FONT_DIR = REPO_DIR / "technical_note" / "fonts"
FONT_FAMILY = "IBM Plex Sans"

# display order of the groups
MISSIONS = ["S1A", "S1B", "S1C", "S1D"]
MODES = ["IW", "EW", "SM", "WV"]
TYPES = ["SLC", "GRD"]
BATCH_MISSIONS = ["S1A", "S1C", "S1D"]  # missions of the one day analysis

# noise power source: CSV value of "with L0N?" -> legend label and bar color
SOURCES = {
    "no": ("Noise power from L1 annotation", "#5B9BD5"),
    "yes": ("Noise power from L0N product", "#ED7D31"),
}

# box plots of the one day analysis: color of each acquisition mode
MODE_COLORS = {"IW": "#ED7D31", "EW": "#5B9BD5"}

# neutral inks
TEXT_PRIMARY = "#2b2b29"
TEXT_SECONDARY = "#6b6a65"
GRID = "#e1e0d9"
SEPARATOR = "#c9c8c0"

DPI = 200


def setup_font():
    """
    Register the IBM Plex Sans font files of the technical note and use them for all the figures.

    Raises
    ------
    FileNotFoundError
        If no IBM Plex Sans font file is found in `FONT_DIR`.
    """

    font_files = sorted(FONT_DIR.glob("IBMPlexSans-*.ttf"))
    if not font_files:
        raise FileNotFoundError(f"No {FONT_FAMILY} font files found in {FONT_DIR}")
    for font_file in font_files:
        font_manager.fontManager.addfont(str(font_file))
    plt.rcParams["font.family"] = FONT_FAMILY


def read_mae(csv_path):
    """
    Read the per-file results of the test CSV and average the MAE per group.

    Parameters
    ----------
    csv_path : pathlib.Path
        CSV written by ``test/run_tests_with_l0n.sh``.

    Returns
    -------
    dict
        Mean MAE [dB], keyed by ``(mission, mode, product_type, source)``, where `source` is ``"no"`` or ``"yes"``
        (noise power from the L1 annotation or from the L0N product).
    """

    values = defaultdict(list)
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["MAE [dB]"]:
                continue
            key = (row["Mission"], row["Mode"], row["Type"], row["with L0N?"])
            values[key].append(float(row["MAE [dB]"]))

    return {key: float(np.mean(v)) for key, v in values.items()}


def read_batch_errors(csv_path):
    """
    Read the per-file results of the one day analysis CSV.

    Parameters
    ----------
    csv_path : pathlib.Path
        CSV written by ``test/run_batch_test.sh``, with columns ``folder``, ``filename``, ``mae`` and ``max``.

    Returns
    -------
    dict
        ``{"mae": errors, "max": errors}``, where `errors` maps ``(mission, mode)`` to the array of the mean or maximum
        absolute errors [dB] of its denoise files. Rows without a MAE are skipped.
    """

    values = {"mae": defaultdict(list), "max": defaultdict(list)}
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["mae"]:
                continue
            key = (row["folder"][:3], row["folder"][4:6])
            values["mae"][key].append(float(row["mae"]))
            values["max"][key].append(float(row["max"]))

    return {column: {key: np.array(v) for key, v in errors.items()} for column, errors in values.items()}


def _style_axes(ax, ylabel="Mean absolute error [dB]", log=False):
    """
    Apply the common style to a chart: recessive hairline grid and axes, secondary ink for ticks, exponential labels
    on the y axis.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes to style.
    ylabel : str, optional
        Label of the y axis. Default is ``"Mean absolute error [dB]"``.
    log : bool, optional
        Whether the y axis is logarithmic (one tick per decade). Default is False.
    """

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(SEPARATOR)
    ax.tick_params(axis="y", length=0, colors=TEXT_SECONDARY, labelsize=9)
    ax.tick_params(axis="x", length=0, colors=TEXT_PRIMARY, labelsize=9)
    if log:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(mticker.LogLocator(numticks=12))
        ax.yaxis.set_minor_locator(mticker.NullLocator())
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0e}"))
    else:
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10], min_n_ticks=4))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1e}" if v else "0"))
    ax.set_ylabel(ylabel, color=TEXT_SECONDARY, fontsize=9)


def _runs(labels):
    """
    Split a sequence of hierarchical labels into runs of consecutive equal labels.

    Parameters
    ----------
    labels : list of tuple
        Label prefix of each group.

    Returns
    -------
    list of tuple
        ``(first, last, label)`` of each run: indices of its first and last group, and its label.
    """

    runs = []
    for i, label in enumerate(labels):
        if runs and runs[-1][2] == label:
            runs[-1] = (runs[-1][0], i, label)
        else:
            runs.append((i, i, label))

    return runs


def _draw_group_labels(ax, labels, tick_lines=1):
    """
    Draw hierarchical labels below the x axis: the innermost label of each group as tick label, each outer level once
    below the groups it spans (with a hairline bracket for the intermediate levels), and the outermost level in bold,
    with thin separators between its groups.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
        Axes with one group per integer x position.
    labels : list of tuple
        Labels of each group, from the outermost to the innermost level (e.g. ``("S1A", "IW", "SLC")``).
    tick_lines : int, optional
        Number of text lines of the tick labels, used to place the outer levels below them. Default is 1.
    """

    n = len(labels)
    levels = len(labels[0])
    ax.set_xticks(np.arange(n))
    ax.set_xticklabels([group[-1] for group in labels])
    ax.set_xlim(-0.6, n - 0.4)

    # outer levels below the tick labels, from the innermost outward
    transform = ax.get_xaxis_transform()
    row_height = 0.085
    top = -0.03 - 0.045 * (tick_lines - 1)
    for depth, level in enumerate(range(levels - 2, -1, -1), start=1):
        y = top - row_height * depth
        outermost = level == 0
        for first, last, label in _runs([group[: level + 1] for group in labels]):
            if not outermost:
                ax.plot([first - 0.42, last + 0.42], [y + 0.012] * 2, transform=transform, clip_on=False,
                        color=SEPARATOR, linewidth=0.8)
            ax.text((first + last) / 2, y - 0.005, label[-1], transform=transform, ha="center", va="top",
                    fontsize=10 if outermost else 9, fontweight="bold" if outermost else "normal",
                    color=TEXT_PRIMARY)
            if outermost and first > 0:
                ax.axvline(first - 0.5, color=SEPARATOR, linewidth=0.8)


def grouped_bar_chart(mae, groups, path):
    """
    Draw the MAE of each group as a pair of bars (noise power from the L1 annotation and from the L0N product), with
    hierarchical labels below the axis.

    The innermost label of each group is the tick label; each outer level is written once below the groups it spans
    (with a hairline bracket for the intermediate levels), and the outermost level (the mission) in bold, with thin
    separators between its groups.

    Parameters
    ----------
    mae : dict
        Mean MAE per group, as returned by `read_mae`.
    groups : list of tuple
        ``(labels, key)`` of each group, in display order, where `labels` are the labels of the group from the
        outermost to the innermost level (e.g. ``("S1A", "IW", "SLC")``) and `key` the
        ``(mission, mode, product_type)`` key of the group in `mae`.
    path : pathlib.Path
        Output image path.
    """

    n = len(groups)
    levels = len(groups[0][0])
    fig, ax = plt.subplots(figsize=(max(5.5, 0.62 * n + 1.5), 3.4 + 0.25 * (levels - 1)))

    x = np.arange(n)
    width = 0.36
    gap = 0.02
    for i, (source, (label, color)) in enumerate(SOURCES.items()):
        offset = (i - 0.5) * (width + gap)
        heights = [mae.get((*key, source), np.nan) for _, key in groups]
        ax.bar(x + offset, heights, width, color=color, label=label, linewidth=0)

    _style_axes(ax)
    _draw_group_labels(ax, [labels for labels, _ in groups])

    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(SOURCES), fontsize=9,
              handlelength=1.0, handleheight=1.0, labelcolor=TEXT_PRIMARY, borderaxespad=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def _groups(mae, product_types, with_type_level):
    """
    Return the groups present in the results, ordered by mission, mode and product type.

    Parameters
    ----------
    mae : dict
        Mean MAE per group, as returned by `read_mae`.
    product_types : list of str
        Product types to include.
    with_type_level : bool
        Whether the product type is a label level (below the mode), or the mode is the innermost level.

    Returns
    -------
    list of tuple
        ``(labels, key)`` of each group, as expected by `grouped_bar_chart`.
    """

    present = {key[:3] for key in mae}
    groups = []
    for mission in MISSIONS:
        for mode in MODES:
            for product_type in TYPES:
                if product_type in product_types and (mission, mode, product_type) in present:
                    labels = (mission, mode, product_type) if with_type_level else (mission, mode)
                    groups.append((labels, (mission, mode, product_type)))

    return groups


def figure_overall(mae, path):
    """MAE of all the cross-type test products, per mission, mode and product type."""
    grouped_bar_chart(mae, _groups(mae, TYPES, with_type_level=True), path)


def figure_slc_errors(mae, path):
    """MAE of the SLC cross-type test products, per mission and mode."""
    grouped_bar_chart(mae, _groups(mae, ["SLC"], with_type_level=False), path)


def figure_grd_errors(mae, path):
    """MAE of the GRD cross-type test products, per mission and mode."""
    grouped_bar_chart(mae, _groups(mae, ["GRD"], with_type_level=False), path)


def box_plot(errors, ylabel, path):
    """
    Draw the distribution of the errors of each mission and mode as a box plot on a logarithmic axis.

    The box spans the 25th-75th percentiles and the whiskers the 5th-95th percentiles; the median is drawn as a line
    and the mean as a diamond. Each box is colored by acquisition mode, and the number of denoise files of each group
    is written below its mode.

    Parameters
    ----------
    errors : dict
        Errors [dB] per ``(mission, mode)``, as one of the dictionaries returned by `read_batch_errors`.
    ylabel : str
        Label of the y axis.
    path : pathlib.Path
        Output image path.
    """

    keys = [(mission, mode) for mission in BATCH_MISSIONS for mode in MODES
            if (mission, mode) in errors and mode in MODE_COLORS]
    data = [errors[key] for key in keys]

    fig, ax = plt.subplots(figsize=(max(5.5, 0.9 * len(keys) + 1.5), 3.9))
    for position, ((_, mode), values) in enumerate(zip(keys, data)):
        color = MODE_COLORS[mode]
        ax.boxplot(values, positions=[position], widths=0.42, whis=(5, 95), showfliers=False, showmeans=True,
                   patch_artist=True, zorder=3,
                   boxprops=dict(facecolor=color + "59", edgecolor=color, linewidth=1.2),
                   whiskerprops=dict(color=color, linewidth=1.2),
                   capprops=dict(color=color, linewidth=1.2),
                   medianprops=dict(color=TEXT_PRIMARY, linewidth=1.6),
                   meanprops=dict(marker="D", markersize=5, markerfacecolor="white", markeredgecolor=TEXT_PRIMARY,
                                  markeredgewidth=1.0))

    _style_axes(ax, ylabel=ylabel, log=True)
    low = min(np.percentile(v, 5) for v in data)
    high = max(max(np.percentile(v, 95), v.mean()) for v in data)
    ax.set_ylim(low / 3, high * 3)
    _draw_group_labels(ax, [(mission, f"{mode}\nn = {errors[(mission, mode)].size}") for mission, mode in keys],
                       tick_lines=2)

    modes = [mode for mode in MODE_COLORS if any(key[1] == mode for key in keys)]
    handles = [plt.Line2D([], [], marker="s", linestyle="", markersize=9, markerfacecolor=MODE_COLORS[mode] + "59",
                          markeredgecolor=MODE_COLORS[mode], markeredgewidth=1.2, label=mode) for mode in modes]
    handles += [plt.Line2D([], [], color=TEXT_PRIMARY, linewidth=1.6, label="Median"),
               plt.Line2D([], [], marker="D", linestyle="", markersize=5, markerfacecolor="white",
                          markeredgecolor=TEXT_PRIMARY, label="Mean")]
    ax.legend(handles=handles, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(handles), fontsize=9,
              labelcolor=TEXT_PRIMARY, borderaxespad=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def figure_mae_by_mode(batch, path):
    """Distribution of the MAE of the one day analysis, per mission and mode."""
    box_plot(batch["mae"], "Mean absolute error [dB]", path)


def figure_max_by_mode(batch, path):
    """Distribution of the maximum error of the one day analysis, per mission and mode."""
    box_plot(batch["max"], "Maximum absolute error [dB]", path)


# dataset name -> CSV reader
READERS = {
    "cross-type": read_mae,
    "batch": read_batch_errors,
}

# figure name (output file name without extension) -> (dataset name, function(data, path))
FIGURES = {
    "overall": ("cross-type", figure_overall),
    "slc-errors": ("cross-type", figure_slc_errors),
    "grd-errors": ("cross-type", figure_grd_errors),
    "mae_by_mode": ("batch", figure_mae_by_mode),
    "max_by_mode": ("batch", figure_max_by_mode),
}


def main(argv=None):
    """
    Parse the command line and generate the requested figures.

    Parameters
    ----------
    argv : list of str, optional
        Command line arguments. Default is ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 on success, 1 if a CSV file needed by the requested figures does not exist.
    """

    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--cross-type-csv", type=Path, default=DEFAULT_CROSS_TYPE_CSV,
                        help=f"cross-type test results (default: {DEFAULT_CROSS_TYPE_CSV.relative_to(REPO_DIR)})")
    parser.add_argument("--batch-csv", type=Path, default=DEFAULT_BATCH_CSV,
                        help=f"one day analysis results (default: {DEFAULT_BATCH_CSV.relative_to(REPO_DIR)})")
    parser.add_argument("--output-dir", type=Path, action="append", dest="output_dirs",
                        help="output directory, can be repeated (default: technical_note/img and docs/assets/images)")
    parser.add_argument("--figures", nargs="+", choices=list(FIGURES), default=list(FIGURES),
                        help="figures to generate (default: all)")
    args = parser.parse_args(argv)

    csv_paths = {"cross-type": args.cross_type_csv, "batch": args.batch_csv}
    datasets = {FIGURES[name][0] for name in args.figures}
    missing = [csv_paths[dataset] for dataset in sorted(datasets) if not csv_paths[dataset].exists()]
    if missing:
        print(f"CSV file not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1

    setup_font()
    data = {dataset: READERS[dataset](csv_paths[dataset]) for dataset in datasets}
    for output_dir in args.output_dirs or DEFAULT_OUTPUT_DIRS:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in args.figures:
            dataset, figure = FIGURES[name]
            path = output_dir / f"{name}.png"
            figure(data[dataset], path)
            print(f"Saved {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
