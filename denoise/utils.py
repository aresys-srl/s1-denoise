# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
General-purpose utility module for the denoising pipeline.

Provides helpers for ISO timestamp conversion, swath range boundary lookup,
slant-to-ground and ground-to-slant range coordinate conversion, interval-based
outlier removal for noise pulse outlier detection in noise power series, and list normalisation.
"""

import numpy as np

from perseo_core.timing import PreciseDateTime

from denoise.__init__ import NOISE_LOWER_BOUND, NOISE_UPPER_BOUND


def linear_to_db(value, is_amplitude=False):
    """Convert value(s) from linear scale to dB (20*log10 if is_amplitude, else 10*log10). Accepts scalars, lists or arrays."""

    factor = 20 if is_amplitude else 10
    db = factor * np.log10(np.asarray(value, dtype=float))

    return float(db) if db.ndim == 0 else db

def db_to_linear(value, is_amplitude=False):
    """Convert value(s) from dB to linear scale (10**(x/20) if is_amplitude, else 10**(x/10)). Accepts scalars, lists or arrays."""

    factor = 20 if is_amplitude else 10
    linear = 10 ** (np.asarray(value, dtype=float) / factor)
    
    return float(linear) if linear.ndim == 0 else linear

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

def slant2ground(start_az_time, sim_noise_sr, inc_angle_sr, coordinate_conversions, sr_distances):
    """
    Convert slant-range data to ground-range using actual SR distances (in metres).
    sr_distances must match the sample axis of sim_noise_sr and inc_angle_sr.
    """
    sim_noise_sr = np.asarray(sim_noise_sr, dtype=float)
    inc_angle_sr = np.asarray(inc_angle_sr, dtype=float)
    sr_distances = np.asarray(sr_distances, dtype=float)

    # select closest SRGR polynomial in azimuth
    cc = min(
        coordinate_conversions,
        key=lambda cc: abs(iso_to_datetime(cc["azimuthTime"]) - iso_to_datetime(start_az_time))
    )

    sr0 = float(cc["sr0"])
    gr0 = float(cc["gr0"])
    sr_to_gr_polynomial = cc["srgrCoeffs"]

    # from SR distances to GR distances (metres)
    sr_norm = sr_distances - sr0
    gr = gr0 + np.polyval(sr_to_gr_polynomial[::-1], sr_norm)

    # resample onto a uniform GR grid of same length
    gr_grid = np.linspace(gr.min(), gr.max(), sim_noise_sr.size)

    sim_noise_gr = np.interp(gr_grid, gr, sim_noise_sr, left=np.nan, right=np.nan)
    inc_angle_gr = np.interp(gr_grid, gr, inc_angle_sr, left=np.nan, right=np.nan)

    return sim_noise_gr, inc_angle_gr

def remove_outliers(azimuth_times, noise_pulses, expected_noise_value):
    """
    Removes noise pulses whose value falls outside a given interval, and removes corresponding azimuth times.

    Parameters:
        azimuth_times : array-like, same length as noise_pulses
        noise_pulses : array-like
        expected_noise_value : float, expected noise power (linear scale) from AUX_INS.
            Pulses outside [expected - NOISE_LOWER_BOUND dB, expected + NOISE_UPPER_BOUND dB] are removed.

    Returns:
        azimuth_times_clean, noise_pulses_clean : arrays with outliers removed
        outlier_indices : removed points, as [index, azimuth_time, noise_pulse], sorted by index
    """

    azimuth_times = np.array(azimuth_times)
    noise_pulses = np.array(noise_pulses, dtype=float)
    min_value, max_value = expected_noise_value * db_to_linear(-NOISE_LOWER_BOUND), expected_noise_value * db_to_linear(NOISE_UPPER_BOUND)

    # Detect outliers
    mask = (noise_pulses < min_value) | (noise_pulses > max_value)
    indices = np.where(mask)[0]

    # Collect outlier info (already sorted by original index)
    outlier_indices = [[idx, azimuth_times[idx], noise_pulses[idx]] for idx in indices]

    # Remove outliers
    azimuth_times_clean = azimuth_times[~mask]
    noise_pulses_clean = noise_pulses[~mask]

    return azimuth_times_clean, noise_pulses_clean, outlier_indices

def ensure_list(x) -> list:
    """
    Ensure that the input value is returned as a list.

    If `x` is already a list, it is returned unchanged. Otherwise, the value
    is wrapped in a new single-element list.
    """

    return x if isinstance(x, list) else [x]