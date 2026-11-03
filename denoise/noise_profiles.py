# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Core module orchestrating the generation of simulated thermal noise profiles for
Sentinel-1 SLC and GRD products.

Iterates over all polarisation/subswath channels of a SAFE product, applies
thermal noise modelling via the ThermalNoise class, and returns structured output
noise vectors. Optionally incorporates noise power correction derived from the
associated L0N file.
"""

import sys
from pathlib import Path
import numpy as np
from tabulate import tabulate

from perseo_core.timing import PreciseDateTime

# Local imports
from denoise.logger import denoise_logger
from denoise.s1safe import S1Safe
from denoise.xml_reader import create_slant_range_times, read_metadata_from_safe_annotation
from denoise.thermal_noise import ThermalNoise
from denoise.auxiliary_files import AuxiliaryFiles
from denoise.output import OutputNoiseVectors_SLC, OutputNoiseVectors_GRD
from denoise.l0_noise_power_calculation import get_noise_pwr_from_l0n
from denoise.utils import ensure_list, slant2ground

ERROR_THRESHOLD = 0.1

def get_valid_matrix(matrix, first_valid_data, last_valid_data):
    """Returns a matrix containing only the valid data"""
 
    row_n = len(first_valid_data[first_valid_data != -1])
    col_n =  last_valid_data[last_valid_data != -1][0] - first_valid_data[first_valid_data != -1][0] + 1
    result_matrix = np.zeros((row_n, col_n))
 
    ind = 0
    for row in range(matrix.shape[0]):
       
        if first_valid_data[row] != -1:
            result_matrix[ind, :] = matrix[row, first_valid_data[row] : last_valid_data[row] +1]
            ind += 1
   
    return result_matrix

def print_results(error_list, headers) -> None:

    formatted_headers = headers[1:]
    formatted_values = [row[1:] for row in error_list]

    denoise_logger.debug("Mean error on bursts vs orbit time:\n"+tabulate(formatted_values, headers=formatted_headers, tablefmt="simple"))

def print_exec_time(elapsed_seconds: float) -> None:

    hours = int(elapsed_seconds // 3600)
    remaining = elapsed_seconds % 3600
    minutes = int(remaining // 60)
    seconds = remaining % 60

    time_parts = []
    if hours > 0:
        time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0 or not time_parts:
        time_parts.append(f"{seconds:.3f} seconds")

    denoise_logger.debug("Execution time: %s", ", ".join(time_parts))

def slc_processing(safe, aux_files, dn_value, noise_power_correction):

    simulated_noises = []

    # loop on annotation files
    for pol, subswath in safe._channels:

        channel = safe.channel(pol, subswath)
        denoise_logger.info(f"Processing bursts from polarization {pol}, subswath {subswath}")        

        metadata_dict = read_metadata_from_safe_annotation(channel.annotation)
        ads_header = metadata_dict['product']['adsHeader']
        lines_per_burst = int(metadata_dict['product']['swathTiming']['linesPerBurst'])
        az_dt = float(metadata_dict['product']['imageAnnotation']['imageInformation']['azimuthTimeInterval'])

        thermal_noise = ThermalNoise(metadata_dict=metadata_dict, 
                                     channel=channel, 
                                     aux_info=aux_files,
                                     product_id=safe.path.stem[4:13],
                                     noise_power_from_l0=noise_power_correction)

        sim_noises = {}
        geo_grid_info = metadata_dict['product']['geolocationGrid']

        for rel_burst in range(channel.num_bursts):

            denoise_logger.debug(f"Generating noise profiles for burst {rel_burst+1} ({pol}, {subswath})")
        
            az_t0 = PreciseDateTime.from_utc_string((channel.annotation.find('antennaPattern/antennaPatternList/antennaPattern[{}]/azimuthTime'.format(rel_burst+1)).text))

            range_t = create_slant_range_times(metadata_dict)
            az_central_time = az_t0 + az_dt * lines_per_burst/2
            
            # create simulated noise
            thermal_noise.set_range_values(range_t)
            thermal_noise.rel_burst = rel_burst
            thermal_noise.get_shrink_factor()
            
            sim_noise, incidence_angles = thermal_noise.compute_simulated_noise(az_central_time)

            start_az_time = az_t0.isoformat(sep="T", timespec="microseconds").rstrip("Z")

            sim_noises.update({
                (rel_burst, start_az_time): (sim_noise, geo_grid_info, incidence_angles, dn_value)
            })
        
        # Append it
        simulated_noises.append(OutputNoiseVectors_SLC(adsHeader=ads_header, noiseRangeVectors=sim_noises))

    return simulated_noises

def grd_processing(safe, aux_files, dn_value, noise_power_correction):

    simulated_noises = []

    for pol, swath in safe._channels:

        channel = safe.channel(pol, swath)
        denoise_logger.info(f"Processing bursts from polarization {pol}, subswath {swath}")        

        metadata_dict = read_metadata_from_safe_annotation(channel.annotation)
        ads_header = metadata_dict['product']['adsHeader']

        count_noise_list = int(channel.annotation.find('generalAnnotation/noiseList').attrib['count'])
        antenna_patterns = channel.annotation.findall('antennaPattern/antennaPatternList/antennaPattern')
        tot_noise_vectors_out = int(channel.annotation.find('antennaPattern/antennaPatternList').attrib['count'])
        swath_merging_info = ensure_list(metadata_dict['product']['swathMerging']['swathMergeList']['swathMerge'])

        # for slant to range conversion
        raw_cc = metadata_dict['product']['coordinateConversion']['coordinateConversionList']['coordinateConversion']
        coordinate_conversions = []
        for cc in raw_cc:
            coordinate_conversions.append({
                "azimuthTime": cc["azimuthTime"],
                "sr0": float(cc["sr0"]),
                "gr0": float(cc["gr0"]),  # adjust if needed
                "srgrCoeffs": [float(x) for x in cc["srgrCoefficients"]["#text"].split()]
            })
        
        # separate noise by subswath
        noise_subswaths = []
        for idx in range(1, count_noise_list+1):
            tmp_subswath = channel.annotation.find('generalAnnotation/noiseList/noise[{}]/swath'.format(idx)).text
            if tmp_subswath not in noise_subswaths:
                noise_subswaths.append(tmp_subswath)

        sim_noises = {}

        for subswath in noise_subswaths:

            thermal_noise = ThermalNoise(metadata_dict=metadata_dict, 
                                         channel=channel, 
                                         aux_info=aux_files, 
                                         product_id=safe.path.stem[4:13],
                                         noise_power_from_l0=noise_power_correction,
                                         subswath=subswath)

            subswath_antenna_patterns = [
                ap for ap in antenna_patterns
                if ap.findtext('swath') == subswath
            ]
                    
            for index in range(len(subswath_antenna_patterns)):

                denoise_logger.info(f"Generating noise profiles for block {index+1} ({pol}, {subswath})")
                            
                az_t0 = PreciseDateTime.from_utc_string(subswath_antenna_patterns[index].findtext('azimuthTime'))

                range_t = create_slant_range_times(metadata_dict, subswath=subswath, az_t0=az_t0)
                
                # create simulated noise
                thermal_noise.set_range_values(range_t)
                thermal_noise.rel_burst = index
                thermal_noise.get_shrink_factor()
                
                # slant range simulated noise
                sim_noise, incidence_angles = thermal_noise.compute_simulated_noise(az_t0)

                # convert from slant range to ground range using actual SR distances
                az_t0_string = az_t0.isoformat(sep="T", timespec="microseconds").rstrip("Z")
                sim_noise, incidence_angles = slant2ground(
                    az_t0_string, sim_noise, incidence_angles,
                    coordinate_conversions, thermal_noise.range_distances
                )

                sim_noises.update({
                    (index, subswath): (az_t0_string, sim_noise, incidence_angles)
                })

        # append it
        simulated_noises.append(OutputNoiseVectors_GRD(adsHeader=ads_header, 
                                                       swath_merges=swath_merging_info, 
                                                       noiseRangeVectors=sim_noises,
                                                       dn_value=dn_value,
                                                       tot_vec_out=tot_noise_vectors_out))

    return simulated_noises

def generate_noise_profiles(prod: Path, aux_files: AuxiliaryFiles, l0n_file: Path = None):

    safe = S1Safe(prod)
    dn_value = aux_files.get_dn_value(safe.mode, safe.product_type)

    denoise_logger.info(f"Denoising product: {prod.resolve()}")

    if l0n_file is not None:        
        noise_power_correction = get_noise_pwr_from_l0n(l0n_path = l0n_file, aux_ins_path = aux_files.ins_filepath)
    else:
        noise_power_correction = None

    if safe.product_type == "SLC":
        simulated_noises = slc_processing(safe, aux_files, dn_value, noise_power_correction)
    elif safe.product_type.startswith("GRD"):
        simulated_noises = grd_processing(safe, aux_files, dn_value, noise_power_correction)
    else:
        denoise_logger.error(f"Unknown product type: {safe.product_type}")
        sys.exit(128)
    
    return simulated_noises
