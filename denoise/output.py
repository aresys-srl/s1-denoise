# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for formatting and writing denoised noise profile vectors to XML output files.

Provides data classes for SLC and GRD noise range vectors, handles subswath
concatenation and azimuth-time interpolation for GRD products, and serialises
the results to noise XML files following the Sentinel-1 annotation format.
"""

from lxml import etree
from pathlib import Path
import re
import numpy as np
import copy
from scipy.interpolate import interp1d

from arepytools.timing.precisedatetime import PreciseDateTime

from denoise.utils import iso_to_datetime, get_range_boundaries, ensure_list
from denoise.logger import denoise_logger

class NoiseRangeVector_SLC:

    def __init__(self, burst_no, azimuth_start_time, noise_values, geolocation_info, incidence_angles, lut_value):
        
        self.azimuthTime = azimuth_start_time

        unique_lines = sorted({
            int(d['line'])
            for d in geolocation_info['geolocationGridPointList']['geolocationGridPoint']
            if 'line' in d and str(d['line']).isdigit()
        })

        max_pixel = max({
            int(d['pixel'])
            for d in geolocation_info['geolocationGridPointList']['geolocationGridPoint']
            if 'pixel' in d and str(d['pixel']).isdigit()
        })

        self.line = unique_lines[burst_no]
        
        self.pixels = np.arange(0, max_pixel, 40)
        if self.pixels[-1] != max_pixel:
            self.pixels = np.append(self.pixels, max_pixel)
        self.pixel_count = len(self.pixels)

        noise = noise_values / incidence_angles

        noise *= lut_value ** 2
        noise = np.interp(self.pixels, np.arange(len(noise)), noise)

        self.noise_values = noise
        self.noiseRangeLut_count = len(self.noise_values)

class NoiseRangeVector_GRD:

    def __init__(self, start_az_time, noise_values, incidence_angles, line_no, pixels_array, lut_value):
    
        self.azimuthTime = start_az_time
        self.line = line_no        
        self.pixels = pixels_array
        self.pixel_count = len(self.pixels)

        noise = np.array(noise_values) / np.array(incidence_angles)

        noise *= lut_value ** 2
        noise = np.interp(self.pixels, np.arange(len(noise)), noise)

        self.noise_values = noise
        self.noiseRangeLut_count = len(self.noise_values)

class OutputNoiseVectors_SLC:

    def __init__(self, adsHeader: dict, noiseRangeVectors):

        self.ads_header = adsHeader
        self.noise_range_vectors = []  # list

        for burst_values, noise_values in noiseRangeVectors.items():
            rel_burst = burst_values[0]
            start_az_time = burst_values[1]
            sim_noise = noise_values[0]
            geo_grid_info = noise_values[1]
            incidence_angles = noise_values[2]
            dn_value = noise_values[3]
            noise_range_vec = NoiseRangeVector_SLC(rel_burst, 
                                                   start_az_time, 
                                                   sim_noise,
                                                   geo_grid_info, 
                                                   incidence_angles, 
                                                   dn_value)
            
            self.noise_range_vectors.append(noise_range_vec)

        self.vector_count = len(noiseRangeVectors)

class OutputNoiseVectors_GRD:

    def __init__(self, adsHeader: dict, swath_merges, noiseRangeVectors, dn_value, tot_vec_out):

        self.ads_header = adsHeader
        self.noise_range_vectors = []  # list

        subswaths = list(dict.fromkeys(key[1] for key in noiseRangeVectors))

        max_lines = max(
            int(d['lastAzimuthLine'])
            for p in swath_merges
            for d in ensure_list(p['swathBoundsList']['swathBounds'])
        )

        noise_range_vectors = {}

        burst_max = min(
            max(first for (first, second2) in noiseRangeVectors if second2 == second)
            for second in {s for (_, s) in noiseRangeVectors}
        )

        pixels_dic = {}

        for burst_no in range(burst_max):

            # concatenate noise values throughout subswaths
            noise = []
            incidence_angles = []
            start_az_times = []
            pixels = []

            # auxiliary variables to consider overlap in noise concatenation
            last_prec = None
            overlap = 0

            for subswath in subswaths:
                if (burst_no, subswath) in noiseRangeVectors:

                    az_time = noiseRangeVectors[(burst_no, subswath)][0]                    
                    start_az_times.append(az_time)
                    first, last = get_range_boundaries(swath_merges=swath_merges, subswath=subswath, az_time=az_time)

                    if last_prec is None:
                        overlap = 0
                    else:
                        overlap = abs(first - last_prec)
                        noise = noise[:-overlap]
                        incidence_angles = incidence_angles[:-overlap]
                    
                    pixels.append(first)
                    pixels.append(last)                    

                    noise.extend(noiseRangeVectors[(burst_no, subswath)][1])
                    incidence_angles.extend(noiseRangeVectors[(burst_no, subswath)][2])

                    last_prec = last

                else:
                    pass
    
            noise_range_vectors.update({
                burst_no: (min(start_az_times), noise, incidence_angles)
            })

            pixels_dic.update({
                min(start_az_times): pixels
            })

        # extend noise range values on 1-second spaced azimuth times 
        noise_range_vectors = extend_noise_grd(noise_range_vectors, tot_vec_out, self.ads_header['startTime'], self.ads_header['stopTime'], max_lines)
        
        for elem in noise_range_vectors.values():
            start_az_time, noise, incidence_angles, line_numbers = elem[0], elem[1], elem[2], elem[3]
            closest_az_time = min(pixels_dic.keys(),
                                  key=lambda k: abs(PreciseDateTime.fromisoformat(k) - PreciseDateTime.fromisoformat(start_az_time)))
            pixel_line = calculate_pixel_line(pixels_dic[closest_az_time])
            noise_range_vec = NoiseRangeVector_GRD(start_az_time, noise, incidence_angles, line_numbers, pixel_line, dn_value)            
            self.noise_range_vectors.append(noise_range_vec)

        self.vector_count = len(self.noise_range_vectors)


def calculate_pixel_line(pixel_bound_list):

    pixels = []
    start = 0
    for bound in pixel_bound_list[1::2]:
        pixels.extend(np.arange(start, bound, 40))
        if bound not in pixels:
            pixels.append(bound)
        start = bound + 1
        
    pixel_string = [str(int(x)) for x in pixels]

    return pixel_string

def extend_noise_grd(input_vectors, tot_vec_out, start_time, stop_time, max_lines):

    output_vectors = dict()

    # create new azimuth time axis
    az_times_old = [iso_to_datetime(v[0]).sec85 for v in input_vectors.values()]
    az_times_new = np.arange(iso_to_datetime(start_time).sec85, iso_to_datetime(stop_time).sec85, 1.0)
    
    if iso_to_datetime(stop_time) not in az_times_new:
        az_times_new = np.append(az_times_new, iso_to_datetime(stop_time).sec85)
    
    tot_vec_out = len(az_times_new)

    target_len = max(len(input_vectors[j][1]) for j in range(len(input_vectors)))

    for j in range(len(input_vectors)):
        vec = list(input_vectors[j])  # make a mutable copy
        y1 = np.array(vec[1])
        y2 = np.array(vec[2])
        x1 = np.linspace(0, 1, len(y1))
        x2 = np.linspace(0, 1, len(y2))
        x_new = np.linspace(0, 1, target_len)
        vec[1] = np.interp(x_new, x1, y1).tolist()
        vec[2] = np.interp(x_new, x2, y2).tolist()
        input_vectors[j] = vec  # put back

    noise_old = np.stack([v[1] for v in input_vectors.values()])
    angles_old = np.stack([v[2] for v in input_vectors.values()])

    interp_noise = interp1d(
        az_times_old,
        noise_old,
        axis=0,
        bounds_error=False,
        fill_value="extrapolate"
    )

    interp_angles = interp1d(
        az_times_old,
        angles_old,
        axis=0,
        bounds_error=False,
        fill_value="extrapolate"
    )
    
    noise_new = interp_noise(az_times_new)
    incidence_angles_new = interp_angles(az_times_new)

    delta_line = (az_times_new[-1] - az_times_new[0])/max_lines


    for i in range(tot_vec_out):

        line_number = int(np.round((az_times_new[i]-az_times_new[0])/delta_line))

        output_vectors.update({
            i: (PreciseDateTime.from_sec85(az_times_new[i]).isoformat(timespec="microseconds").rstrip("Z"), noise_new[i, :], incidence_angles_new[i, :], line_number)
        })

    return output_vectors

def build_filename(header):
    
    def clean_time(s):
        return re.sub(r"[.\-:]", "", s[:-7])
        
    filename = ( 
        f"denoise"
        f"-{header['missionId']}-{header['swath']}-{header['productType']}-{header['polarisation']}-"
        f"{clean_time(header['startTime'])}-{clean_time(header['stopTime'])}-"      # remove all ":", "." and "-" from timestamps
        f"{header['absoluteOrbitNumber'].zfill(6)}-"                                # add 0 padding
        f"{int(header['missionDataTakeId']):06x}-"                                  # convert mission datatake ID to hexadecimal
        f"{header['imageNumber']}.xml"
    )

    return filename

def write_to_xml(noise, output_path: Path, safe_path):

    root = etree.Element("noise")

    # adsHeader
    header = etree.SubElement(root, "adsHeader")
    for key, value in noise.ads_header.items():
        child = etree.SubElement(header, key)
        child.text = str(value)

    # noiseRangeVectorList
    vector_list = etree.SubElement(root, "noiseRangeVectorList", {"count": str(noise.vector_count)})
    for v in noise.noise_range_vectors:
        vec_elem = etree.SubElement(vector_list, "noiseRangeVector")

        etree.SubElement(vec_elem, "azimuthTime").text = str(v.azimuthTime)
        etree.SubElement(vec_elem, "line").text = str(v.line)

        etree.SubElement(vec_elem, "pixel", {"count": str(v.pixel_count)}).text = ' '.join(map(str, v.pixels))
        etree.SubElement(vec_elem, "noiseRangeLut", {"count": str(v.noiseRangeLut_count)}).text = " ".join(f"{x:.6e}" for x in v.noise_values)

    xml_tree = etree.ElementTree(root)

    output_path = output_path / Path(safe_path.stem)
    output_path.mkdir(parents=True, exist_ok=True) # check output folder exists
    output_filename = build_filename(noise.ads_header)
    output_file_path = Path(str(output_path.resolve() / output_filename.lower()))

    # noiseAzimuthVectorList
    nominal_noise_file = safe_path / "annotation/calibration" / (output_file_path.stem[2:] + ".xml")
    if nominal_noise_file.exists():
        nominal_noise_tree = etree.parse(nominal_noise_file)
        nominal_noise_root = nominal_noise_tree.getroot()
        noise_az_list = nominal_noise_root.find("noiseAzimuthVectorList")

        if noise_az_list is not None:
            root.append(copy.deepcopy(noise_az_list))

    etree.indent(root, space="  ")
    xml_tree.write(output_file_path, pretty_print=True, encoding="utf-8", xml_declaration=True)
    denoise_logger.info(f"Written output to {output_file_path.resolve()}")

    return output_file_path
    
def match_denoise_files(original_noise_folder, denoise_folder):

    # All files in folder_a (original names)
    noise_files = {
        p.name: p
        for p in original_noise_folder.iterdir()
        if p.is_file()
    }

    # Files in folder_b, stripping the "de" prefix
    denoise_files = {
        p.name.removeprefix("de"): p
        for p in denoise_folder.iterdir()
        if p.is_file()
    }

    # Keep only common filenames
    common_names = noise_files.keys() & denoise_files.keys()

    matches = [
        (noise_files[name], denoise_files[name])
        for name in common_names
    ]

    return matches