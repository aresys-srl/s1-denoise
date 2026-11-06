# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module implementing the ThermalNoise class for physics-based simulation of
Sentinel-1 thermal noise profiles.

Encapsulates the full noise modelling chain: noise power (from the L0N product or
the annotation), elevation antenna pattern, range spreading loss, PG correction,
processing gain, and noise normalization factor, the latter reconstructed from the
azimuth antenna pattern on the azimuth FFT grid. Supports both SLC and GRD processing
for TopSAR IW and EW modes.
"""

import numpy as np
import math
import sys
from numpy.typing import NDArray
from scipy.constants import speed_of_light

from s1_denoise import R_REF
from s1_denoise.utils import db_to_linear, remove_outliers
from s1_denoise.logger import denoise_logger
from s1_denoise.auxiliary_files import AuxiliaryFiles
from s1_denoise.xml_reader import (
    read_pg_correction_list,
    read_noise_power_correction_from_annotation,
    read_antenna_patterns,
    closest_antenna_pattern_index,
    extract_FR_info,
    extract_AR_info,
    build_orbit
)
from s1_denoise.noise_norm_factor import (
    find_fft_size, 
    hamming, 
    calculate_window_norm_factor, 
    update_window_noise_norm,
    get_start_indices,
    get_look_overlap_samples
)

class ThermalNoise:
    """
    Thermal noise model of a single subswath and polarization of a Sentinel-1 L1 product.

    Reads once all the burst-independent quantities (annotation parameters, auxiliary file values, antenna
    patterns, PG corrections, orbit, noise power) and provides the methods to compute the simulated noise
    range vector at a given azimuth time.

    Before calling `compute_simulated_noise`, the caller must set the slant range axis with `set_range_values`,
    set `rel_burst` and compute the shrink factor with `get_shrink_factor`.

    Parameters
    ----------
    metadata_dict : dict
        Product annotation parsed into a dictionary (see `xml_reader.read_metadata_from_safe_annotation`).
    channel : S1Channel
        Channel (polarization and swath) of the SAFE product, providing the parsed annotation XML tree.
    aux_info : AuxiliaryFiles
        Auxiliary files (AUX_PP1, AUX_CAL, AUX_INS) used for processing.
    product_id : str
        Product identifier used to select the AUX_PP1 parameters (e.g. ``"IW_SLC__1"``).
    noise_power_from_l0 : dict, optional
        Noise power extracted from the L0N product, mapping ``(swath, polarization)`` to
        ``[azimuth_times, noise_powers]``. If None (default), the noise power is read from the noise list of
        the product annotation.
    subswath : str, optional
        Subswath identifier (e.g. ``"IW1"``) for GRD products, where the annotation covers all subswaths.
        If None (default), the product is treated as SLC and the swath of `channel` is used.

    Attributes
    ----------
    is_grd : bool
        True for GRD products, i.e. when `subswath` is given.
    swath_id : str
        Identifier of the processed subswath.
    polarization : str
        Polarization of the channel.
    lines_per_burst : int
        Number of lines per burst (1300 if annotated as 0).
    samples_per_burst : int
        Number of samples per burst.
    look_bandwidth, processing_bandwidth : float
        Azimuth look and processing bandwidths [Hz].
    number_of_looks : int
        Number of azimuth looks.
    azi_window_coefficient : float
        Azimuth Hamming window coefficient.
    rg_processing_bandwidth : float
        Range processing bandwidth [Hz].
    radar_frequency : float
        Radar carrier frequency [Hz].
    velocities : list of float
        Norm of the annotated orbit state vector velocities [m/s].
    antenna_patterns : dict
        Antenna pattern records of the subswath (see `xml_reader.read_antenna_patterns`).
    pg_correction_factors : numpy.ndarray
        Annotated PG product amplitudes.
    pg_az_t : numpy.ndarray
        Azimuth times of the PG correction factors, as PreciseDateTime.
    noise_az_t : numpy.ndarray
        Azimuth times of the retained noise power values.
    noise_power_correction_factors : numpy.ndarray
        Noise power values retained after the noise pulse discard.
    outlier_detected : list
        Discarded noise pulses, as ``[index, azimuth_time, noise_power]``.
    range_over_sampling_factor : float
        Ratio between the range sampling frequency after decimation and the range total bandwidth.
    rel_burst : int or None
        Index of the current burst (SLC) or antenna pattern block (GRD).
    range_t : numpy.ndarray or None
        Slant range times of the current noise vector samples [s].
    range_distances : numpy.ndarray or None
        Slant range distances of the current noise vector samples [m].
    range_spread_loss : numpy.ndarray or None
        Range spreading loss correction $(R_{ref} / R)^4$.
    shrink_factor : float
        TopSAR azimuth shrink factor, set by `get_shrink_factor`.
    noise_norm_factor : float
        Noise normalization factor, set by `reconstruct_noise_norm_factor`.
    """

    def __init__(self, metadata_dict, channel, aux_info: AuxiliaryFiles, product_id, noise_power_from_l0 = None, subswath = None):

        self.metadata_dict = metadata_dict
        self.channel = channel
        self.annotation = channel.annotation
        self.product_id = product_id

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
        self.samples_per_burst = int(product["swathTiming"]["samplesPerBurst"])
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
        self.azi_window_coefficient = float(azimuth_processing["windowCoefficient"])

        range_processing = self.get_range_processing(self.swath_id)
        self.rg_processing_bandwidth = float(range_processing["processingBandwidth"])

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

        # info from auxiliary files
        self.read_aux_info(aux_info)

        # annotation info that does not change between bursts/blocks: read only once
        self.antenna_patterns = read_antenna_patterns(self.metadata_dict, self.swath_id)
        self.pg_correction_factors, self.pg_az_t = read_pg_correction_list(self.annotation, self.swath_id)
        self.orbit = build_orbit(self.metadata_dict)

        # other info
        self.get_noise_power_correction(noise_power_from_l0)
        self.range_over_sampling_factor = self.sampling_freq_afterdecimation() / self.rg_bandwidth()
        self.rel_burst = None # only for TOPSAR SLC
        self.range_t = None
        self.range_distances = None
        self.range_spread_loss = None

    
    def get_azimuth_processing(self, swath_name):
        """
        Return the annotated azimuth processing parameters of a swath.

        Parameters
        ----------
        swath_name : str
            Swath identifier.

        Returns
        -------
        dict
            `azimuthProcessing` section of the swath processing parameters.

        Raises
        ------
        KeyError
            If the swath is not found among the swath processing parameters.
        """
        for sp in self.swath_params:
            if sp["swath"] == swath_name:
                return sp["azimuthProcessing"]
        raise KeyError(f"Swath {swath_name} not found")

    def get_range_processing(self, swath_name):
        """
        Return the annotated range processing parameters of a swath.

        Parameters
        ----------
        swath_name : str
            Swath identifier.

        Returns
        -------
        dict
            `rangeProcessing` section of the swath processing parameters.

        Raises
        ------
        KeyError
            If the swath is not found among the swath processing parameters.
        """
        for sp in self.swath_params:
            if sp["swath"] == swath_name:
                return sp["rangeProcessing"]
        raise KeyError(f"Swath {swath_name} not found")

    def read_aux_info(self, aux_info: AuxiliaryFiles):
        """
        Read the subswath and polarization parameters from the auxiliary files and store them as attributes.

        Reads the processing gain and window coefficients from AUX_PP1; the azimuth antenna pattern (gains in dB
        and angle increment in degrees), absolute calibration constant and noise calibration factor from AUX_CAL;
        the PG reference, expected noise value and azimuth steering rate [rad/s] from AUX_INS.

        Parameters
        ----------
        aux_info : AuxiliaryFiles
            Auxiliary files used for processing.
        """

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
        self.expected_noise_value = aux_info.get_expected_noise_value(self.swath_id, self.polarization)
        self.azimuth_steering_rate = aux_info.get_azimuth_steering_rate(self.swath_id)

        denoise_logger.debug(f"Absolute calibration constant: {self.absolute_calibration_constant}")

    def rg_dim(self):
        """
        Return the number of range samples of the image.

        Returns
        -------
        int
            Annotated `numberOfSamples`.
        """
        return int(self.metadata_dict["product"]["imageAnnotation"]["imageInformation"]["numberOfSamples"])

    def rg_bandwidth(self):
        """
        Return the range total bandwidth of the subswath.

        Returns
        -------
        float
            Annotated range `totalBandwidth` [Hz].

        Raises
        ------
        ValueError
            If the range processing parameters of the subswath are not found in the annotation.
        """
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

        raise ValueError(f"Range processing parameters for swath {self.swath_id} not found in annotation")

    def sampling_freq_afterdecimation(self):
        """
        Return the range sampling frequency after decimation of the subswath.

        Returns
        -------
        float
            Annotated `samplingFrequencyAfterDecimation` [Hz].

        Raises
        ------
        ValueError
            If the downlink information of the subswath is not found in the annotation.
        """
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

        raise ValueError(f"Downlink information for swath {self.swath_id} not found in annotation")

    def pg_correction_computation(self, azimuth_T):
        """
        Interpolate the annotated PG correction factors at a given azimuth time.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time of interpolation.

        Returns
        -------
        float
            PG product amplitude linearly interpolated at `azimuth_T`.
        """

        pg_az_t = self.pg_az_t

        pg_corr_az_central_t = np.interp(azimuth_T - pg_az_t[0], (pg_az_t - pg_az_t[0]).astype(float), self.pg_correction_factors)

        return pg_corr_az_central_t

    def get_noise_power_correction(self, noise_power_from_l0):
        """
        Load the noise power values of the subswath and discard the outlier noise pulses.

        The noise power is taken from the L0N product if provided, otherwise from the noise list of the product
        annotation. Pulses outside the tolerance interval around the AUX_INS expected noise value are discarded
        (a warning is logged for each one); if all of them are discarded, the expected noise value is used at all
        the original azimuth times. Results are stored in `noise_az_t`, `noise_power_correction_factors` and
        `outlier_detected`.

        Parameters
        ----------
        noise_power_from_l0 : dict or None
            Noise power extracted from the L0N product, mapping ``(swath, polarization)`` to
            ``[azimuth_times, noise_powers]``, or None to use the annotated noise list.
        """

        if noise_power_from_l0 is None:
            noise_power_correction_factors, noise_az_t = read_noise_power_correction_from_annotation(self.metadata_dict, self.swath_id)
        else:
            noise_az_t = noise_power_from_l0.get((self.swath_id,self.polarization))[0]
            noise_power_correction_factors = noise_power_from_l0.get((self.swath_id,self.polarization))[1]

        # discard noise pulses
        self.noise_az_t, self.noise_power_correction_factors, self.outlier_detected = remove_outliers(noise_az_t, noise_power_correction_factors, self.expected_noise_value)

        for ind in range(len(self.outlier_detected)):
            formatted = self.outlier_detected[ind][1].isoformat(timespec="microseconds").rstrip("Z")
            denoise_logger.warning(f"Noise pulse at azimuth time {formatted} discarded")

        # all noise pulses discarded: fall back to the nominal expected noise from AUX_INS
        if self.noise_power_correction_factors.size == 0 and len(noise_az_t) > 0:
            denoise_logger.warning(f"All noise pulses discarded for {self.swath_id} {self.polarization}: using nominal noise value from AUX_INS")
            self.noise_az_t = np.array(noise_az_t)
            self.noise_power_correction_factors = np.full(self.noise_az_t.size, self.expected_noise_value)

    def noise_power_computation(self, azimuth_T):
        """
        Interpolate the noise power at a given azimuth time.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time of interpolation.

        Returns
        -------
        float
            Noise power $P_n$ linearly interpolated at `azimuth_T` (held constant outside the noise pulse
            time span).
        """

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
        """
        Return the elevation antenna pattern gain on the current slant range axis.

        Uses the antenna pattern record closest in azimuth time to `azimuth_T` and interpolates it on `range_t`.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time used to select the antenna pattern record.

        Returns
        -------
        numpy.ndarray
            Elevation antenna pattern gain (modulus of the annotated complex pattern, linear) for each sample
            of `range_t`.
        """

        # elevation pattern used to compensate
        idx = closest_antenna_pattern_index(self.antenna_patterns, azimuth_T)

        final_elevation_pattern_gain = np.interp(
            self.range_t,
            self.antenna_patterns["slant_range_t"][idx],
            self.antenna_patterns["elevation_pattern_gain"][idx]
        )

        return final_elevation_pattern_gain

    def incidence_angles(self, azimuth_T):
        """
        Return the incidence angles on the current slant range axis.

        Uses the antenna pattern record closest in azimuth time to `azimuth_T` and interpolates it on `range_t`.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time used to select the antenna pattern record.

        Returns
        -------
        numpy.ndarray
            Incidence angles [deg] for each sample of `range_t`.
        """

        # incidence angles used to compensate
        idx = closest_antenna_pattern_index(self.antenna_patterns, azimuth_T)

        final_incidence_angles = np.interp(
            self.range_t,
            self.antenna_patterns["slant_range_t"][idx],
            self.antenna_patterns["incidence_angles"][idx]
        )
        return final_incidence_angles

    def compute_k_proc(self):
        """
        Compute the processing gain $k_{proc}$.

        Returns
        -------
        float
            Squared AUX_PP1 gain divided by the range oversampling factor.
        """

        k_proc = ( self.aux_pp1_calib / np.sqrt(self.range_over_sampling_factor) ) ** 2

        return k_proc

    def compute_G_tot(self, azimuth_T):
        """
        Compute the total gain applied to the noise power along the current slant range axis.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time at which the PG correction and the elevation antenna pattern are evaluated.

        Returns
        -------
        numpy.ndarray
            Total gain for each sample of `range_t`.

        Notes
        -----
        The total gain is

        $$
        G_{tot} = \\frac{(G_{pg} \\cdot PG_{ref})^2 \\cdot k_{noise} \\cdot k_{proc}}{G_{eap} \\cdot G_{rsl}}
        $$

        where $G_{pg}$ is the interpolated PG correction, $PG_{ref}$ the AUX_INS PG reference,
        $k_{noise}$ the AUX_CAL noise calibration factor, $G_{eap}$ the elevation antenna pattern gain
        and $G_{rsl}$ the range spreading loss correction.
        """

        pg = self.pg_correction_computation(azimuth_T) * self.pg_reference
        k_N = self.noise_calibration_factor
        k_proc = self.compute_k_proc()
        elev_pattern = self.elevation_pattern(azimuth_T)
        rsl = self.range_spread_loss

        G_tot = pg**2 * k_N * k_proc / (elev_pattern * rsl)

        return G_tot

    def compute_simulated_noise(self, azimuth_T) -> NDArray[np.float64]:
        """
        Compute the simulated noise range vector at a given azimuth time, in slant range.

        Also reconstructs the noise normalization factor (see `reconstruct_noise_norm_factor`), so
        `set_range_values` and `get_shrink_factor` must have been called beforehand.

        Parameters
        ----------
        azimuth_T : PreciseDateTime
            Azimuth time of the noise vector (burst center time for SLC, antenna pattern record time for GRD).

        Returns
        -------
        simulated_noise : numpy.ndarray
            Simulated noise $\\sin(\\theta_i) \\cdot P_n \\cdot G_{tot} \\cdot k_{norm}$ (linear power) for each
            sample of `range_t`.
        sin_incidence : numpy.ndarray
            Sine of the incidence angle $\\theta_i$ for each sample of `range_t`.
        """

        inc_angles = np.deg2rad(self.incidence_angles(azimuth_T))
        noise_pwr = self.noise_power_computation(azimuth_T)
        G_tot = self.compute_G_tot(azimuth_T)

        self.reconstruct_noise_norm_factor()

        simulated_noise = np.sin(inc_angles) * noise_pwr * G_tot * self.noise_norm_factor

        return np.array(simulated_noise, dtype=np.float64), np.sin(inc_angles)

    def get_shrink_factor(self, azimuth_time=None):
        """
        Compute the TopSAR azimuth shrink factor of the current burst and store it in `shrink_factor`.

        The shrink factor is $1 - k_{DC} / k_{FM}$, where $k_{FM}$ is the azimuth FM rate of the burst
        (`rel_burst`) and $k_{DC}$ the Doppler centroid rate derived from the AUX_INS azimuth steering rate,
        both evaluated at mid-range as in the IPF. It requires `range_t` and `rel_burst` to be set.

        Parameters
        ----------
        azimuth_time : PreciseDateTime, optional
            Azimuth time of the current block, used for GRD products to select the closest FM rate record.
            Default is None (first record).
        """

        range_filter = int(self.rel_burst is not None)
        range_times = self.range_t[::range_filter]

        # deramping matrix
        FM_rate, az_mid_time = extract_FR_info(self.metadata_dict, range_times, self.rel_burst, self.is_grd, self.swath_id,
                                               azimuth_time=azimuth_time)
        FM_rate_mid = FM_rate[range_times.size // 2]

        AR_vec = extract_AR_info(self.metadata_dict, az_mid_time, range_times, self.azimuth_steering_rate, orbit=self.orbit)
            
        # give back FM rate and DC rate evaluated at mid-range like IPF
        doppler_centroid_rate = AR_vec[range_times.size // 2]

        self.shrink_factor = 1 - doppler_centroid_rate / FM_rate_mid

    def set_range_values(self, range_t):
        """
        Set the slant range axis of the noise vector and derive the range-dependent quantities.

        Stores the slant range times in `range_t`, the slant range distances in `range_distances` and the range
        spreading loss correction $(R_{ref} / R)^4$ in `range_spread_loss`.

        Parameters
        ----------
        range_t : numpy.ndarray
            Slant range times of the noise vector samples [s].
        """

        self.range_t = range_t # [s]
        self.range_distances = self.range_t * speed_of_light / 2  # [m]
        self.range_spread_loss  = (R_REF / self.range_distances) ** 4

    def reconstruct_noise_norm_factor(self):
        """
        Reconstruct the IPF noise normalization factor and store it in `noise_norm_factor`.

        Reverse-engineered from the IPF. The azimuth contribution is obtained by interpolating the AUX_CAL azimuth
        antenna pattern on the azimuth FFT grid (angles converted to frequencies using the `shrink_factor`),
        extracting the overlapping looks with a Hamming window, and averaging over the looks the ratio between the
        noise processing gain and the window normalization factor. The range contribution accounts for the rounding
        of the range processing bandwidth to an integer number of samples (flat spectrum). The stored value is the
        product of the two.

        Raises
        ------
        ValueError
            If the azimuth or range processing bandwidth corresponds to zero samples, or if a window
            normalization factor is zero.

        Notes
        -----
        $$
        k_{norm} = k_{norm,az} \\cdot k_{norm,rg}, \\qquad
        k_{norm,az} = \\frac{1}{L} \\sum_{l=1}^{L} \\frac{G_l}{W_l}, \\qquad
        k_{norm,rg} = \\frac{N_{bw,rg}}{\\mathrm{round}(N_{bw,rg})}
        $$

        See the "Noise normalization factor" page of the algorithm documentation for details.
        """

        # 1. Constants and basic frequency parameters

        LAMBDA = speed_of_light / self.radar_frequency

        fft_length = find_fft_size(self.lines_per_burst)
        sampling_freq_az = 1.0 / self.input_azi_spacing
        delta_freq = sampling_freq_az / fft_length

        bw_in_samples = int(np.round(self.processing_bandwidth / delta_freq))
        if bw_in_samples == 0:
            raise ValueError("Zero samples in bandwith")

        # window length is given by the look bandwidth, as in IPF
        window_len = int(np.round(self.look_bandwidth / delta_freq))
        window = hamming(window_len, alpha=self.azi_window_coefficient)

        look_overlap = get_look_overlap_samples(
            window_len=window_len,
            bw_in_samples=bw_in_samples,
            number_of_looks=self.number_of_looks
        )

        look_start_indices = get_start_indices(
            look_overlap=look_overlap,
            window_len=window_len,
            in_line_len=fft_length,
            number_of_looks=self.number_of_looks
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

        data_spectra = db_to_linear(aap_data, is_amplitude=True)

        # 4. Window normalization

        window_norm_factor = calculate_window_norm_factor(
            data_spectra=data_spectra,
            in_line_len=fft_length,
            window=window,
            bw_in_samples=bw_in_samples,
            look_start_indices=look_start_indices,
            correct_spectra=self.apply_azi_ant_pattern
        )

        sum_window_sq_noise = update_window_noise_norm(
            data_spectra=data_spectra,
            window=window,
            look_start_indices=look_start_indices,
            in_line_len=fft_length,
            correct_spectra=self.apply_azi_ant_pattern
        )

        # 5. Noise normalization factor computation

        if np.any(window_norm_factor == 0.0):
            raise ValueError("Zero window normalization factor")

        noise_proc_gain = sum_window_sq_noise / bw_in_samples
        noise_norm_factor_all_looks = noise_proc_gain / window_norm_factor

        noise_azi_norm_factor = np.mean(noise_norm_factor_all_looks)

        # 6. Range noise normalization factor (flat spectrum in range, as in IPF):
        # window norm uses the exact bandwidth in samples, noise gain the rounded one

        rg_fft_length = find_fft_size(self.samples_per_burst if self.samples_per_burst > 0 else self.rg_dim())
        rg_delta_freq = self.sampling_freq_afterdecimation() / rg_fft_length
        rg_bw_samples = self.rg_processing_bandwidth / rg_delta_freq
        if int(np.round(rg_bw_samples)) == 0:
            raise ValueError("Zero samples in range bandwith")

        noise_rng_norm_factor = rg_bw_samples / int(np.round(rg_bw_samples))

        denoise_logger.debug(f"Noise azimuth norm factor: {noise_azi_norm_factor}, range norm factor: {noise_rng_norm_factor}")

        self.noise_norm_factor = noise_azi_norm_factor * noise_rng_norm_factor

        denoise_logger.debug(f"Noise norm factor: {self.noise_norm_factor}")
