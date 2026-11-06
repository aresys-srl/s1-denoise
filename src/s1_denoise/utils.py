# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
General-purpose utilities for the denoising pipeline.

Provides helpers for linear/dB and ISO timestamp conversions, swath range boundary lookup, slant-to-ground and
ground-to-slant range conversions, outlier rejection of noise pulses in noise power series, and list normalisation.
"""

import numpy as np

from perseo_core.timing import PreciseDateTime

from s1_denoise import NOISE_LOWER_BOUND, NOISE_UPPER_BOUND


def linear_to_db(value, is_amplitude=False):
    """
    Convert values from linear scale to dB.

    Parameters
    ----------
    value : float or array_like
        Value(s) in linear scale.
    is_amplitude : bool, optional
        If True, `value` is an amplitude and ``20 * log10`` is used, otherwise ``10 * log10`` (power). Default is False.

    Returns
    -------
    float or numpy.ndarray
        Value(s) in dB; a float for scalar input.
    """

    factor = 20 if is_amplitude else 10
    db = factor * np.log10(np.asarray(value, dtype=float))

    return float(db) if db.ndim == 0 else db

def db_to_linear(value, is_amplitude=False):
    """
    Convert values from dB to linear scale.

    Parameters
    ----------
    value : float or array_like
        Value(s) in dB.
    is_amplitude : bool, optional
        If True, an amplitude is returned (``10 ** (x / 20)``), otherwise a power (``10 ** (x / 10)``). Default is False.

    Returns
    -------
    float or numpy.ndarray
        Value(s) in linear scale; a float for scalar input.
    """

    factor = 20 if is_amplitude else 10
    linear = 10 ** (np.asarray(value, dtype=float) / factor)
    
    return float(linear) if linear.ndim == 0 else linear

def iso_to_datetime(iso_str: str) -> PreciseDateTime:
    """
    Convert an ISO timestamp string to a PreciseDateTime object.

    Parameters
    ----------
    iso_str : str
        ISO 8601 timestamp.

    Returns
    -------
    PreciseDateTime
        Corresponding time.
    """
    return PreciseDateTime.fromisoformat(iso_str)

def get_range_boundaries(swath_merges, subswath, az_time: str | PreciseDateTime):
    """
    Return the first and last GRD range samples of a subswath at a given azimuth time.

    The swath bounds record used is the latest one with azimuth time not after `az_time`, or the first record if none
    precedes it.

    Parameters
    ----------
    swath_merges : list of dict
        ``swathMerge`` records of the annotation.
    subswath : str
        Subswath identifier (e.g. ``"IW2"``).
    az_time : str or PreciseDateTime
        Azimuth time (UTC string or PreciseDateTime).

    Returns
    -------
    first_range_sample : int
        First range sample of the subswath.
    last_range_sample : int
        Last range sample of the subswath.

    Raises
    ------
    TypeError
        If `az_time` is neither a str nor a PreciseDateTime.
    ValueError
        If `subswath` is not in `swath_merges`.
    """

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

    available = [swath_merge["swath"] for swath_merge in swath_merges]
    raise ValueError(f"Subswath {subswath} not found in the swath merge list (available: {', '.join(available)})")

def ground2slant(start_az_time, gr_array, coordinate_conversions):
    """
    Convert ground range distances to slant range distances.

    The GRSR polynomial of the coordinate conversion record closest in azimuth to `start_az_time` is evaluated in
    ``(gr - gr0)``.

    Parameters
    ----------
    start_az_time : str
        ISO azimuth time used to select the coordinate conversion record.
    gr_array : array_like
        Ground range distances [m].
    coordinate_conversions : list of dict
        ``coordinateConversion`` records of the annotation (with ``azimuthTime``, ``gr0`` and ``grsrCoefficients``).

    Returns
    -------
    numpy.ndarray
        Slant range distances [m].
    """
    gr_array = np.asarray(gr_array, dtype=float)

    # select closest GRSR polynomial in azimuth
    cc = min(
        coordinate_conversions,
        key=lambda cc: abs(iso_to_datetime(cc["azimuthTime"]) - iso_to_datetime(start_az_time))
    )

    # GRSR polynomial: sr = P(gr - gr0) (sr0 is the origin of the SRGR polynomial only)
    gr0 = float(cc["gr0"])
    grsr_coeffs = [float(c) for c in cc["grsrCoefficients"]['#text'].split(' ')]

    delta_gr = gr_array - gr0
    sr_array = np.polyval(grsr_coeffs[::-1], delta_gr)  # np.polyval usa coeff in ordine decrescente

    return sr_array

def slant2ground(start_az_time, sim_noise_sr, sin_incidence_sr, coordinate_conversions, sr_distances):
    """
    Resample slant range data onto a uniform ground range grid.

    The slant range distances are converted to ground range with the SRGR polynomial of the coordinate conversion record
    closest in azimuth to `start_az_time`, as ``P(sr - sr0)``. Both inputs are then linearly interpolated onto a
    uniform ground range grid spanning the converted distances, with the same number of samples (NaN outside).

    Parameters
    ----------
    start_az_time : str
        ISO azimuth time used to select the coordinate conversion record.
    sim_noise_sr : array_like
        Simulated noise on the slant range grid.
    sin_incidence_sr : array_like
        Sine of the incidence angle on the slant range grid.
    coordinate_conversions : list of dict
        Coordinate conversion records with ``azimuthTime``, ``sr0`` and ``srgrCoeffs`` (list of float).
    sr_distances : array_like
        Slant range distances [m] of the samples of `sim_noise_sr` and `sin_incidence_sr`.

    Returns
    -------
    sim_noise_gr : numpy.ndarray
        Simulated noise on the uniform ground range grid.
    sin_incidence_gr : numpy.ndarray
        `sin_incidence_sr` resampled on the uniform ground range grid.
    """
    sim_noise_sr = np.asarray(sim_noise_sr, dtype=float)
    sin_incidence_sr = np.asarray(sin_incidence_sr, dtype=float)
    sr_distances = np.asarray(sr_distances, dtype=float)

    # select closest SRGR polynomial in azimuth
    cc = min(
        coordinate_conversions,
        key=lambda cc: abs(iso_to_datetime(cc["azimuthTime"]) - iso_to_datetime(start_az_time))
    )

    sr0 = float(cc["sr0"])
    sr_to_gr_polynomial = cc["srgrCoeffs"]

    # from SR distances to GR distances (metres): SRGR polynomial gr = P(sr - sr0)
    # (gr0 is the origin of the GRSR polynomial only)
    sr_norm = sr_distances - sr0
    gr = np.polyval(sr_to_gr_polynomial[::-1], sr_norm)

    # resample onto a uniform GR grid of same length
    gr_grid = np.linspace(gr.min(), gr.max(), sim_noise_sr.size)

    sim_noise_gr = np.interp(gr_grid, gr, sim_noise_sr, left=np.nan, right=np.nan)
    sin_incidence_gr = np.interp(gr_grid, gr, sin_incidence_sr, left=np.nan, right=np.nan)

    return sim_noise_gr, sin_incidence_gr

def remove_outliers(azimuth_times, noise_pulses, expected_noise_value):
    """
    Remove the noise pulses falling outside an interval around the expected noise power, with their azimuth times.

    A pulse is retained if it lies within ``[P_exp - NOISE_LOWER_BOUND dB, P_exp + NOISE_UPPER_BOUND dB]``, with
    ``P_exp`` the expected noise power.

    Parameters
    ----------
    azimuth_times : array_like
        Azimuth times of the noise pulses, same length as `noise_pulses`.
    noise_pulses : array_like
        Noise power of each pulse (linear scale).
    expected_noise_value : float
        Expected noise power from AUX_INS (linear scale).

    Returns
    -------
    azimuth_times_clean : numpy.ndarray
        Azimuth times of the retained pulses.
    noise_pulses_clean : numpy.ndarray
        Noise power of the retained pulses.
    outlier_indices : list of list
        Removed pulses, as ``[index, azimuth_time, noise_pulse]``, sorted by index.
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
    Return the input as a list.

    If `x` is already a list it is returned unchanged, otherwise it is wrapped in a single-element list.

    Parameters
    ----------
    x : object
        Input value.

    Returns
    -------
    list
        `x` itself, or ``[x]``.
    """

    return x if isinstance(x, list) else [x]