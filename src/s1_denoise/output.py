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

from perseo_core.timing import PreciseDateTime

from s1_denoise.utils import iso_to_datetime, get_range_boundaries, ensure_list
from s1_denoise.logger import denoise_logger

def get_lines_and_pixels(geolocation_info):
    """
    Return the lines of the geolocation grid and the output pixel axis.

    Parameters
    ----------
    geolocation_info : dict
        `geolocationGrid` section of the product annotation.

    Returns
    -------
    unique_lines : list of int
        Sorted unique line numbers of the geolocation grid points.
    pixels : numpy.ndarray
        Output pixels, every 40 samples from 0, with the last pixel of the grid appended.
    """

    grid_points = geolocation_info['geolocationGridPointList']['geolocationGridPoint']

    unique_lines = sorted({
        int(d['line'])
        for d in grid_points
        if 'line' in d and str(d['line']).isdigit()
    })

    max_pixel = max({
        int(d['pixel'])
        for d in grid_points
        if 'pixel' in d and str(d['pixel']).isdigit()
    })

    pixels = np.arange(0, max_pixel, 40)
    if pixels[-1] != max_pixel:
        pixels = np.append(pixels, max_pixel)

    return unique_lines, pixels

class NoiseRangeVector_SLC:
    """
    Noise range vector of an SLC burst, in the format of the noise annotation.

    The simulated noise is divided by the sine of the incidence angle, scaled by the squared digital number
    value and interpolated on the output pixels.

    Parameters
    ----------
    azimuth_start_time : str
        Azimuth start time of the burst, in ISO format.
    line : int
        Line number of the vector.
    pixels : numpy.ndarray
        Output pixel indices.
    noise_values : numpy.ndarray
        Simulated noise for each range sample (linear power).
    sin_incidence : numpy.ndarray
        Sine of the incidence angle for each range sample.
    lut_value : float
        Digital number calibration value from AUX_PP1.

    Attributes
    ----------
    azimuthTime : str
        Azimuth start time of the burst.
    line : int
        Line number of the vector.
    pixels : numpy.ndarray
        Output pixel indices.
    pixel_count : int
        Number of output pixels.
    noise_values : numpy.ndarray
        Output noise values on the output pixels.
    noiseRangeLut_count : int
        Number of output noise values.
    """

    def __init__(self, azimuth_start_time, line, pixels, noise_values, sin_incidence, lut_value):

        self.azimuthTime = azimuth_start_time
        self.line = line
        self.pixels = pixels
        self.pixel_count = len(self.pixels)

        noise = noise_values / sin_incidence

        noise *= lut_value ** 2
        noise = np.interp(self.pixels, np.arange(len(noise)), noise)

        self.noise_values = noise
        self.noiseRangeLut_count = len(self.noise_values)

class NoiseRangeVector_GRD:
    """
    Merged cross-subswath noise range vector of a GRD azimuth line, in the format of the noise annotation.

    The simulated noise is divided by the sine of the incidence angle, scaled by the squared digital number
    value and interpolated from the sample pixel coordinates to the output pixels.

    Parameters
    ----------
    start_az_time : str
        Azimuth time of the vector, in ISO format.
    noise_values : array_like
        Simulated noise for each sample (linear power), concatenated over the subswaths.
    sin_incidence : array_like
        Sine of the incidence angle for each sample.
    line_no : int
        Line number of the vector.
    pixels_array : list of str
        Output pixel indices.
    sample_pixel_coords : array_like
        Pixel coordinate of each noise sample.
    lut_value : float
        Digital number calibration value from AUX_PP1.

    Attributes
    ----------
    azimuthTime : str
        Azimuth time of the vector.
    line : int
        Line number of the vector.
    pixels : list of str
        Output pixel indices.
    pixel_count : int
        Number of output pixels.
    noise_values : numpy.ndarray
        Output noise values on the output pixels.
    noiseRangeLut_count : int
        Number of output noise values.
    """

    def __init__(self, start_az_time, noise_values, sin_incidence, line_no, pixels_array, sample_pixel_coords, lut_value):

        self.azimuthTime = start_az_time
        self.line = line_no
        self.pixels = pixels_array
        self.pixel_count = len(self.pixels)

        noise = np.array(noise_values) / np.array(sin_incidence)
        noise *= lut_value ** 2
        noise = np.interp(np.array(self.pixels, dtype=float), sample_pixel_coords, noise)

        self.noise_values = noise
        self.noiseRangeLut_count = len(self.noise_values)

