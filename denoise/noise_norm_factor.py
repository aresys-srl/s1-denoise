# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Utility module providing signal processing helpers for noise profile generation.

Includes functions for FFT size selection, Hamming window computation, look
start index calculation, window normalisation factor estimation, and azimuth
antenna pattern extraction from AUX_CAL files.
"""

import numpy as np
import math
from lxml import etree

from denoise.logger import denoise_logger

def find_fft_size(desired_fft_size):

    FFT_VALID_LENGTHS = [ 240, 256, 288, 320, 384, 480, 512, 576, 640, 720, 768, 960, 1024, 1152, 1300, 1400, 1584, 1800, 1950, 2080, 2464, 2560, 2880, 3072, 3564, 3750, 3822, 4096, 4400, 5000, 5760, 6144, 7840, 8100, 8400, 8640, 8736, 8820, 9072, 9216, 11520, 12288, 15360, 15552, 16000, 16384, 17280, 18000, 18480, 19008, 19440, 19500, 20000, 20580, 22400, 23760, 24576, 25920, 27648, 28800, 30720, 31104, 32000, 32768, 34560, 36000, 36864, 38400, 40000, 40960, 41472, 43200, 46080, 48000, 49152, 51200, 51840, 54000, 55296, 57600, 60000, 61440, 62208, 64000, 64800, 65536, 69120, 72000, 73728, 76800, 77760, 80000, 81920, 82944, 86400, 92160, 93312, 96000, 98304 ]

    for size in FFT_VALID_LENGTHS:

        if desired_fft_size <= size:
            return size
        
    raise ValueError(f"No valid FFT size found for the desired FFT size {desired_fft_size}.")

import numpy as np

def update_window_noise_norm(data_spectra, window, look_start_indices, in_line_len):
    """Computation of the sum of squared (window / data_spectra) for multiple starting positions, with wrapping."""

    window = np.asarray(window, dtype=np.float64)
    data_spectra = np.asarray(data_spectra, dtype=np.float64)
    look_start_indices = np.asarray(look_start_indices, dtype=np.int64)

    window_len = window.size

    if np.any(look_start_indices >= in_line_len):
        raise ValueError(f"look_start_indices ({look_start_indices}) must be less than in_line_len ({in_line_len})")
    if window_len > in_line_len:
        raise ValueError(f"window length ({window_len}) must be <= in_line_len ({in_line_len})")
    if data_spectra.size < in_line_len:
        raise ValueError(f"data_spectra is shorter ({data_spectra.size}) than in_line_len ({in_line_len})")

    window_sq = window ** 2
    base_idx = np.arange(window_len, dtype=np.int64)

    idx = (look_start_indices[:, None] + base_idx) % in_line_len
    segments = data_spectra[idx]

    sum_window_sq_noise = np.sum(window_sq / (segments**2), axis=1)

    return sum_window_sq_noise


def calculate_window_norm_factor(data_spectra, bw_in_samples, in_line_len, window, num_looks = 1):

    sum_window_sq = np.sum(window ** 2)

    if data_spectra is not None:
        data_spectra = np.array(data_spectra, dtype=np.float32)

        start_index = math.floor((-1.0 * bw_in_samples + 1) / 2)
        start_index %= in_line_len

        indices = (start_index + np.arange(bw_in_samples)) % in_line_len

        selected = np.take(data_spectra, indices, mode="wrap")

        sum_adjustment_sq = np.sum(selected ** 2)

    else:
        sum_adjustment_sq = bw_in_samples

    window_norm_factor = np.full(num_looks, sum_window_sq / sum_adjustment_sq, dtype=np.float32)

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

    # Hamming
    for i in range(1, nsize + 1): 
        arg = TWO_PI * (float(i) - centre) / fsize
        whamm[i - 1] = alpha + (1.0 - alpha) * math.cos(arg)

    return whamm

def get_start_indices(look_overlap, window_len, in_line_len, number_of_looks = 1):

    extracted_freq_samples = window_len + (window_len - look_overlap) * (number_of_looks - 1)
    start_offset = int(np.floor((1.0 - extracted_freq_samples) / 2))

    look_start_indices = [0] * number_of_looks
    look_end_indices = [0] * number_of_looks

    for lookIdx in range(number_of_looks):
        look_start_indices[lookIdx] = int((start_offset + (window_len - look_overlap) * lookIdx) % in_line_len)

        look_end_indices[lookIdx] = int((look_start_indices[lookIdx] + window_len - 1) % in_line_len)

    return look_start_indices

def extract_azimuth_antenna_pattern(mission_id, target_swath, target_polarisation):
    """Read from appropriate AUX calibration files"""
    
    tree = etree.parse(f'./{mission_id.lower()}-aux-cal.xml')
    root = tree.getroot()

    # find the calibration parameters matching swath and polarisation
    for cal_params in root.findall(".//calibrationParams"):
        swath = cal_params.find("swath").text
        polarisation = cal_params.find("polarisation").text
        
        if swath == target_swath and polarisation == target_polarisation:
            # found the correct swath and polarisation, now extract AAP values
            values = cal_params.find("azimuthAntennaPattern/values")
            values_text = values.text.strip()
            values_count = int(values.attrib['count'])
            aap_gain = [float(val) for val in values_text.split()]
            azi_angle_incr = float(cal_params.find("azimuthAntennaPattern/azimuthAngleIncrement").text)
            return aap_gain, values_count, azi_angle_incr


    # if no matching swath/polarisation is found
    denoise_logger.error(f"No matching swath '{target_swath}' and polarisation '{target_polarisation}' found.")
    return None
