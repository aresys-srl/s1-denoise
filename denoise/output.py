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

    def __init__(self, start_az_time, noise_values, incidence_angles, line_no, pixels_array, sample_pixel_coords, lut_value):

        self.azimuthTime = start_az_time
        self.line = line_no
        self.pixels = pixels_array
        self.pixel_count = len(self.pixels)

        noise = np.array(noise_values) / np.array(incidence_angles)
        noise *= lut_value ** 2
        noise = np.interp(np.array(self.pixels, dtype=float), sample_pixel_coords, noise)

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
        self.noise_range_vectors = []

        # Collect unique subswath identifiers preserving their order
        subswaths = list(dict.fromkeys(key[1] for key in noiseRangeVectors))

        # Determine the total number of azimuth lines across all swath boundaries
        max_lines = max(
            int(d['lastAzimuthLine'])
            for p in swath_merges
            for d in ensure_list(p['swathBoundsList']['swathBounds'])
        )

        # Extend each subswath's noise vectors to cover the full azimuth extent
        # Burst vectors are re-indexed sequentially and extrapolated to fill any gaps beyond the first/last burst up to the scene boundaries
        subswath_extended = {}
        for subswath in subswaths:
            burst_indices = sorted(k[0] for k in noiseRangeVectors if k[1] == subswath)
            sw_vectors = {
                j: noiseRangeVectors[(burst_no, subswath)]
                for j, burst_no in enumerate(burst_indices)
            }
            subswath_extended[subswath] = extend_noise_grd(
                sw_vectors, self.ads_header['startTime'], self.ads_header['stopTime'], max_lines
            )

        # Step 2: for each azimuth time step, concatenate subswaths in range.
        # All subswaths share the same number of time steps after the extension above.
        tot_vec = len(next(iter(subswath_extended.values())))

        for i in range(tot_vec):
            # Azimuth time and line number are taken from the first subswath (same for all)
            az_time = subswath_extended[subswaths[0]][i][0]
            line_no  = subswath_extended[subswaths[0]][i][3]

            noise = []
            incidence_angles = []
            sample_pixel_coords = []
            pixels = []
            last_prec = None
            first_prec = None

            for subswath in subswaths:
                noise_sw   = subswath_extended[subswath][i][1]
                angles_sw  = subswath_extended[subswath][i][2]

                # Retrieve the pixel range covered by this subswath at the current azimuth time
                first, last = get_range_boundaries(swath_merges=swath_merges, subswath=subswath, az_time=az_time)

                # Remove samples from the previous subswath that fall in the overlap region
                if last_prec is not None:
                    overlap_pixels = max(0, last_prec - first + 1)
                    if overlap_pixels > 0:
                        prev_pixel_width = last_prec - first_prec + 1
                        overlap_samples = round(overlap_pixels * len(noise) / prev_pixel_width)
                        noise = noise[:-overlap_samples]
                        incidence_angles = incidence_angles[:-overlap_samples]
                        sample_pixel_coords = sample_pixel_coords[:-overlap_samples]

                # Append this subswath's samples, mapping them uniformly across its pixel range
                pixels.extend([first, last])
                n_samples = len(noise_sw)
                sample_pixel_coords.extend(np.linspace(first, last, n_samples).tolist())
                noise.extend(list(noise_sw))
                incidence_angles.extend(list(angles_sw))

                first_prec = first
                last_prec = last

            # Build the merged noise range vector for this azimuth line and store it
            pixel_line = calculate_pixel_line(pixels)
            noise_range_vec = NoiseRangeVector_GRD(az_time, noise, incidence_angles, line_no, pixel_line, sample_pixel_coords, dn_value)
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

def extend_noise_grd(input_vectors, start_time, stop_time, max_lines):

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
        x_new = np.linspace(0, 1, target_len)
        for idx in (1, 2):  # noise, incidence_angles
            y = np.array(vec[idx])
            x = np.linspace(0, 1, len(y))
            vec[idx] = np.interp(x_new, x, y).tolist()
        input_vectors[j] = vec  # put back

    noise_old = np.stack([v[1] for v in input_vectors.values()])
    angles_old = np.stack([v[2] for v in input_vectors.values()])

    interp_noise = interp1d(
        az_times_old,
        noise_old,
        axis=0,
        bounds_error=False,
        fill_value=(noise_old[0], noise_old[-1])
    )

    interp_angles = interp1d(
        az_times_old,
        angles_old,
        axis=0,
        bounds_error=False,
        fill_value=(angles_old[0], angles_old[-1])
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

    # All files in original_noise_folder
    noise_files = {
        p.name: p
        for p in original_noise_folder.iterdir()
        if p.is_file()
    }

    # Files in denoise_folder
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