class OutputNoiseVectors_SLC:
    """
    Output noise range vectors of an SLC channel (polarization and subswath).

    Parameters
    ----------
    adsHeader : dict
        `adsHeader` section of the product annotation.
    noiseRangeVectors : dict
        Simulated noise per burst, mapping ``(burst_index, start_azimuth_time)`` to
        ``(noise, geolocation_grid, sin_incidence, dn_value)``, where `noise` is the slant range simulated noise,
        `geolocation_grid` the `geolocationGrid` annotation section, `sin_incidence` the sine of the incidence angles
        and `dn_value` the digital number calibration value.

    Attributes
    ----------
    ads_header : dict
        `adsHeader` section of the product annotation.
    noise_range_vectors : list of NoiseRangeVector_SLC
        One noise range vector per burst, at the geolocation grid line of the burst.
    vector_count : int
        Number of noise range vectors.
    """

    def __init__(self, adsHeader: dict, noiseRangeVectors):

        self.ads_header = adsHeader
        self.noise_range_vectors = []  # list

        geo_grid_lines = None
        pixels = None

        for burst_values, noise_values in noiseRangeVectors.items():
            rel_burst = burst_values[0]
            start_az_time = burst_values[1]
            sim_noise = noise_values[0]
            geo_grid_info = noise_values[1]
            sin_incidence = noise_values[2]
            dn_value = noise_values[3]

            # the geolocation grid is the same for all the bursts of the swath
            if geo_grid_lines is None:
                geo_grid_lines, pixels = get_lines_and_pixels(geo_grid_info)

            noise_range_vec = NoiseRangeVector_SLC(start_az_time,
                                                   geo_grid_lines[rel_burst],
                                                   pixels,
                                                   sim_noise,
                                                   sin_incidence,
                                                   dn_value)
            
            self.noise_range_vectors.append(noise_range_vec)

        self.vector_count = len(noiseRangeVectors)

class OutputNoiseVectors_GRD:
    """
    Output cross-subswath noise range vectors of a GRD channel (polarization).

    The noise vectors of each subswath are interpolated in azimuth on the output lines of the IPF noise annotation
    (see `grd_noise_lines` and `extend_noise_grd`), then, for each output line, concatenated along range according to
    the swath merging boundaries, removing the samples of the previous subswath that fall in the overlap region.

    Parameters
    ----------
    adsHeader : dict
        `adsHeader` section of the product annotation.
    swath_merges : list of dict
        `swathMerge` entries of the product annotation.
    noiseRangeVectors : dict
        Simulated noise per block, mapping ``(block_index, subswath)`` to
        ``(azimuth_time, noise, sin_incidence)``, with the noise in ground range.
    dn_value : float
        Digital number calibration value from AUX_PP1.
    azimuth_time_interval : float
        Azimuth time interval between consecutive lines of the image [s].

    Attributes
    ----------
    ads_header : dict
        `adsHeader` section of the product annotation.
    noise_range_vectors : list of NoiseRangeVector_GRD
        One merged noise range vector per output line.
    vector_count : int
        Number of noise range vectors.
    """

    def __init__(self, adsHeader: dict, swath_merges, noiseRangeVectors, dn_value, azimuth_time_interval):

        self.ads_header = adsHeader
        self.noise_range_vectors = []

        # Collect unique subswath identifiers preserving their order
        subswaths = list(dict.fromkeys(key[1] for key in noiseRangeVectors))

        # Output lines of the noise vectors, as in the IPF noise annotation
        lines = grd_noise_lines(swath_merges, azimuth_time_interval)

        # Interpolate each subswath's noise vectors on the output lines
        # Block vectors are re-indexed sequentially and extrapolated to fill any gaps beyond the first/last block up to the scene boundaries
        subswath_extended = {}
        for subswath in subswaths:
            burst_indices = sorted(k[0] for k in noiseRangeVectors if k[1] == subswath)
            sw_vectors = {
                j: noiseRangeVectors[(burst_no, subswath)]
                for j, burst_no in enumerate(burst_indices)
            }
            subswath_extended[subswath] = extend_noise_grd(
                sw_vectors, self.ads_header['startTime'], azimuth_time_interval, lines
            )

        # Step 2: for each output line, concatenate subswaths in range.
        # All subswaths share the same output lines after the extension above.
        tot_vec = len(next(iter(subswath_extended.values())))

        for i in range(tot_vec):
            # Azimuth time and line number are taken from the first subswath (same for all)
            az_time = subswath_extended[subswaths[0]][i][0]
            line_no  = subswath_extended[subswaths[0]][i][3]

            noise = []
            sin_incidence = []
            sample_pixel_coords = []
            pixels = []
            last_prec = None
            first_prec = None

            for subswath in subswaths:
                noise_sw   = subswath_extended[subswath][i][1]
                sin_incidence_sw  = subswath_extended[subswath][i][2]

                # Retrieve the pixel range covered by this subswath at the current azimuth time
                first, last = get_range_boundaries(swath_merges=swath_merges, subswath=subswath, az_time=az_time)

                # Remove samples from the previous subswath that fall in the overlap region
                if last_prec is not None:
                    overlap_pixels = max(0, last_prec - first + 1)
                    if overlap_pixels > 0:
                        prev_pixel_width = last_prec - first_prec + 1
                        overlap_samples = round(overlap_pixels * len(noise) / prev_pixel_width)
                        noise = noise[:-overlap_samples]
                        sin_incidence = sin_incidence[:-overlap_samples]
                        sample_pixel_coords = sample_pixel_coords[:-overlap_samples]

                # Append this subswath's samples, mapping them uniformly across its pixel range
                pixels.extend([first, last])
                n_samples = len(noise_sw)
                sample_pixel_coords.extend(np.linspace(first, last, n_samples).tolist())
                noise.extend(list(noise_sw))
                sin_incidence.extend(list(sin_incidence_sw))

                first_prec = first
                last_prec = last

            # Build the merged noise range vector for this azimuth line and store it
            pixel_line = calculate_pixel_line(pixels)
            noise_range_vec = NoiseRangeVector_GRD(az_time, noise, sin_incidence, line_no, pixel_line, sample_pixel_coords, dn_value)
            self.noise_range_vectors.append(noise_range_vec)

        self.vector_count = len(self.noise_range_vectors)


