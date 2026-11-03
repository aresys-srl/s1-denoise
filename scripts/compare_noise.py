#!.venv/bin/python3

import numpy as np
import argparse
import sys
import matplotlib.pyplot as plt
from pathlib import Path
from math import nan

from denoise.error_calculation import compare_noise, DN_VALUES
from denoise.utils import linear_to_db

OUTPUT_DIR = Path("compare-noise-plots")


def plot_2d(common_pixels, common_times_sec, diff_db, out_name, colorbar, pol, subswath, prod_type):

    plt.figure(figsize=(9, 7))
    if colorbar is not None:
        vmax = colorbar
    else:
        vmax = np.nanmax(np.abs(diff_db))  
    vmin = -vmax # symmetric range

    plt.pcolormesh(
        common_pixels,
        common_times_sec,
        diff_db,
        shading="auto",
        cmap="RdBu",
        vmax=vmax,
        vmin=vmin
    )
    plt.colorbar(label="Noise difference [dB]")
    plt.xlabel("Range pixel")
    plt.ylabel("Azimuth time from acquisition start [s]")
    if prod_type == "GRD":
        plt.title(f"Denoising results ({pol})", weight="bold", pad=25, fontsize=14)
    else:
        plt.title(f"Denoising results ({pol}, {subswath})", weight="bold", pad=25, fontsize=14)
    plt.gca().text(0.5, 1.02, f"{out_name}", transform=plt.gca().transAxes, ha="center", fontsize=10, fontfamily="monospace")

    plt.tight_layout()
    out_path = OUTPUT_DIR / "whole_raster.png"
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved plot to {out_path}")


def plot_cut_profiles(common_pixels, f_noise, f_denoise, cut_times_sec, product_name, dn, pol, subswath, prod_type):
    """
    Plots 1D profiles (noise vs denoise and difference) at specified azimuth times.
    """

    for t_sec in cut_times_sec:
        # Query points: shape (num_pixels, 2) = (time, pixel)
        points = np.column_stack([np.full_like(common_pixels, t_sec), common_pixels])

        # Evaluate interpolators
        noise_profile = f_noise(points) / (dn**2)
        denoise_profile = f_denoise(points) / (dn**2)

        # Avoid zero values for log10
        noise_profile[noise_profile == 0] = nan
        denoise_profile[denoise_profile == 0] = nan

        noise_db = linear_to_db(noise_profile)
        denoise_db = linear_to_db(denoise_profile)
        diff_db = denoise_db - noise_db

        # Plot
        fig, axs = plt.subplots(2, 1, figsize=(8, 9), sharex=True)
        fig.suptitle(f"{product_name}", fontfamily = "monospace", fontweight="bold")

        # Top panel: original vs denoised
        axs[0].plot(common_pixels, noise_db, label="noise", linestyle="--")
        axs[0].plot(common_pixels, denoise_db, label="denoise", alpha=0.7)
        axs[0].set_ylabel("Noise power [dB]")
        if prod_type == "SLC":
            axs[0].set_title(f"Profiles ({pol}, {subswath}) at azimuth ≈ {t_sec:.2f} s")
        else:
            axs[0].set_title(f"Profiles ({pol}) at azimuth ≈ {t_sec:.2f} s")
        axs[0].legend()
        axs[0].grid(True)

        # Bottom panel: difference (remove swath jumps)
        deriv = (diff_db[1:] - diff_db[:-1])
        deriv = np.insert(deriv, 0, 0.0)
        mask = abs(deriv) > 0.01

        # print(max(abs(deriv)))

        diff_db[mask] = np.nan

        axs[1].plot(common_pixels, diff_db, color="red")
        axs[1].set_xlabel("Range pixel")
        axs[1].set_ylabel("Noise difference [dB]")
        if prod_type == "SLC":
            axs[1].set_title("Difference")
        else:
            axs[1].set_title("Difference (filtering subswath jumps)")
        axs[1].grid(True)

        plt.tight_layout()
        out_path = OUTPUT_DIR / f"cut_profile_{int(t_sec)}s.png"
        plt.savefig(out_path, dpi=300)
        plt.close()
        print(f"Saved plot to {out_path}")


def main():
    """CLI entry point"""

    parser = argparse.ArgumentParser(
        description="Compare two Sentinel-1 noise XML files (GRD or SLC)"
    )

    parser.add_argument("noise_file", type=Path, help="Nominal noise XML")
    parser.add_argument("denoise_file", type=Path, help="Candidate denoise XML")
    parser.add_argument("--colorbar", type=float, default=None, help="Colorbar symmetric limit value for plotting")

    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    product_name = args.noise_file.parent.parent.parent.stem

    parts_noise = args.noise_file.stem.split("-")
    parts_denoise = args.denoise_file.stem.split("-")
    
    subswath = parts_noise[2].upper()
    prod_type = parts_noise[3].upper()
    pol = parts_noise[4].upper()

    subswath_d = parts_denoise[2].upper()
    prod_type_d = parts_denoise[3].upper()
    pol_d = parts_denoise[4].upper()

    if subswath != subswath_d:
        print(f"Incompatible subswaths: {subswath} for noise file, {subswath_d} for denoise file")
        sys.exit(128)
    elif prod_type != prod_type_d:
        print(f"Incompatible product type: {prod_type} for noise file, {prod_type_d} for denoise file")
        sys.exit(128)
    elif pol != pol_d:
        print(f"Incompatible polarization: {pol} for noise file, {pol_d} for denoise file")
        sys.exit(128)

    mode = product_name[4:6]
    dn = DN_VALUES.get((mode, prod_type))

    print(f"Mode: {mode}\nProduct type: {prod_type}")

    common_pixels, common_times_sec, diff_db, f1, f2, mean_error, max_error = compare_noise(args.noise_file, args.denoise_file, dn)
    print(f"Absolute mean error on denoising: {mean_error:.2e} dB")
    print(f"Maximum absolute error on denoising: {max_error:.2e} dB")

    plot_2d(common_pixels, common_times_sec, diff_db, product_name, args.colorbar, pol, subswath, prod_type)

    # azimuth cut times in seconds
    cut_times_sec = np.linspace(1, max(common_times_sec), 6, endpoint=False)[1:]  
    plot_cut_profiles(common_pixels, f1, f2, cut_times_sec, product_name, dn, pol, subswath, prod_type)


if __name__ == "__main__":
    main()