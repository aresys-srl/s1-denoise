# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Compute the error between nominal and reconstructed Sentinel-1 noise vectors.

Provides utilities to read noise XML files, interpolate the noise range vectors onto a common range/azimuth grid, and
compute the mean and maximum absolute differences in dB between the two, after normalisation by the digital number
scaling factor of the product.
"""

import numpy as np
import xml.etree.ElementTree as ET
import numpy as np
from scipy.interpolate import interp1d, RegularGridInterpolator

from perseo_core.timing import PreciseDateTime

from s1_denoise.utils import linear_to_db

DN_VALUES = {
    ("IW", "SLC"): 237,
    ("IW", "GRD"): 474,
    ("EW", "SLC"): 543,
    ("EW", "GRD"): 1087
}

def read_noise_xml(path):
    """
    Read the noise range vectors from a Sentinel-1 noise XML file (GRD or SLC).

    An XML namespace, if present, is handled.

    Parameters
    ----------
    path : str or pathlib.Path
        Path of the noise XML file.

    Returns
    -------
    times : numpy.ndarray
        Azimuth times of the noise range vectors, as PreciseDateTime objects.
    pixels_list : list of numpy.ndarray
        Range pixel indices of each vector.
    noise_list : list of numpy.ndarray
        Noise range LUT values of each vector (linear scale).
    """

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
            """Find a child tag of the current noise range vector, honouring the XML namespace."""
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
    """
    Linearly interpolate each noise range vector onto a common range pixel axis.

    Parameters
    ----------
    pixels_list : list of numpy.ndarray
        Range pixel indices of each vector.
    noise_list : list of numpy.ndarray
        Noise values of each vector.
    common_pixels : numpy.ndarray
        Common range pixel axis.

    Returns
    -------
    numpy.ndarray
        Interpolated noise, shape ``(n_vectors, len(common_pixels))``; NaN outside each vector's pixel span.
    """

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
    """
    Compute a common range pixel and azimuth time grid for two sets of noise range vectors.

    Both axes have 1500 points spanning the overlap of the two sets. Azimuth times of both sets are expressed in seconds
    relative to the same reference, the first vector of the first set, so that the two sets are aligned in time.

    Parameters
    ----------
    times1, times2 : numpy.ndarray
        Azimuth times of the two sets, as PreciseDateTime objects.
    pixels_list1, pixels_list2 : list of numpy.ndarray
        Range pixel indices of each vector of the two sets.

    Returns
    -------
    common_pixels : numpy.ndarray
        Common range pixel axis.
    times1_sec : numpy.ndarray
        Azimuth times of the first set [s], relative to the first vector of the first set.
    times2_sec : numpy.ndarray
        Azimuth times of the second set [s], relative to the first vector of the first set.
    common_times_sec : numpy.ndarray
        Common azimuth time axis [s].
    """

    # Common pixel axis
    min_pixel = max(min(p[0] for p in pixels_list1),
                    min(p[0] for p in pixels_list2))
    max_pixel = min(max(p[-1] for p in pixels_list1),
                    max(p[-1] for p in pixels_list2))

    common_pixels = np.linspace(min_pixel, max_pixel, 1500)

    # Convert azimuth time to seconds, using a common reference time for both sets
    t_ref = times1[0]
    times1_sec = np.array([float(t - t_ref) for t in times1])
    times2_sec = np.array([float(t - t_ref) for t in times2])

    common_times_sec = np.linspace(
        max(times1_sec[0], times2_sec[0]),
        min(times1_sec[-1], times2_sec[-1]),
        1500,
    )

    return common_pixels, times1_sec, times2_sec, common_times_sec


def compare_noise(noise_file, denoise_file, dn):
    """
    Compare nominal and reconstructed noise vectors on a common grid.

    Both files are interpolated onto the grid of `build_common_grid` and normalised by ``dn**2``; zero values are set to
    NaN and values are clipped at 1e-12 before conversion to dB.

    Parameters
    ----------
    noise_file : str or pathlib.Path
        Nominal noise XML file.
    denoise_file : str or pathlib.Path
        Reconstructed noise XML file.
    dn : float
        Digital number scaling factor of the product (see `DN_VALUES`).

    Returns
    -------
    common_pixels : numpy.ndarray
        Common range pixel axis.
    common_times_sec : numpy.ndarray
        Common azimuth time axis [s].
    diff_db : numpy.ndarray
        Reconstructed minus nominal noise [dB], shape ``(len(common_times_sec), len(common_pixels))``.
    f1 : scipy.interpolate.RegularGridInterpolator
        Interpolator of the nominal noise over (azimuth time [s], range pixel), not normalised by `dn`.
    f2 : scipy.interpolate.RegularGridInterpolator
        Interpolator of the reconstructed noise over (azimuth time [s], range pixel), not normalised by `dn`.
    mae : float
        Mean absolute difference [dB], ignoring NaN.
    max_err : float
        Maximum absolute difference [dB], ignoring NaN.
    """

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

    diff_db = linear_to_db(np.maximum(np.abs(denoise), 1e-12)) - linear_to_db(np.maximum(np.abs(noise), 1e-12))

    mae = np.nanmean(np.abs(diff_db))
    max_err = np.nanmax(np.abs(diff_db))

    return common_pixels, common_times_sec, diff_db, f1, f2, mae, max_err


def calculate_error(nominal_noise_filepath, denoise_filepath):
    """
    Compute the mean and maximum absolute error between a nominal and a reconstructed noise XML file.

    The acquisition mode and product type, used to select the digital number in `DN_VALUES`, are derived from the name
    of the SAFE product containing the nominal noise file (three directory levels up).

    Parameters
    ----------
    nominal_noise_filepath : pathlib.Path
        Nominal noise XML file, inside a SAFE product.
    denoise_filepath : pathlib.Path
        Reconstructed noise XML file.

    Returns
    -------
    mean_error : float
        Mean absolute difference [dB].
    max_error : float
        Maximum absolute difference [dB].
    """

    product_name = nominal_noise_filepath.parent.parent.parent.stem
    mode = product_name[4:6]
    prod_type = product_name[7:10]
    dn = DN_VALUES.get((mode, prod_type))

    common_pixels, common_times_sec, diff_db, f1, f2, mean_error, max_error = compare_noise(nominal_noise_filepath, denoise_filepath, dn)

    return mean_error, max_error
