# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Parse and extract geophysical parameters from Sentinel-1 L1 annotation XML files.

Provides readers for noise power, antenna patterns (elevation pattern, incidence angles), PG correction, azimuth FM
rate and orbit state vectors, as well as helpers to build the slant range time axis and the orbit trajectory, and to
convert the azimuth antenna steering rate into a Doppler rate.
"""

import numpy as np
import xml.etree.ElementTree as ET
import xmltodict
from scipy.constants import speed_of_light
from perseo_core.timing import PreciseDateTime
from perseo_core.geometry.navigation import CubicSplineTrajectory

from s1_denoise.utils import ground2slant, ensure_list, get_range_boundaries
from s1_denoise.logger import denoise_logger

def read_metadata_from_safe_annotation(annotation):
    """
    Convert a parsed L1 annotation XML tree into a nested dictionary.

    Parameters
    ----------
    annotation : xml.etree.ElementTree.ElementTree
        Parsed annotation XML of one channel.

    Returns
    -------
    dict
        Nested dictionary produced by ``xmltodict``, rooted at the ``product`` key.
    """

    xml_data = annotation.getroot()
    xmlstr = ET.tostring(xml_data, encoding='utf-8', method='xml')

    return dict(xmltodict.parse(xmlstr))

def create_slant_range_times(metadata_dict, subswath = None, az_t0 = None):
    """
    Create the two-way slant range time axis of the image range samples.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    subswath : str, optional
        Subswath identifier (e.g. ``"IW1"``), to be given for GRD products only. Default is None (SLC case).
    az_t0 : PreciseDateTime, optional
        Azimuth time used to select the swath bounds and the ground-to-slant range polynomial. Required when
        `subswath` is given. Default is None.

    Returns
    -------
    numpy.ndarray
        Two-way slant range times [s], one per range sample.

    Notes
    -----
    In the SLC case, ``numberOfSamples`` times are generated starting from ``slantRangeTime`` with a step of
    ``2 * rangePixelSpacing / c``. In the GRD case, the ground range pixels between the first and last range samples of
    the subswath (from the swath merging bounds valid at `az_t0`) are converted to ground range distances using
    ``rangePixelSpacing``, then to slant range with the GRSR polynomial closest in azimuth to `az_t0`, and finally to
    two-way slant range times.
    """

    delta_t = 2 * (float(metadata_dict['product']['imageAnnotation']['imageInformation']['rangePixelSpacing'])) / speed_of_light
    start_sr = float(metadata_dict['product']['imageAnnotation']['imageInformation']['slantRangeTime'])
    samples_num = int(metadata_dict['product']['imageAnnotation']['imageInformation']['numberOfSamples'])
    
    # SLC case
    if subswath is None:
        return np.arange(samples_num) * delta_t + start_sr # [s]
    # GRD case
    else:
        swath_merges = ensure_list(metadata_dict['product']['swathMerging']['swathMergeList']['swathMerge'])
        cc = metadata_dict['product']['coordinateConversion']['coordinateConversionList']['coordinateConversion']

        start_sample, end_sample = get_range_boundaries(swath_merges, subswath, az_t0)
        
        # GRD pixel indices for the subswath
        indices = np.arange(start_sample, end_sample + 1)

        # ground range times [s]
        t_grd = indices * delta_t

        # ground range [m]
        gr_m = t_grd * speed_of_light / 2

        # slant range [m]
        sr_m = ground2slant(start_az_time = az_t0.isoformat(sep="T", timespec="microseconds").rstrip("Z"), 
                            gr_array = gr_m, 
                            coordinate_conversions = cc)

        # slant range times [s]
        t_slant = sr_m * 2 / speed_of_light

        return t_slant

def read_pg_correction_list(annotation, swath_id):
    """
    Read the PG product amplitudes annotated in the replica information of a swath.

    Parameters
    ----------
    annotation : xml.etree.ElementTree.ElementTree
        Parsed annotation XML of one channel.
    swath_id : str
        Swath identifier (e.g. ``"IW1"``).

    Returns
    -------
    pg_correction : numpy.ndarray
        ``pgProductAmplitude`` values, one per replica record.
    az_t : numpy.ndarray
        Azimuth times of the replica records, as PreciseDateTime objects.
    """

    pg_correction = []
    az_t = []

    data = annotation.findall('generalAnnotation/replicaInformationList/replicaInformation/swath')
    for ind, element in enumerate(data):
        if element.text == swath_id:

            pg_correction_list = annotation.findall('generalAnnotation/replicaInformationList/replicaInformation[{}]/replicaList/replica/pgProductAmplitude'.format(ind+1))
            az_t_list = annotation.findall('generalAnnotation/replicaInformationList/replicaInformation[{}]/replicaList/replica/azimuthTime'.format(ind+1))

            for t_ind, t in enumerate(az_t_list):
                pg_correction.append(float(pg_correction_list[t_ind].text))
                az_t.append(t.text)
    
        
    return np.array(pg_correction), np.array(convert_az_times(az_t))

def read_noise_power_correction_from_annotation(metadata_dict, swath_id):
    """
    Read the noise power annotated in the L1 product for a swath, discarding biased rank echo measurements.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    swath_id : str
        Swath identifier (e.g. ``"IW1"``).

    Returns
    -------
    final_noise_pwr : numpy.ndarray
        Retained ``noisePowerCorrectionFactor`` values (linear scale).
    final_times : numpy.ndarray
        Azimuth times of the retained values, as PreciseDateTime objects.

    Notes
    -----
    The same mission-specific selection applied to L0N rank echoes (see
    `s1_denoise.l0_noise_power_calculation.filter_rank_echoes`) is used. For S1A/B: IW1 keeps the first value, the
    odd-indexed values and the last value; IW2/IW3 keep the even-indexed values and the last value; EW1 drops the last
    value. For the other missions, the last EW2 value is dropped. All other swaths are kept unchanged.
    """
    
    data = metadata_dict['product']['generalAnnotation']['noiseList']['noise']
    mission_id = metadata_dict['product']['adsHeader']['missionId']

    filtered = [el for el in data if el['swath'] == swath_id]
    noise_power_correction = np.array([float(el['noisePowerCorrectionFactor']) for el in filtered])
    az_t = np.array([el['azimuthTime'] for el in filtered])

    if mission_id in ["S1A", "S1B"]:
        if swath_id == "IW1":
            final_noise_pwr = np.concatenate([[noise_power_correction[0]], noise_power_correction[1::2], [noise_power_correction[-1]]])
            final_times = np.concatenate([[az_t[0]], az_t[1::2], [az_t[-1]]])
        elif swath_id in ["IW2", "IW3"]:
            final_noise_pwr = np.concatenate([noise_power_correction[0::2], [noise_power_correction[-1]]])
            final_times = np.concatenate([az_t[0::2], [az_t[-1]]])
        elif swath_id == "EW1":
            final_noise_pwr = noise_power_correction[:-1]
            final_times = az_t[:-1]
        else:
            final_times = az_t
            final_noise_pwr = noise_power_correction
    else:
        if swath_id == "EW2":
            final_times = az_t[:-1]
            final_noise_pwr = noise_power_correction[:-1]
        else:
            final_times = az_t
            final_noise_pwr = noise_power_correction
    
    return final_noise_pwr, convert_az_times(final_times)

def read_antenna_patterns(metadata_dict, swath_id):
    """
    Parse the antenna pattern records of a swath.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    swath_id : str
        Swath identifier (e.g. ``"IW1"``).

    Returns
    -------
    dict
        Dictionary with one element per antenna pattern record of the swath, under the keys:

        - ``"azimuth_times"``: numpy.ndarray of PreciseDateTime record azimuth times;
        - ``"elevation_pattern_gain"``: list of numpy.ndarray, magnitude of the annotated complex elevation pattern;
        - ``"incidence_angles"``: list of numpy.ndarray, incidence angles [deg];
        - ``"slant_range_t"``: list of numpy.ndarray, slant range times [s] at which the above are given.
    """

    data = ensure_list(metadata_dict['product']['antennaPattern']['antennaPatternList']['antennaPattern'])

    antenna_patterns = {
        "azimuth_times": [],
        "elevation_pattern_gain": [],
        "incidence_angles": [],
        "slant_range_t": []
    }

    for element in data:
        if element['swath'] == swath_id:

            antenna_patterns["azimuth_times"].append(PreciseDateTime.from_utc_string(element['azimuthTime']))

            elevation = np.array(element['elevationPattern']['#text'].split(' '), dtype=float)
            antenna_patterns["elevation_pattern_gain"].append(abs(elevation[::2] + elevation[1::2] * 1j))

            antenna_patterns["incidence_angles"].append(np.array(element['incidenceAngle']['#text'].split(' '), dtype=float))
            antenna_patterns["slant_range_t"].append(np.array(element['slantRangeTime']['#text'].split(' '), dtype=float))

    antenna_patterns["azimuth_times"] = np.array(antenna_patterns["azimuth_times"])

    return antenna_patterns

def closest_antenna_pattern_index(antenna_patterns, az_t):
    """
    Return the index of the antenna pattern record closest in azimuth time to `az_t`.

    Parameters
    ----------
    antenna_patterns : dict
        Antenna patterns, as returned by `read_antenna_patterns`.
    az_t : PreciseDateTime
        Reference azimuth time.

    Returns
    -------
    int
        Index of the closest record.
    """

    return np.argmin(abs(antenna_patterns["azimuth_times"] - az_t))

def extract_AR_info(metadata_dict, az_t, range_times, az_steering_rate, orbit = None):
    """
    Compute the Doppler rate induced by the azimuth antenna steering, constant over the range axis.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    az_t : PreciseDateTime
        Azimuth time at which the satellite velocity is evaluated.
    range_times : numpy.ndarray
        Slant range times [s]; only their number is used.
    az_steering_rate : float
        Azimuth antenna steering rate of the subswath [rad/s], from AUX_INS.
    orbit : CubicSplineTrajectory, optional
        Orbit trajectory, to avoid rebuilding it from the annotation at each call. Default is None (built with
        `build_orbit`).

    Returns
    -------
    numpy.ndarray
        Steering Doppler rate ``2 * v / lambda * az_steering_rate`` [Hz/s], repeated for each range time.
    """

    # Derive AR info
    AR_vec = angles2freq_conversion(metadata_dict, az_t, orbit) * az_steering_rate * np.ones(len(range_times))

    return AR_vec

def extract_FR_info(metadata_dict, range_times, burst_idx = None, is_grd: bool = False, subswath = None):
    """
    Evaluate the azimuth FM rate over the range axis.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    range_times : numpy.ndarray
        Two-way slant range times [s] at which the FM rate polynomial is evaluated.
    burst_idx : int, optional
        Index of the azimuth FM rate record to use (SLC only). Default is None (first record).
    is_grd : bool, optional
        Whether the product is GRD. Default is False.
    subswath : str, optional
        Subswath identifier, required when `is_grd` is True. Default is None.

    Returns
    -------
    FR : numpy.ndarray
        Azimuth FM rate [Hz/s] at each range time.
    az_mid_time : PreciseDateTime
        Azimuth time of the FM rate record used (time-shifted for GRD products, see `extract_FMrate`).

    Notes
    -----
    The first record returned by `extract_FMrate` is used. For GRD products `burst_idx` is ignored, so the first
    annotated record is always used.
    """
    
    # Extract FMrate info
    burst_times, FM_poly_coeffs = extract_FMrate(metadata_dict = metadata_dict, 
                                                 burst_index = burst_idx,
                                                 is_grd = is_grd,
                                                 subswath = subswath)

    FR = FM_poly_coeffs[0].evaluate(range_times)  # burst_idx is handled inside extract_FMrate
    az_mid_time = burst_times[0]
    
    return FR, az_mid_time

def build_orbit(metadata_dict):
    """
    Build the orbit trajectory from the annotated orbit times, positions and velocities.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.

    Returns
    -------
    CubicSplineTrajectory
        Orbit trajectory interpolating the annotated state vectors.
    """

    date_time_vec, positions, velocities = extract_info_orbita(metadata_dict)

    return CubicSplineTrajectory(times=date_time_vec, positions=positions, velocities=velocities)

def angles2freq_conversion(metadata_dict, az_t, orbit = None):
    """
    Return the factor converting an azimuth angular rate into a Doppler rate.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    az_t : PreciseDateTime
        Azimuth time at which the satellite velocity is evaluated.
    orbit : CubicSplineTrajectory, optional
        Orbit trajectory. Default is None (built with `build_orbit`).

    Returns
    -------
    float
        Conversion factor ``2 * v / lambda`` [Hz/rad], with ``v`` the satellite velocity norm at `az_t` and ``lambda``
        the radar wavelength derived from ``radarFrequency``.
    """

    fc_hz = float(metadata_dict['product']['generalAnnotation']['productInformation']['radarFrequency'])

    if orbit is None:
        orbit = build_orbit(metadata_dict)
    velocity = np.linalg.norm(orbit.velocity(az_t))
    
    return 2 * velocity / (speed_of_light / fc_hz)

def convert_az_times(times):
    """
    Convert a sequence of UTC time strings into PreciseDateTime objects.

    Parameters
    ----------
    times : list of str
        UTC time strings, as annotated.

    Returns
    -------
    numpy.ndarray
        Array of PreciseDateTime objects.
    """
    
    date_time_vec = np.ndarray((len(times),), dtype=PreciseDateTime)
    for idx, date in enumerate(times):
        date_time_vec[idx] = PreciseDateTime.from_utc_string(date)

    return date_time_vec

def extract_FMrate(metadata_dict, burst_index=None, is_grd: bool = False, subswath = None):
    """
    Read the azimuth FM rate records from the annotation.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.
    burst_index : int, optional
        If given (and `is_grd` is False), only the record at this index is returned. Default is None (all records).
    is_grd : bool, optional
        Whether the product is GRD. Default is False.
    subswath : str, optional
        Subswath identifier (e.g. ``"IW2"``), required when `is_grd` is True. Default is None.

    Returns
    -------
    burst_times_vec : numpy.ndarray
        Azimuth times of the records, as PreciseDateTime objects.
    polynomials : list of PolynomialCoefficients2
        FM rate polynomials [Hz/s] in two-way slant range time, one per record.

    Raises
    ------
    KeyError
        If `is_grd` is True and `subswath` is not an IW/EW subswath.

    Notes
    -----
    The ``t0`` of the first selected record is used as reference time for all polynomials. For GRD products, the record
    times are shifted by ``k * (t[1] - t[0]) / n_swaths``, where ``k`` is the zero-based subswath index and ``n_swaths``
    the number of swaths in the swath merge list; no shift is applied if only one record is present.
    """

    fmrate_list = ensure_list(metadata_dict['product']['generalAnnotation']['azimuthFmRateList']['azimuthFmRate'])
    tot_swath_number = int(metadata_dict['product']['swathMerging']['swathMergeList']['@count'])

    # Handle burst_index if provided
    if burst_index is not None and not is_grd:
        fmrate_list = [fmrate_list[burst_index]]

    # Extract t0
    t0 = float(fmrate_list[0]['t0'])

    times = []
    polynomials = []

    for element in fmrate_list:
        times.append(PreciseDateTime.from_utc_string(element['azimuthTime']))

        poly_values = element['azimuthFmRatePolynomial']['#text'].split(' ')
        coefficients = PolynomialCoefficients2(
            x0=t0,
            termine_noto=float(poly_values[0]),
            coeff_lineare=float(poly_values[1]),
            coeff_quadratico=float(poly_values[2])
        )
        polynomials.append(coefficients)

    # Convert times to numpy array
    burst_times_vec = np.array(times, dtype=object)
    delta = 0

    if is_grd:
        swath_in_swaths = {
            "IW1": 0, 
            "EW1": 0,
            "IW2": 1, 
            "EW2": 1,
            "IW3": 2, 
            "EW3": 2,
            "EW4": 3, 
            "EW5": 4,
        }

        current_swath = swath_in_swaths[subswath]
        if burst_times_vec.size > 1:
            delta = current_swath * (burst_times_vec[1] - burst_times_vec[0]) / tot_swath_number
        else:
            denoise_logger.warning(f"Only one azimuth FM rate record found: no time shift applied for subswath {subswath}")

    return burst_times_vec + delta, polynomials

class PolynomialCoefficients2():
    """
    Second-order polynomial in ``(x - x0)``.

    Parameters
    ----------
    x0 : float
        Reference abscissa.
    termine_noto : float
        Constant term.
    coeff_lineare : float
        Linear coefficient.
    coeff_quadratico : float
        Quadratic coefficient.

    Attributes
    ----------
    x0 : float
        Reference abscissa.
    termine_noto : float
        Constant term.
    coeff_lineare : float
        Linear coefficient.
    coeff_quadratico : float
        Quadratic coefficient.
    """
    x0: float
    termine_noto: float
    coeff_lineare: float
    coeff_quadratico: float

    def __init__(self, x0, termine_noto, coeff_lineare, coeff_quadratico):
        self.x0 = x0
        self.termine_noto = termine_noto
        self.coeff_lineare = coeff_lineare
        self.coeff_quadratico = coeff_quadratico

    def evaluate(self, x):
        """
        Evaluate the polynomial.

        Parameters
        ----------
        x : float or numpy.ndarray
            Abscissa value(s).

        Returns
        -------
        float or numpy.ndarray
            Polynomial value(s) at `x`.
        """
        return self.termine_noto + self.coeff_lineare * (x - self.x0) + self.coeff_quadratico * ((x - self.x0) **2)
   

def extract_info_orbita(metadata_dict):
    """
    Read the annotated orbit state vectors.

    Parameters
    ----------
    metadata_dict : dict
        Annotation dictionary, as returned by `read_metadata_from_safe_annotation`.

    Returns
    -------
    times : numpy.ndarray
        Orbit state vector times, as PreciseDateTime objects, shape ``(n,)``.
    positions : numpy.ndarray
        Positions [m], shape ``(n, 3)``.
    velocities : numpy.ndarray
        Velocities [m/s], shape ``(n, 3)``.
    """

    data_orbita = metadata_dict['product']['generalAnnotation']['orbitList']['orbit']
    
    times, x, y, z, vx, vy, vz = [], [], [], [], [], [], []
    for element  in data_orbita:
        times.append(element['time'])
        x.append(float(element['position']['x']))
        y.append(float(element['position']['y']))
        z.append(float(element['position']['z']))
        vx.append(float(element['velocity']['x']))
        vy.append(float(element['velocity']['y']))
        vz.append(float(element['velocity']['z']))

    # Convert times
    date_time_vec = np.ndarray((len(times),), dtype=PreciseDateTime)
    for idx, date in enumerate(times):
        date_time_vec[idx] = PreciseDateTime.from_utc_string(date)

    return np.array(date_time_vec), np.c_[x, y, z], np.c_[vx, vy, vz]
