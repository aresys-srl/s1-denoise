# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
General-purpose utility module for the denoising pipeline.

Provides helpers for ISO timestamp conversion, swath range boundary lookup,
slant-to-ground and ground-to-slant range coordinate conversion, Z-score-based
outlier removal for RFI detection in noise power series, and list normalisation.
"""

import numpy as np
from scipy.constants import speed_of_light

from arepytools.timing.precisedatetime import PreciseDateTime

from denoise.__init__ import RFI_DETECTION_CONSTANT

def iso_to_datetime(iso_str: str) -> PreciseDateTime:
    """Convert ISO timestamp string to PreciseDateTime object."""
    return PreciseDateTime.fromisoformat(iso_str)

def get_range_boundaries(swath_merges, subswath, az_time: str | PreciseDateTime):

    if isinstance(az_time, str):
        az_time = PreciseDateTime.from_utc_string(az_time)
    elif not isinstance(az_time, PreciseDateTime):
        raise TypeError("az_time must be str or PreciseDateTime")

    for swath_merge in swath_merges:

        if swath_merge["swath"] == subswath:
            
            swath_bounds = ensure_list(swath_merge["swathBoundsList"]["swathBounds"])
            
            closest_swath_merge = max(
                (k for k in swath_bounds
                if PreciseDateTime.fromisoformat(k["azimuthTime"]) <= az_time),
                key=lambda k: PreciseDateTime.fromisoformat(k["azimuthTime"]),
                default=swath_bounds[0]
            )

            first_range_sample = int(closest_swath_merge["firstRangeSample"])
            last_range_sample = int(closest_swath_merge["lastRangeSample"]) 
    
    return first_range_sample, last_range_sample

def ground2slant(start_az_time, gr_array, coordinate_conversions):
    """
    Convert ground-range data to slant-range.
    """
    gr_array = np.asarray(gr_array, dtype=float)

    # select closest GRSR polynomial in azimuth
    cc = min(
        coordinate_conversions,
        key=lambda cc: abs(iso_to_datetime(cc["azimuthTime"]) - iso_to_datetime(start_az_time))
    )

    sr0 = float(cc["sr0"])
    gr0 = float(cc["gr0"])
    grsr_coeffs = [float(c) for c in cc["grsrCoefficients"]['#text'].split(' ')]

    delta_gr = gr_array - gr0
    sr_array = np.polyval(grsr_coeffs[::-1], delta_gr)  # np.polyval usa coeff in ordine decrescente

    return sr_array

def slant2ground(start_az_time, sim_noise_sr, inc_angle_sr, coordinate_conversions, range_sampling_rate):
    """
    Convert slant-range data to ground-range while keeping output size same as input.
    """
    sim_noise_sr = np.asarray(sim_noise_sr, dtype=float)
    inc_angle_sr = np.asarray(inc_angle_sr, dtype=float)

    dr = speed_of_light / (2 * range_sampling_rate) 

    # select closest SRGR polynomial in azimuth
    cc = min(
        coordinate_conversions,
        key=lambda cc: abs(iso_to_datetime(cc["azimuthTime"]) - iso_to_datetime(start_az_time))
    )

    sr0 = cc["sr0"]
    gr0 = cc["gr0"]
    sr_to_gr_polynomial = cc["srgrCoeffs"]

    # slant range axis (meters)
    pixels = np.arange(sim_noise_sr.size)
    sr = sr0 + pixels * dr

    # from SR to GR
    sr_norm = sr - sr0
    gr = gr0 + np.polyval(sr_to_gr_polynomial[::-1], sr_norm)

    # generate GR grid same size as input
    gr_grid = np.linspace(gr.min(), gr.max(), sim_noise_sr.size)

    # interpolate
    sim_noise_gr = np.interp(gr_grid, gr, sim_noise_sr, left=np.nan, right=np.nan)
    inc_angle_gr = np.interp(gr_grid, gr, inc_angle_sr, left=np.nan, right=np.nan)

    return sim_noise_gr, inc_angle_gr

def remove_outliers(x, y, alpha=RFI_DETECTION_CONSTANT):
    """
    Removes outliers in y using Z-score (sigma-clipping), and removes corresponding x values.

    Parameters:
        x : array-like, same length as y
        y : array-like
        alpha : detection threshold in standard deviations (Z-score).
                Lower alpha → more aggressive detection → more points removed.
                Higher alpha → more conservative detection → fewer points removed.

                Typical values:
                    2.5  → aggressive, many outliers removed
                    3.0  → standard
                    3.5  → conservative

    Returns:
        x_clean, y_clean : arrays with outliers removed
        outlier_indices : indices of y that were removed, with [index, x_val, y_val]
    """

    x = np.array(x)
    y = np.array(y, dtype=float)
    outlier_indices = []

    if len(y) < 3:
        # Too few points to compute meaningful statistics
        return x, y, outlier_indices

    mean = np.mean(y)
    std = np.std(y, ddof=1)
    if std == 0:
        # No variability, nothing to remove
        return x, y, outlier_indices

    # Compute Z-score
    z_scores = (y - mean) / std

    # Detect outliers
    mask = np.abs(z_scores) > alpha
    indices = np.where(mask)[0]

    # Collect outlier info
    for idx in indices:
        outlier_indices.append([idx, x[idx], y[idx]])

    # Remove outliers
    x_clean = x[~mask]
    y_clean = y[~mask]

    # Sort outlier indices by original index
    outlier_indices_sorted = sorted(outlier_indices, key=lambda u: u[0])

    return x_clean, y_clean, outlier_indices_sorted

def ensure_list(x) -> list:
    """
    Ensure that the input value is returned as a list.

    If `x` is already a list, it is returned unchanged. Otherwise, the value
    is wrapped in a new single-element list.
    """

    return x if isinstance(x, list) else [x]