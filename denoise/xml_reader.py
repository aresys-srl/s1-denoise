# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for parsing and extracting geophysical parameters from Sentinel-1 L1
annotation XML files.

Provides readers for noise power correction, elevation pattern, incidence angles,
PG correction, FM rate, Doppler centroid, and orbit state vectors, as well as
helpers for slant range time array construction, azimuth deramping matrix
computation, and antenna rate to frequency conversion.
"""

import numpy as np
import xml.etree.ElementTree as ET
import xmltodict
from scipy.constants import speed_of_light

from arepytools.timing.precisedatetime import PreciseDateTime
from arepytools.geometry.generalsarorbit import GeneralSarOrbit

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
    noise_power_correction = []
    az_t = []
    for element in data:
        if element['swath'] == swath_id:
            noise_power_correction.append(float(element['noisePowerCorrectionFactor']))
            az_t.append(element['azimuthTime'])

    if mission_id == "S1A":
        if swath_id == "IW1":
            # include preamble
            final_noise_pwr = [noise_power_correction[0]]
            final_times = [az_t[0]]

            final_noise_pwr += noise_power_correction[1::2]
            final_times += az_t[1::2]

            # include postamble
            final_noise_pwr.append(noise_power_correction[-1])
            final_times.append(az_t[-1])
        elif swath_id in ["IW2", "IW3"]:
            final_noise_pwr = noise_power_correction[0::2]
            final_noise_pwr.append(noise_power_correction[-1])
            final_times = az_t[0::2]
            final_times.append(az_t[-1])
        else:
            final_times = az_t
            final_noise_pwr = noise_power_correction
    else:
        # on S1C, EW2 annotation files have an extra entry
        if swath_id == "EW2":
            final_times = az_t[:-1]
            final_noise_pwr = noise_power_correction[:-1]
        else:
            final_times = az_t
            final_noise_pwr = noise_power_correction
    
    return np.array(final_noise_pwr), np.array(convert_az_times(final_times))

def read_elevation_pattern(metadata_dict, swath_id, az_t):
    "Create two lists of elevation pattern gain arrays and slant range time arrays"
    
    data = metadata_dict['product']['antennaPattern']['antennaPatternList']['antennaPattern']
    
    elevation_pattern_gain = []
    slant_range_t = []
    azimuth_times = []

    for element in data:
        if element['swath'] == swath_id:

            azimuth_times.append(PreciseDateTime.from_utc_string(element['azimuthTime']))

            elevation_real = [float(i) for i in element['elevationPattern']['#text'].split(' ')][::2]
            elevation_imag = [float(i) for i in element['elevationPattern']['#text'].split(' ')][1::2]
            elevation_pattern_gain.append(abs(np.array(elevation_real) + np.array(elevation_imag) * 1j))

            range_t = [float(i) for i in element['slantRangeTime']['#text'].split(' ')]
            slant_range_t.append(range_t)
    
    az_correspondent_time = np.argmin(abs(np.array(azimuth_times) - az_t))

    return elevation_pattern_gain[az_correspondent_time], slant_range_t[az_correspondent_time]

def read_incidence_angles(metadata_dict, swath_id, az_t):
    "Create a list of incidence angles array"
    
    data = metadata_dict['product']['antennaPattern']['antennaPatternList']['antennaPattern']
    
    incidence_angles = []
    slant_range_t = []
    azimuth_times = []
    
    for element  in data:
        if element['swath'] == swath_id:

            azimuth_times.append(PreciseDateTime.from_utc_string(element['azimuthTime']))
            inc_ang = [float(i) for i in element['incidenceAngle']['#text'].split(' ')]
            incidence_angles.append(inc_ang)

            range_t = [float(i) for i in element['slantRangeTime']['#text'].split(' ')]
            slant_range_t.append(range_t)
    
    az_correspondent_time = np.argmin(abs(np.array(azimuth_times) - az_t))
    return incidence_angles[az_correspondent_time], slant_range_t[az_correspondent_time]

def compute_deramping_function(metadata_dict, N_azimuth, range_filter=1, burst_idx = None, doppler_centroid = None):
    """Returns a np.ndarray containing the complex values for the deramping function (azimuth times, range times)"""

    azimuth_times = create_burst_az_times(metadata_dict, N_azimuth)
    range_times = create_slant_range_times(metadata_dict)[::range_filter]

    # Derive DC info
    if doppler_centroid is None:
        DC_poly_coeffs = extract_doppler_centroid(metadata_dict, burst_idx)
        DC = DC_poly_coeffs.evaluate(range_times)
    else:
        DC = np.ones(range_times.size) * doppler_centroid

    FR, Az_mid_time = extract_FR_info(metadata_dict, range_times, burst_idx)

    AR, Az_steering_rate = extract_AR_info(metadata_dict, Az_mid_time, range_times)
    
    K_TU = - FR * AR / (AR - FR)

    deramping_matrix = np.zeros((N_azimuth, len(range_times)))
    denoise_logger.debug('Start deramping matrix computation')

    deramping_matrix = (np.pi * K_TU * (DC / FR + azimuth_times.reshape((azimuth_times.size, 1)))**2 
                        - 2*np.pi * (DC / FR + azimuth_times.reshape((azimuth_times.size, 1))) * DC)
    
    complex_deramping_matrix = np.exp(-1j * deramping_matrix)

    return complex_deramping_matrix, AR[range_times.size // 2], FR[range_times.size // 2], Az_steering_rate

def create_burst_az_times(metadata_dict, N):
    """Returns a np.array of relative azimuth times, centered in 0 with delta_t = azimuthTimeInterval"""

    delta_azimuth = float(metadata_dict['product']['imageAnnotation']['imageInformation']['azimuthTimeInterval'])
    az_times = (np.linspace(0, N-1, N) - (N-1)/2) * delta_azimuth
    return az_times

def extract_AR_info(metadata_dict, az_t, range_times, subswath = None):

    # fixed quantities in [rad], do not change 
    ANTENNA_RATES = {
        "IW1": 0.02775717,
        "IW2": 0.01710184,
        "IW3": 0.02438994,
        "EW1": 0.04172900,
        "EW2": 0.04906998,
        "EW3": 0.04129791,
        "EW4": 0.04385479,
        "EW5": 0.03705082,
    }

    # Derive AR info
    az_steering_rate = ANTENNA_RATES[subswath]
    AR_vec = angles2freq_conversion(metadata_dict, az_t) * az_steering_rate * np.ones(len(range_times))

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

def angles2freq_conversion(metadata_dict, az_t):

    fc_hz = float(metadata_dict['product']['generalAnnotation']['productInformation']['radarFrequency'])

    date_time_vec, positions = extract_info_orbita(metadata_dict)
    # Generate the orbit object from times and positions
    GSO_orbit = GeneralSarOrbit(date_time_vec, positions.ravel(order='F'))
    velocity = np.linalg.norm(GSO_orbit.get_velocity(az_t))
    
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
        delta = current_swath * (burst_times_vec[1] - burst_times_vec[0]) / tot_swath_number

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
   

def extract_doppler_centroid(metadata_dict, burst_idx):
    """Returns a Polinomial_coefficients_2 containing the doppler centroid coefficients"""

    doppler_data = metadata_dict['product']['dopplerCentroid']['dcEstimateList']['dcEstimate'][burst_idx]
    t0 = float(doppler_data['t0'])

    poly = doppler_data['dataDcPolynomial']['#text'].split(' ')

    doppler_coeffs = PolynomialCoefficients2(x0 = t0, termine_noto = float(poly[0]), coeff_lineare = float(poly[1]), coeff_quadratico = float(poly[2]))
    
    return doppler_coeffs

def extract_info_orbita(metadata_dict):
    """Rerurns two np.arrays:  the first containing orbit times [n], the second containing orbit positions [3, n]"""

    data_orbita = metadata_dict['product']['generalAnnotation']['orbitList']['orbit']
    
    times, x, y, z = [], [], [], []
    for element  in data_orbita:
        times.append(element['time'])
        x.append(float(element['position']['x']))
        y.append(float(element['position']['y']))
        z.append(float(element['position']['z']))

    # Convert times
    date_time_vec = np.ndarray((len(times),), dtype=PreciseDateTime)
    for idx, date in enumerate(times):
        date_time_vec[idx] = PreciseDateTime.from_utc_string(date)

    return np.array(date_time_vec), np.array([x, y, z])
