# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Utility module providing signal processing helpers for noise profile generation.

Includes functions for FFT size selection, Hamming window computation, look
start index and overlap calculation, and window normalisation factor estimation.

IPF noise normalization factor
------------------------------
During L1 post-processing (LBP), each look is extracted with a Hamming window
and, in azimuth, possibly corrected for the azimuth antenna pattern (AAP). The
signal is then scaled by the window normalization factor so that its power is
preserved:

    windowNorm = sum(w^2) / sum(AAP^2 over the processing bandwidth)

Thermal noise has a flat spectrum, so this normalization changes its power
differently. Since the AAP correction (division by AAP) also boosts the noise
at the spectrum edges, its processing gain is:

    noiseProcGain = sum(w^2 / AAP^2) / bwInSamples

The noise normalization factor is the ratio between the two, averaged over the
looks:

    noiseNormFactor = mean_looks(noiseProcGain / windowNorm)

It is computed in azimuth and in range (flat spectrum, so the range factor is
~1) and the final value is their product. The IPF multiplies the noise vectors
by this factor when noiseNormalizationFlag is set, and annotates its average
over the blocks of each swath in noiseNormalizationFactorList (1.0 if the flag
is not set). If the AAP correction is not applied, AAP is not divided out:
sum(w^2) is used in noiseProcGain and sum((AAP * w)^2) per look in windowNorm.
"""

import numpy as np
import math

def find_fft_size(desired_fft_size):

    FFT_VALID_LENGTHS = [ 240, 256, 288, 320, 384, 480, 512, 576, 640, 720, 768, 960, 1024, 1152, 1300, 1400, 1584, 1800, 1950, 2080, 2464, 2560, 2880, 3072, 3564, 3750, 3822, 4096, 4400, 5000, 5760, 6144, 7840, 8100, 8400, 8640, 8736, 8820, 9072, 9216, 11520, 12288, 15360, 15552, 16000, 16384, 17280, 18000, 18480, 19008, 19440, 19500, 20000, 20580, 22400, 23760, 24576, 25920, 27648, 28800, 30720, 31104, 32000, 32768, 34560, 36000, 36864, 38400, 40000, 40960, 41472, 43200, 46080, 48000, 49152, 51200, 51840, 54000, 55296, 57600, 60000, 61440, 62208, 64000, 64800, 65536, 69120, 72000, 73728, 76800, 77760, 80000, 81920, 82944, 86400, 92160, 93312, 96000, 98304 ]

    for size in FFT_VALID_LENGTHS:

        if desired_fft_size <= size:
            return size
        
    raise ValueError(f"No valid FFT size found for the desired FFT size {desired_fft_size}.")

def update_window_noise_norm(data_spectra, window, look_start_indices, in_line_len, correct_spectra=True):
    """Computation of the sum of squared (window / data_spectra) for multiple starting positions, with wrapping.
    If the data spectra are not corrected, the sum of squared window is used for each look (as in IPF)."""

    window = np.asarray(window, dtype=np.float64)
    look_start_indices = np.asarray(look_start_indices, dtype=np.int64)

    window_len = window.size
    window_sq = window ** 2

    if data_spectra is None or not correct_spectra:
        return np.full(look_start_indices.size, np.sum(window_sq))

    data_spectra = np.asarray(data_spectra, dtype=np.float64)

    if np.any(look_start_indices >= in_line_len):
        raise ValueError(f"look_start_indices ({look_start_indices}) must be less than in_line_len ({in_line_len})")
    if window_len > in_line_len:
        raise ValueError(f"window length ({window_len}) must be <= in_line_len ({in_line_len})")
    if data_spectra.size < in_line_len:
        raise ValueError(f"data_spectra is shorter ({data_spectra.size}) than in_line_len ({in_line_len})")

    base_idx = np.arange(window_len, dtype=np.int64)

    idx = (look_start_indices[:, None] + base_idx) % in_line_len
    segments = data_spectra[idx]

    sum_window_sq_noise = np.sum(window_sq / (segments**2), axis=1)

    return sum_window_sq_noise


def calculate_window_norm_factor(data_spectra, bw_in_samples, in_line_len, window, look_start_indices, correct_spectra=True):
    """Window normalization factor for each look (IPF LBP_GenerateLook::calcNormFactor).
    If the data spectra are not corrected, the spectra remaining at the end of processing
    are included in the sum of squared window, separately for each look."""

    window = np.asarray(window, dtype=np.float64)
    look_start_indices = np.asarray(look_start_indices, dtype=np.int64)

    if data_spectra is not None:
        data_spectra = np.array(data_spectra, dtype=np.float32)

        start_index = math.floor((-1.0 * bw_in_samples + 1) / 2)
        start_index %= in_line_len

        indices = (start_index + np.arange(bw_in_samples)) % in_line_len

        selected = np.take(data_spectra, indices, mode="wrap")

        sum_adjustment_sq = np.sum(selected ** 2)

    else:
        sum_adjustment_sq = bw_in_samples

    if data_spectra is not None and not correct_spectra:
        idx = (look_start_indices[:, None] + np.arange(window.size)) % in_line_len
        sum_window_sq = np.sum((data_spectra[idx] * window) ** 2, axis=1)
    else:
        sum_window_sq = np.full(look_start_indices.size, np.sum(window ** 2))

    window_norm_factor = sum_window_sq / sum_adjustment_sq

    return window_norm_factor

def hamming(nsize, alpha):

    whamm = np.zeros(nsize, dtype=np.float64)

    # alpha >= 1.0 -> flat window
    if alpha >= 1.0:
        whamm[:] = 1.0
        return whamm

    if alpha < 0.0:
        return None

    # Constants
    TWO_PI = 2.0 * math.pi
    centre = float(nsize + 1) / 2.0  # central index
    fsize = float(nsize - 1)

    # Hamming (sample indices from 1 to nsize)
    arg = TWO_PI * (np.arange(1, nsize + 1, dtype=np.float64) - centre) / fsize
    whamm = alpha + (1.0 - alpha) * np.cos(arg)

    return whamm

def get_look_overlap_samples(window_len, bw_in_samples, number_of_looks = 1):
    """Look overlap in frequency samples, computed as in IPF (LBP_GenerateLook::initBlock)."""

    if number_of_looks <= 1:
        return 0

    extra_samples = window_len * number_of_looks - bw_in_samples

    return int(math.ceil(extra_samples / (number_of_looks - 1.0)))

def get_start_indices(look_overlap, window_len, in_line_len, number_of_looks = 1):
    """Start index of each look on the FFT grid. look_overlap is in frequency samples."""

    extracted_freq_samples = window_len + (window_len - look_overlap) * (number_of_looks - 1)
    start_offset = int(np.floor((1.0 - extracted_freq_samples) / 2))

    look_start_indices = [0] * number_of_looks
    look_end_indices = [0] * number_of_looks

    for lookIdx in range(number_of_looks):
        look_start_indices[lookIdx] = int((start_offset + (window_len - look_overlap) * lookIdx) % in_line_len)

        look_end_indices[lookIdx] = int((look_start_indices[lookIdx] + window_len - 1) % in_line_len)

    return look_start_indices