def calculate_pixel_line(pixel_bound_list):
    """
    Build the output pixel axis of a merged GRD noise range vector.

    Within each subswath, pixels are taken every 40 samples, always including the last pixel of the subswath; the
    next subswath starts after the last pixel of the previous one.

    Parameters
    ----------
    pixel_bound_list : list of int
        First and last pixel of each subswath, as ``[first_1, last_1, first_2, last_2, ...]``. Only the last pixels
        are used.

    Returns
    -------
    list of str
        Output pixel indices.
    """

    pixels = []
    start = 0
    for bound in pixel_bound_list[1::2]:
        pixels.extend(np.arange(start, bound, 40))
        if bound not in pixels:
            pixels.append(bound)
        start = bound + 1
        
    pixel_string = [str(int(x)) for x in pixels]

    return pixel_string

def grd_noise_lines(swath_merges, azimuth_time_interval):
    """
    Return the output lines of the noise vectors of a GRD product, as in the IPF noise annotation.

    The lines are taken every ``round(1 / azimuth_time_interval)`` lines (about 1 s) from the first line, plus the
    last line of the image. If they are not already included, the first and the last line covered by all the
    subswaths are added as well (start and end of the full swath coverage at the data-take boundaries).

    Parameters
    ----------
    swath_merges : list of dict
        `swathMerge` entries of the product annotation.
    azimuth_time_interval : float
        Azimuth time interval between consecutive lines of the image [s].

    Returns
    -------
    list of int
        Sorted output lines.
    """

    swath_bounds = [ensure_list(swath_merge['swathBoundsList']['swathBounds']) for swath_merge in swath_merges]

    last_line = max(int(b['lastAzimuthLine']) for bounds in swath_bounds for b in bounds)
    step = int(np.round(1.0 / azimuth_time_interval))
    lines = set(range(0, last_line, step)) | {last_line}

    # first and last line covered by all the subswaths
    first_full_line = max(min(int(b['firstAzimuthLine']) for b in bounds) for bounds in swath_bounds)
    last_full_line = min(max(int(b['lastAzimuthLine']) for b in bounds) for bounds in swath_bounds)
    lines |= {line for line in (first_full_line, last_full_line) if 0 < line < last_line}

    return sorted(lines)

