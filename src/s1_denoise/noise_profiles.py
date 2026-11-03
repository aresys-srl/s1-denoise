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

from perseo_core.timing import PreciseDateTime

# Local imports
from s1_denoise.logger import denoise_logger
from s1_denoise.s1safe import S1Safe
from s1_denoise.xml_reader import create_slant_range_times, read_metadata_from_safe_annotation
from s1_denoise.thermal_noise import ThermalNoise
from s1_denoise.auxiliary_files import AuxiliaryFiles
from s1_denoise.output import OutputNoiseVectors_SLC, OutputNoiseVectors_GRD
from s1_denoise.l0_noise_power_calculation import get_noise_pwr_from_l0n
from s1_denoise.utils import ensure_list, slant2ground

def print_exec_time(elapsed_seconds: float) -> None:
    """
    Log the execution time in a human-readable format.

    The time is split into hours, minutes and seconds and logged at debug level.

    Parameters
    ----------
    elapsed_seconds : float
        Elapsed time [s].
    """

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
    """
    Generate the simulated noise range vectors of an SLC product.

    For each polarization/subswath channel, one noise vector is computed per burst, at the burst center azimuth
    time, on the slant range axis of the swath.

    Parameters
    ----------
    safe : S1Safe
        Input SLC SAFE product.
    aux_files : AuxiliaryFiles
        Auxiliary files used for processing.
    dn_value : float
        Digital number calibration value from AUX_PP1, used to scale the output noise values.
    noise_power_correction : dict or None
        Noise power extracted from the L0N product, mapping ``(swath, polarization)`` to
        ``[azimuth_times, noise_powers]``, or None to use the noise power annotated in the product.

    Returns
    -------
    list of OutputNoiseVectors_SLC
        Output noise vectors, one element per channel.
    """

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

        # slant range times are the same for all the bursts of the swath
        range_t = create_slant_range_times(metadata_dict)
        thermal_noise.set_range_values(range_t)

        denoise_logger.info(f"Generating noise profiles for subswath {subswath} ({pol})")

        for rel_burst in range(channel.num_bursts):

            denoise_logger.debug(f"Generating noise profiles for burst {rel_burst+1} ({pol}, {subswath})")

            az_t0 = PreciseDateTime.from_utc_string((channel.annotation.find('antennaPattern/antennaPatternList/antennaPattern[{}]/azimuthTime'.format(rel_burst+1)).text))

            az_central_time = az_t0 + az_dt * lines_per_burst/2

            # create simulated noise
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
    """
    Generate the simulated noise range vectors of a GRD product.

    For each polarization, the subswaths merged in the annotation are processed separately: one noise vector is
    computed in slant range for each antenna pattern record (block) of the subswath and converted to ground range.
    The subswaths are then merged into cross-subswath noise vectors by `OutputNoiseVectors_GRD`.

    Parameters
    ----------
    safe : S1Safe
        Input GRD SAFE product.
    aux_files : AuxiliaryFiles
        Auxiliary files used for processing.
    dn_value : float
        Digital number calibration value from AUX_PP1, used to scale the output noise values.
    noise_power_correction : dict or None
        Noise power extracted from the L0N product, mapping ``(swath, polarization)`` to
        ``[azimuth_times, noise_powers]``, or None to use the noise power annotated in the product.

    Returns
    -------
    list of OutputNoiseVectors_GRD
        Output noise vectors, one element per polarization.
    """

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

            denoise_logger.info(f"Generating noise profiles for subswath {subswath} ({pol})")
                    
            for index in range(len(subswath_antenna_patterns)):

                denoise_logger.debug(f"Generating noise profiles for block {index+1} ({pol}, {subswath})")
                            
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
    """
    Generate the simulated noise range vectors of a Sentinel-1 L1 product.

    Dispatches to `slc_processing` or `grd_processing` depending on the product type. If an L0N product is given,
    the noise power is extracted from it instead of being read from the product annotation.

    Parameters
    ----------
    prod : pathlib.Path
        Path to the input L1 SAFE product (SLC or GRD).
    aux_files : AuxiliaryFiles
        Auxiliary files used for processing.
    l0n_file : pathlib.Path, optional
        Path to the associated L0N SAFE product. If None (default), the annotated noise power is used.

    Returns
    -------
    list of OutputNoiseVectors_SLC or list of OutputNoiseVectors_GRD
        Output noise vectors, one element per output noise file.

    Notes
    -----
    The process exits with code 128 if the product type is neither SLC nor GRD.
    """

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
