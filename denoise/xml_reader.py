# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for parsing and extracting geophysical parameters from Sentinel-1 L1
annotation XML files.

Provides readers for noise power correction, antenna patterns (elevation pattern,
incidence angles), PG correction, FM rate, and orbit state vectors, as well as
helpers for slant range time array construction, orbit construction, and antenna
rate to frequency conversion.
"""

import numpy as np
import xml.etree.ElementTree as ET
import xmltodict
from scipy.constants import speed_of_light
from perseo_core.timing import PreciseDateTime
from perseo_core.geometry.navigation import CubicSplineTrajectory

from denoise.utils import ground2slant, ensure_list, get_range_boundaries
from denoise.logger import denoise_logger

def read_metadata_from_safe_annotation(annotation):

    xml_data = annotation.getroot()
    xmlstr = ET.tostring(xml_data, encoding='utf-8', method='xml')

    return dict(xmltodict.parse(xmlstr))

def create_slant_range_times(metadata_dict, subswath = None, az_t0 = None):
    """Create an array of slant range times starting from the acquisition start time"""

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
    "Create two np.array of pg correction factor every 6 s"

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
    "Create a np.array of noise power correction factor every 3 s"
    
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
    """Parse once the antenna pattern records of the swath: returns a dict of lists (azimuth times,
    elevation pattern gains, incidence angles and slant range times), one element per record"""

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
    """Index of the antenna pattern record closest in azimuth time to az_t"""

    return np.argmin(abs(antenna_patterns["azimuth_times"] - az_t))

def extract_AR_info(metadata_dict, az_t, range_times, az_steering_rate, orbit = None):
    """az_steering_rate is the azimuth steering rate [rad/s] of the subswath, from AUX_INS.
    orbit can be passed to avoid rebuilding it from the annotation at each call"""

    # Derive AR info
    AR_vec = angles2freq_conversion(metadata_dict, az_t, orbit) * az_steering_rate * np.ones(len(range_times))

    return AR_vec

def extract_FR_info(metadata_dict, range_times, burst_idx = None, is_grd: bool = False, subswath = None):
    
    # Extract FMrate info
    burst_times, FM_poly_coeffs = extract_FMrate(metadata_dict = metadata_dict, 
                                                 burst_index = burst_idx,
                                                 is_grd = is_grd,
                                                 subswath = subswath)

    FR = FM_poly_coeffs[0].evaluate(range_times)  # burst_idx is handled inside extract_FMrate
    az_mid_time = burst_times[0]
    
    return FR, az_mid_time

def build_orbit(metadata_dict):
    """Generate the orbit object from annotated orbit times, positions and velocities"""

    date_time_vec, positions, velocities = extract_info_orbita(metadata_dict)

    return CubicSplineTrajectory(times=date_time_vec, positions=positions, velocities=velocities)

def angles2freq_conversion(metadata_dict, az_t, orbit = None):

    fc_hz = float(metadata_dict['product']['generalAnnotation']['productInformation']['radarFrequency'])

    if orbit is None:
        orbit = build_orbit(metadata_dict)
    velocity = np.linalg.norm(orbit.velocity(az_t))
    
    return 2 * velocity / (speed_of_light / fc_hz)

def convert_az_times(times):
    """Converst a list of azimuth times in PreciseDateTime format"""
    
    date_time_vec = np.ndarray((len(times),), dtype=PreciseDateTime)
    for idx, date in enumerate(times):
        date_time_vec[idx] = PreciseDateTime.from_utc_string(date)

    return date_time_vec

def extract_FMrate(metadata_dict, burst_index=None, is_grd: bool = False, subswath = None):
    """Returns one np.array containing azimuth times [n], and a list of [Polynomial_coefficients_2]"""

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
        return self.termine_noto + self.coeff_lineare * (x - self.x0) + self.coeff_quadratico * ((x - self.x0) **2)
   

def extract_info_orbita(metadata_dict):
    """Rerurns two np.arrays:  the first containing orbit times [n], the second containing orbit positions [3, n]"""

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