def extend_noise_grd(input_vectors, start_time, azimuth_time_interval, lines):
    """
    Interpolate the noise vectors of a GRD subswath on the output lines of the scene.

    The vectors are first resampled to a common length (the longest one), then linearly interpolated in azimuth at the
    time of each output line, ``start_time + line * azimuth_time_interval``. Values outside the input time span are
    held constant at the first/last vector.

    Parameters
    ----------
    input_vectors : dict
        Noise vectors of the subswath, mapping consecutive indices from 0 to
        ``(azimuth_time, noise, sin_incidence)``, with the azimuth time in ISO format.
    start_time : str
        Azimuth time of the first line of the scene, in ISO format.
    azimuth_time_interval : float
        Azimuth time interval between consecutive lines of the image [s].
    lines : list of int
        Output lines (see `grd_noise_lines`).

    Returns
    -------
    dict
        Extended noise vectors, mapping the output index to ``(azimuth_time, noise, sin_incidence, line)``, with
        the azimuth time as ISO string (rounded to the microsecond) and `noise`, `sin_incidence` as numpy.ndarray.
    """

    output_vectors = dict()

    # azimuth times relative to the first line of the scene [s]
    start = iso_to_datetime(start_time)
    az_times_old = [iso_to_datetime(v[0]) - start for v in input_vectors.values()]
    az_times_new = np.array(lines) * azimuth_time_interval

    target_len = max(len(input_vectors[j][1]) for j in range(len(input_vectors)))

    # resample noise and sine of the incidence angles of each vector to the same length,
    # without modifying the input vectors
    x_new = np.linspace(0, 1, target_len)
    resampled_vectors = []
    for j in range(len(input_vectors)):
        vec = list(input_vectors[j])  # make a mutable copy
        for idx in (1, 2):  # noise, sine of the incidence angles
            y = np.array(vec[idx])
            x = np.linspace(0, 1, len(y))
            vec[idx] = np.interp(x_new, x, y).tolist()
        resampled_vectors.append(vec)

    noise_old = np.stack([v[1] for v in resampled_vectors])
    sin_incidence_old = np.stack([v[2] for v in resampled_vectors])

    interp_noise = interp1d(
        az_times_old,
        noise_old,
        axis=0,
        bounds_error=False,
        fill_value=(noise_old[0], noise_old[-1])
    )

    interp_sin_incidence = interp1d(
        az_times_old,
        sin_incidence_old,
        axis=0,
        bounds_error=False,
        fill_value=(sin_incidence_old[0], sin_incidence_old[-1])
    )

    noise_new = interp_noise(az_times_new)
    sin_incidence_new = interp_sin_incidence(az_times_new)

    for i, line in enumerate(lines):

        # time rounded to the microsecond, as in the IPF noise annotation (isoformat truncates)
        az_time = (start + az_times_new[i] + 0.5e-6).isoformat(timespec="microseconds").rstrip("Z")

        output_vectors.update({
            i: (az_time, noise_new[i, :], sin_incidence_new[i, :], line)
        })

    return output_vectors

def build_filename(header):
    """
    Build the output noise file name from the product header.

    The name follows the Sentinel-1 annotation naming convention, with the ``denoise`` prefix:
    ``denoise-<mission>-<swath>-<type>-<pol>-<start>-<stop>-<orbit>-<datatake>-<image>.xml``.

    Parameters
    ----------
    header : dict
        `adsHeader` section of the product annotation.

    Returns
    -------
    str
        Output file name.
    """
    
    def clean_time(s):
        """Remove the fractional seconds and all ``.``, ``-`` and ``:`` characters from a timestamp."""
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
    """
    Write the output noise vectors to an XML noise file.

    The file is written in a subfolder of `output_path` named after the input product, with the `adsHeader` and the
    `noiseRangeVectorList`. The `noiseAzimuthVectorList` is copied unchanged from the nominal noise file of the
    input product, if present.

    Parameters
    ----------
    noise : OutputNoiseVectors_SLC or OutputNoiseVectors_GRD
        Output noise vectors of one channel.
    output_path : pathlib.Path
        Output directory.
    safe_path : pathlib.Path
        Path to the input SAFE product.

    Returns
    -------
    pathlib.Path
        Path of the written XML file.
    """

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
