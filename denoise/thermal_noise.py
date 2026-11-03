# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module implementing the ThermalNoise class for physics-based simulation of
Sentinel-1 thermal noise profiles.

Encapsulates the full noise modelling chain: noise power correction (from L0N or
annotation), elevation pattern, range spread loss, PG correction, processing gain
normalization, and azimuth antenna pattern reconstruction on the FFT grid. Supports
both SLC and GRD processing for TopSAR IW and EW modes.
"""

import numpy as np
import matplotlib.pyplot as plt
import os
import math
import sys
from numpy.typing import NDArray
from scipy.constants import speed_of_light

from denoise import R_REF
from denoise.utils import remove_outliers
from denoise.logger import denoise_logger
from denoise.auxiliary_files import AuxiliaryFiles
from denoise.xml_reader import (
    read_pg_correction_list,
    read_noise_power_correction_from_annotation,
    read_elevation_pattern,
    read_incidence_angles, 
    extract_FR_info, 
    extract_AR_info
)
from denoise.noise_norm_factor import (
    find_fft_size, 
    hamming, 
    calculate_window_norm_factor, 
    update_window_noise_norm,
    get_start_indices
)

class ThermalNoise:

    def __init__(self, metadata_dict, channel, aux_info: AuxiliaryFiles, product_id, noise_power_from_l0 = None, subswath = None):

        self.metadata_dict = metadata_dict
        self.channel = channel
        self.annotation = channel.annotation
        if subswath is None:
            self.is_grd = False
            self.swath_id = channel.swath_id    # SLC case
        else:
            self.is_grd = True
            self.swath_id = subswath            # GRD case
        self.polarization = channel.pol

        # load and parse L1 annotation file
        # product info
        product = self.metadata_dict["product"]
        self.mode = product['adsHeader']['mode']
        self.product_type = product['adsHeader']['productType']
        self.lines_per_burst = int(product["swathTiming"]["linesPerBurst"])
        if self.lines_per_burst == 0:
            self.lines_per_burst = 1300
        self.sentinel_type = product['adsHeader']['missionId']

        # image annotation
        image_annotation = product["imageAnnotation"]
        self.az_dim = int(image_annotation["imageInformation"]["numberOfLines"])
        self.apply_azi_ant_pattern = str(image_annotation["processingInformation"]["antennaAzimuthPatternApplied"]).strip().lower() == "true"
        self.input_azi_spacing = 1 / float(image_annotation["imageInformation"]["azimuthFrequency"])       

        # azimuth processing info
        params = image_annotation["processingInformation"]["swathProcParamsList"]["swathProcParams"]
        # normalize to list
        if isinstance(params, dict):
            params = [params]
        elif not isinstance(params, list):
            denoise_logger.error(
                f"Unexpected swath processing parameters type: {type(params)}"
            )
            sys.exit(128)

        self.swath_params = [item for item in params if item.get("swath") == self.swath_id]

        azimuth_processing = self.get_azimuth_processing(self.swath_id)
        self.look_bandwidth = float(azimuth_processing["lookBandwidth"])
        self.processing_bandwidth = float(azimuth_processing["processingBandwidth"])
        self.number_of_looks = int(azimuth_processing["numberOfLooks"])
        self.look_overlap = float(azimuth_processing["lookOverlap"])

        # general annotation
        self.radar_frequency = float(product["generalAnnotation"]["productInformation"]["radarFrequency"])
        self.velocities = []
        self.orbits = product["generalAnnotation"]["orbitList"]["orbit"]

        for orbit in self.orbits:
            velocity = orbit["velocity"]
            vx = float(velocity["x"])
            vy = float(velocity["y"])
            vz = float(velocity["z"])

            norm = math.sqrt(vx ** 2 + vy ** 2 + vz ** 2)
            self.velocities.append(norm)

        # other info
        self.get_noise_power_correction(noise_power_from_l0)
        self.product_id = product_id
        self.range_over_sampling_factor = self.sampling_freq_afterdecimation() / self.rg_bandwidth()
        self.rel_burst = None # only for TOPSAR SLC
        self.range_t = None
        self.range_distances = None
        self.range_spread_loss = None

        # info from auxiliary files
        self.read_aux_info(aux_info)

    
    def get_azimuth_processing(self, swath_name):
        for sp in self.swath_params:
            if sp["swath"] == swath_name:
                return sp["azimuthProcessing"]
        raise KeyError(f"Swath {swath_name} not found")

    def read_aux_info(self, aux_info: AuxiliaryFiles):

        # from PP1
        self.aux_pp1_calib = aux_info.get_gain_from_pp1(self.product_id, self.swath_id, self.polarization)
        self.alpha_hamming, self.slc_alpha_hamming = aux_info.get_alpha_from_pp1(self.product_id, self.swath_id)

        # from CAL
        self.aap_gain = aux_info.get_aap_gain(self.swath_id, self.polarization)
        self.aap_gain_count = aux_info.get_aap_gain_count(self.swath_id, self.polarization)
        self.azi_angle_incr = aux_info.get_azi_angle_incr(self.swath_id, self.polarization)
        self.absolute_calibration_constant = aux_info.get_abs_cal(self.swath_id, self.polarization)
        self.noise_calibration_factor = aux_info.get_noise_cal(self.swath_id, self.polarization)
        
        # from INS
        self.pg_reference = aux_info.get_pg_reference(self.swath_id, self.polarization)

        denoise_logger.debug(f"Absolute calibration constant: {self.absolute_calibration_constant}")

    def rg_bandwidth(self):
        data = self.annotation.findall(
            "imageAnnotation/processingInformation/swathProcParamsList/swathProcParams/swath"
        )
        for ind, element in enumerate(data):
            if element.text == self.swath_id:
                rg_bandwidth = float(
                    self.annotation.find(
                        "imageAnnotation/processingInformation/swathProcParamsList/swathProcParams[{}]/rangeProcessing/totalBandwidth".format(
                            ind + 1
                        )
                    ).text
                )

        return rg_bandwidth

    def sampling_freq_afterdecimation(self):
        data = self.annotation.findall(
            "generalAnnotation/downlinkInformationList/downlinkInformation/swath"
        )
        for ind, element in enumerate(data):
            if element.text == self.swath_id:
                sampling_freq_afterdecimation = float(
                    self.annotation.find(
                        "generalAnnotation/downlinkInformationList/downlinkInformation[{}]/downlinkValues/rangeDecimation/samplingFrequencyAfterDecimation".format(
                            ind + 1
                        )
                    ).text
                )

        return sampling_freq_afterdecimation

    def pg_correction_computation(self, azimuth_T):

        pg_correction_factors, pg_az_t = read_pg_correction_list(self.annotation, self.swath_id)

        pg_corr_az_central_t = np.interp(azimuth_T - pg_az_t[0], (pg_az_t - pg_az_t[0]).astype(float), pg_correction_factors)

        return pg_corr_az_central_t

    def get_noise_power_correction(self, noise_power_from_l0):

        if noise_power_from_l0 is None:
            noise_power_correction_factors, noise_az_t = read_noise_power_correction_from_annotation(self.metadata_dict, self.swath_id)
        else:
            noise_az_t                      = noise_power_from_l0.get((self.swath_id,self.polarization))[0]
            noise_power_correction_factors  = noise_power_from_l0.get((self.swath_id,self.polarization))[1]

        # RFI handling
        self.noise_az_t, self.noise_power_correction_factors, self.rfi_detected = remove_outliers(noise_az_t, noise_power_correction_factors)

        for ind in range(len(self.rfi_detected)):
            formatted = self.rfi_detected[ind][1].isoformat(timespec="microseconds").rstrip("Z")
            denoise_logger.warning(f"RFI detected at azimuth time {formatted}, discarding noise power correction factor")

    def noise_power_computation(self, azimuth_T):

        # convert reference times into seconds relative to the first noise azimuth time
        azimuth_T_sec = (azimuth_T - self.noise_az_t[0])
        noise_az_t_sec = np.array([(t - self.noise_az_t[0]) for t in self.noise_az_t])

        # perform interpolation in float domain
        noise_power_correction_az_central_t = np.interp(
            azimuth_T_sec,
            noise_az_t_sec,
            self.noise_power_correction_factors,
        )

        denoise_logger.debug(f"Noise power correction: {noise_power_correction_az_central_t:.4}")

        return noise_power_correction_az_central_t

    def elevation_pattern(self, azimuth_T):

        # elevation pattern used to compensate
        elevation_pattern_gain, slant_range_t = read_elevation_pattern(
            self.metadata_dict, self.swath_id, azimuth_T
        )

        final_elevation_pattern_gain = np.interp(
            self.range_t, slant_range_t, elevation_pattern_gain
        )

        return final_elevation_pattern_gain

    def incidence_angles(self, azimuth_T):

        # incidence angles used to compensate
        incidence_angles, slant_range_t = read_incidence_angles(
            self.metadata_dict, self.swath_id, azimuth_T
        )

        final_incidence_angles = np.interp(
            self.range_t, slant_range_t, incidence_angles
        )
        return final_incidence_angles

    def compute_k_proc(self):

        k_proc = ( self.aux_pp1_calib / np.sqrt(self.range_over_sampling_factor) ) ** 2

        return k_proc

    def compute_G_tot(self, azimuth_T):

        pg = self.pg_correction_computation(azimuth_T) * self.pg_reference
        k_N = self.noise_calibration_factor
        k_proc = self.compute_k_proc()
        elev_pattern = self.elevation_pattern(azimuth_T)
        rsl = self.range_spread_loss

        G_tot = pg**2 * k_N * k_proc / (elev_pattern * rsl)

        return G_tot

    def compute_simulated_noise(self, azimuth_T) -> NDArray[np.float64]:

        inc_angles = np.deg2rad(self.incidence_angles(azimuth_T))
        noise_pwr = self.noise_power_computation(azimuth_T)
        G_tot = self.compute_G_tot(azimuth_T)

        self.reconstruct_noise_norm_factor()

        simulated_noise = np.sin(inc_angles) * noise_pwr * G_tot * self.noise_norm_factor

        return np.array(simulated_noise, dtype=np.float64), np.sin(inc_angles)

    def plot_in_range(self, burst_idx, fig_name):

        fig2 = plt.figure(figsize=(14, 6))
        ax1 = fig2.add_subplot(1, 2, 1)
        ax1.plot(10 * np.log10(self.elevation_pattern(burst_idx)), label="elevation pattern")

        ax1.set_xlabel("range samples")
        plt.legend()
        ax1.set_ylabel(r"$\sigma^0$ [dB]")

        ax2 = fig2.add_subplot(1, 2, 2)
        ax2.plot(10 * np.log10(self.range_spread_loss), label="range spread loss")
        ax2.set_xlabel("range samples")
        plt.legend()
        ax2.set_ylabel(r"$\sigma^0$ [dB]")

        plt.grid()
        plt.savefig(os.path.join(fig_name))

    def hamming_windowing(self, i_freqAxis, i_hammPar, i_bandPass, i_bandTrans, i_normFact=1.0, i_freq0 = 0.0):
    
        B = i_bandTrans * i_normFact
        hamming_window = np.zeros(i_freqAxis.size)
        f = abs(i_freqAxis - i_freq0) - i_bandPass * i_normFact
        
        for index in range(i_freqAxis.size):
            
            if f[index] <= 0.0:
                hamming_window[index] = 1.0
            elif f[index] >= B:
                hamming_window[index] = 0.0
            else:
                hamming_window[index] = i_hammPar + (1 - i_hammPar) * np.cos(np.pi * f[index] / B)
        
        return hamming_window

    def get_start_indices(number_of_looks, look_overlap, window_len, in_line_len):

        extracted_freq_samples = window_len + (window_len - look_overlap) * (number_of_looks - 1)
        start_offset = int(np.floor((1.0 - extracted_freq_samples) / 2))

        look_start_indices = [0] * number_of_looks
        look_end_indices = [0] * number_of_looks

        for lookIdx in range(number_of_looks):
            look_start_indices[lookIdx] = int((start_offset + (window_len - look_overlap) * lookIdx) % in_line_len)

            look_end_indices[lookIdx] = int((look_start_indices[lookIdx] + window_len - 1) % in_line_len)

        return look_start_indices
    
    def find_fft_size(desired_fft_size):

        FFT_VALID_LENGTHS = [ 240, 256, 288, 320, 384, 480, 512, 576, 640, 720, 768, 960, 1024, 1152, 1300, 1400, 1584, 1800, 1950, 2080, 2464, 2560, 2880, 3072, 3564, 3750, 3822, 4096, 4400, 5000, 5760, 6144, 7840, 8100, 8400, 8640, 8736, 8820, 9072, 9216, 11520, 12288, 15360, 15552, 16000, 16384, 17280, 18000, 18480, 19008, 19440, 19500, 20000, 20580, 22400, 23760, 24576, 25920, 27648, 28800, 30720, 31104, 32000, 32768, 34560, 36000, 36864, 38400, 40000, 40960, 41472, 43200, 46080, 48000, 49152, 51200, 51840, 54000, 55296, 57600, 60000, 61440, 62208, 64000, 64800, 65536, 69120, 72000, 73728, 76800, 77760, 80000, 81920, 82944, 86400, 92160, 93312, 96000, 98304 ]

        for size in FFT_VALID_LENGTHS:
            if desired_fft_size <= size:
                return size
            
        raise ValueError(f"No valid FFT size found for the desired FFT size {desired_fft_size}.")
    
    def get_shrink_factor(self):

        range_filter = int(self.rel_burst is not None)
        range_times = self.range_t[::range_filter]

        # deramping matrix
        FM_rate, az_mid_time = extract_FR_info(self.metadata_dict, range_times, self.rel_burst, self.is_grd, self.swath_id)
        FM_rate_mid = FM_rate[range_times.size // 2]

        AR_vec = extract_AR_info(self.metadata_dict, az_mid_time, range_times, subswath=self.swath_id)
            
        # give back FM rate and DC rate evaluated at mid-range like IPF
        doppler_centroid_rate = AR_vec[range_times.size // 2]

        self.shrink_factor = 1 - doppler_centroid_rate / FM_rate_mid

    def set_range_values(self, range_t):

        self.range_t = range_t # [s]
        self.range_distances = self.range_t * speed_of_light / 2  # [m]
        self.range_spread_loss  = (R_REF / self.range_distances) ** 4

    def reconstruct_noise_norm_factor(self):
        """Reversed engineered from IPF: reconstructs the azimuth antenna pattern on the FFT grid, applies windowing over multiple overlapping looks, estimates the noise processing gain for each look, and stores the mean value in `self.noise_norm_factor`."""

        # 1. Constants and basic frequency parameters

        LAMBDA = speed_of_light / self.radar_frequency

        fft_length = find_fft_size(self.lines_per_burst)
        sampling_freq_az = 1.0 / self.input_azi_spacing
        delta_freq = sampling_freq_az / fft_length

        bw_in_samples = int(np.round(self.processing_bandwidth / delta_freq))
        if bw_in_samples == 0:
            raise ValueError("Zero samples in bandwith")        
        
        window = hamming(bw_in_samples, alpha=self.slc_alpha_hamming)

        look_start_indices = get_start_indices(
            look_overlap=self.look_overlap,
            window_len=len(window),
            in_line_len=fft_length
        )


        # 2. Angle computation

        angle_incr_rad = LAMBDA / (2 * np.mean(self.velocities))
        angle_incr_rad *= delta_freq
        angle_incr_rad *= self.shrink_factor

        denoise_logger.debug(f"Shrink factor: {self.shrink_factor:.2f}")

        angle_incr_deg = np.rad2deg(angle_incr_rad)

        output_start_angle = -fft_length / 2 * angle_incr_deg
        input_start_angle = -self.azi_angle_incr * (self.aap_gain_count - 1) / 2

        output_az_axis = np.arange(fft_length) * angle_incr_deg + output_start_angle
        input_az_axis = (
            np.arange(self.aap_gain_count) * self.azi_angle_incr + input_start_angle
        )

        # 3. Antenna pattern interpolation and correction

        aap_data = np.interp(x=output_az_axis, xp=input_az_axis, fp=self.aap_gain)
        aap_data = np.fft.fftshift(aap_data)

        data_spectra = 10 ** (aap_data / 20) # convert AAP from dB to linear scale

        # 4. Window normalization

        window_norm_factor = calculate_window_norm_factor(
            data_spectra=data_spectra,
            in_line_len=fft_length,
            window=window,
            bw_in_samples=bw_in_samples
        )

        sum_window_sq_noise = update_window_noise_norm(
            data_spectra=data_spectra,
            window=window,
            look_start_indices=look_start_indices,
            in_line_len=fft_length
        )

        # 5. Noise normalization factor computation

        if np.any(window_norm_factor == 0.0):
            raise ValueError("Zero window normalization factor")

        noise_proc_gain = sum_window_sq_noise / bw_in_samples
        noise_norm_factor_all_looks = noise_proc_gain / window_norm_factor

        self.noise_norm_factor = np.mean(noise_norm_factor_all_looks)

        if self.is_grd and self.swath_id == "EW1":
            self.noise_norm_factor *= 1.48      # TODO: where does this come from?

        denoise_logger.debug(f"Noise norm factor: {self.noise_norm_factor}")
