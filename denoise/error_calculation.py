# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for computing the mean absolute error (MAE) between nominal and denoised Sentinel-1
noise profiles.

Provides utilities to read noise XML files, interpolate range and azimuth vectors onto
a common grid, and calculate the mean absolute difference in dB between the two profiles,
normalised by the appropriate digital number scaling factor.
"""

import numpy as np
import xml.etree.ElementTree as ET
import numpy as np
from scipy.interpolate import interp1d, RegularGridInterpolator

from perseo_core.timing import PreciseDateTime

DN_VALUES = {
    ("IW", "SLC"): 237,
    ("IW", "GRD"): 474,
    ("EW", "SLC"): 543,
    ("EW", "GRD"): 1087
}

def read_noise_xml(path):
    """Read Sentinel-1 noise XML (GRD or SLC)"""

    tree = ET.parse(path)
    root = tree.getroot()

    # Detect namespace (if present)
    if "}" in root.tag:
        ns = {"ns": root.tag.split("}")[0].strip("{")}
        search_path = ".//ns:noiseRangeVector"
    else:
        ns = {}
        search_path = ".//noiseRangeVector"

    times = []
    pixels_list = []
    noise_list = []

    for vec in root.findall(search_path, ns):

        def find(tag):
            if ns:
                return vec.find(f"ns:{tag}", ns)
            return vec.find(tag)

        az_time = PreciseDateTime.fromisoformat(find("azimuthTime").text)
        pixels = np.array(list(map(float, find("pixel").text.split())))
        noise = np.array(list(map(float, find("noiseRangeLut").text.split())))

        times.append(az_time)
        pixels_list.append(pixels)
        noise_list.append(noise)

    return np.array(times), pixels_list, noise_list


def interpolate_noise(pixels_list, noise_list, common_pixels):
    """Interpolate along range"""

    interp_noises = []
    for pixels, noise in zip(pixels_list, noise_list):
        f = interp1d(
            pixels,
            noise,
            kind="linear",
            bounds_error=False,
            fill_value=np.nan,
        )
        interp_noises.append(f(common_pixels))
    return np.array(interp_noises)


def build_common_grid(times1, pixels_list1, times2, pixels_list2):
    """Compute common grid"""

    # Common pixel axis
    min_pixel = max(min(p[0] for p in pixels_list1),
                    min(p[0] for p in pixels_list2))
    max_pixel = min(max(p[-1] for p in pixels_list1),
                    max(p[-1] for p in pixels_list2))

    common_pixels = np.linspace(min_pixel, max_pixel, 1500)

    # Convert azimuth time to seconds
    times1_sec = np.array([(t - times1[0]) for t in times1])
    times2_sec = np.array([(t - times2[0]) for t in times2])

    common_times_sec = np.linspace(
        max(times1_sec[0], times2_sec[0]),
        min(times1_sec[-1], times2_sec[-1]),
        1500,
    )

    return common_pixels, times1_sec, times2_sec, common_times_sec


def compare_noise(noise_file, denoise_file, dn):
    """Main comparison logic"""

    times1, pixels_list1, noise_list = read_noise_xml(noise_file)
    times2, pixels_list2, denoise_list = read_noise_xml(denoise_file)

    common_pixels, times1_sec, times2_sec, common_times_sec = build_common_grid(times1, pixels_list1, times2, pixels_list2)

    interp_noise = interpolate_noise(pixels_list1, noise_list, common_pixels)
    interp_denoise = interpolate_noise(pixels_list2, denoise_list, common_pixels)

    f1 = RegularGridInterpolator(
        (times1_sec, common_pixels),
        interp_noise,
        bounds_error=False,
        fill_value=np.nan,
    )
    f2 = RegularGridInterpolator(
        (times2_sec, common_pixels),
        interp_denoise,
        bounds_error=False,
        fill_value=np.nan,
    )

    T, P = np.meshgrid(common_times_sec, common_pixels, indexing="ij")

    noise = f1(np.stack([T, P], axis=-1)) / (dn**2)
    denoise = f2(np.stack([T, P], axis=-1)) / (dn**2)

    noise[noise == 0] = np.nan
    denoise[denoise == 0] = np.nan

    diff_db = 10 * np.log10(np.maximum(np.abs(denoise), 1e-12)) - 10 * np.log10(np.maximum(np.abs(noise), 1e-12))

    mae = np.nanmean(np.abs(diff_db))
    max_err = np.nanmax(np.abs(diff_db))

    return common_pixels, common_times_sec, diff_db, f1, f2, mae, max_err


def calculate_error(nominal_noise_filepath, denoise_filepath):

    product_name = nominal_noise_filepath.parent.parent.parent.stem
    mode = product_name[4:6]
    prod_type = product_name[7:10]
    dn = DN_VALUES.get((mode, prod_type))

    common_pixels, common_times_sec, diff_db, f1, f2, mean_error, max_error = compare_noise(nominal_noise_filepath, denoise_filepath, dn)

    return mean_error, max_error
