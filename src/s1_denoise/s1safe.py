# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module providing classes and utilities for reading and handling Sentinel-1 SAFE products.

Defines S1Safe, S1Channel, and S1Roi as the primary abstractions for accessing
product metadata, annotation, noise, calibration, RFI, and measurement files across
TopSAR acquisition modes (IW, EW). Also provides functions for noise map
computation, calibration vector interpolation, and SAFE filename parsing.
"""

from pathlib import Path
from datetime import datetime
import os
import xml.etree.ElementTree as ET
from PIL import Image
import numpy as np
import rasterio
from rasterio.windows import Window

from perseo_core.timing import PreciseDateTime

_subswaths = {'EW': ('EW1', 'EW2', 'EW3', 'EW4', 'EW5'),
              'IW': ('IW1', 'IW2', 'IW3'),
              'WV': ('WV1', 'WV2')}

_pols = {'DV': ('VV', 'VH'),
         'DH': ('HH', 'HV'),
         'SH': ('HH',),
         'SV': ('VV',)}

calibration_types = ('dn', 'betaNought', 'gamma', 'sigmaNought')

def ch_idx(pol, swath_id):
    """
    Return the key identifying a channel in the channel dictionary of ``S1Safe``.

    Parameters
    ----------
    pol : str
        Polarization (e.g. ``VV``).
    swath_id : str
        Swath identifier (e.g. ``IW1``).

    Returns
    -------
    tuple of str
        The ``(pol, swath_id)`` pair.
    """
    return pol, swath_id

def read_calibration_vectors(calibration_et_root, calibration):
    """
    Read the calibration vectors of a given type from a calibration annotation.

    Parameters
    ----------
    calibration_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed calibration annotation XML.
    calibration : str
        Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``.

    Returns
    -------
    list of dict
        One dictionary per ``calibrationVector`` with keys ``azimuthTime`` (PreciseDateTime), ``line`` (int),
        ``pixel`` (numpy.ndarray of int) and ``<calibration>`` (numpy.ndarray of float, calibration LUT values).

    Raises
    ------
    ValueError
        If ``calibration`` is not a supported calibration type.
    """
    if calibration not in calibration_types:
        raise ValueError('Calibration type must be one of the following:' + ', '.join(calibration_types))
    cal_vector_list = list()
    for cal_vector in calibration_et_root.findall('calibrationVectorList/calibrationVector'):
        cal_vector_dict = dict()
        cal_vector_dict['azimuthTime'] = PreciseDateTime.fromisoformat(cal_vector.find('azimuthTime').text)
        cal_vector_dict['line'] = int(cal_vector.find('line').text)
        cal_vector_dict['pixel'] = np.fromstring(cal_vector.find('pixel').text, sep=' ', dtype=int)
        cal_vector_dict[calibration] = np.fromstring(cal_vector.find(calibration).text, sep=' ', dtype=float)
        cal_vector_list.append(cal_vector_dict)
    return cal_vector_list


def calibrate(annotation_et_root, calibration_et_root, data, roi, calibration=None):
    """
    Apply radiometric calibration to data over a region of interest.

    The calibration LUT is interpolated linearly in range on the ROI pixels and linearly in azimuth time between
    the two calibration vectors bracketing each line (extrapolating outside the annotated vectors). The output
    is ``|data|**2 / lut**2``.

    Parameters
    ----------
    annotation_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed product annotation XML, used to build the azimuth time axis.
    calibration_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed calibration annotation XML.
    data : numpy.ndarray
        Data over the ROI, with shape ``(roi.num_azi_lines, roi.num_range_samples)``.
    roi : S1Roi
        Region of interest covered by ``data``.
    calibration : str, optional
        Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``. Default is ``None``, in which
        case ``data`` is returned unchanged.

    Returns
    -------
    numpy.ndarray
        Calibrated data in linear power units, or ``data`` itself if ``calibration`` is ``None``.

    Raises
    ------
    ValueError
        If ``calibration`` is not a supported calibration type.
    """
    if calibration is None:
        return data
    # Build the azimuth time axis
    cal_vectors = read_calibration_vectors(calibration_et_root, calibration)
    start_line_time = PreciseDateTime.fromisoformat(annotation_et_root.find('imageAnnotation/imageInformation/productFirstLineUtcTime').text)
    line_time_step = float(annotation_et_root.find('imageAnnotation/imageInformation/azimuthTimeInterval').text)
    azi_time_axis = np.array([start_line_time + ii * line_time_step for ii in range(roi.azi_start_idx, roi.azi_start_idx + roi.num_azi_lines)])

    # Resample all the calibration vectors on the same range axis
    rng_pixel_axis = np.arange(roi.range_start_idx, roi.range_start_idx+roi.num_range_samples)
    for cal_vec in cal_vectors:
        cal_vec[calibration] = np.interp(rng_pixel_axis, cal_vec['pixel'], cal_vec[calibration])

    # For each azimuth time, linearly interpolate the vectors before and after it.
    cal_mat = np.zeros((roi.num_azi_lines, roi.num_range_samples))
    for azi_idx, azi_time in enumerate(azi_time_axis):
        # Search an azimuth time lower than the current line being processed
        cal_vec_idx = 1
        while (cal_vec_idx < len(cal_vectors) - 1) and (cal_vectors[cal_vec_idx]['azimuthTime'] <= azi_time):
            cal_vec_idx += 1
        # Linear interpolation of the calibration vector
        cal_mat[azi_idx, :] = (cal_vectors[cal_vec_idx - 1][calibration] +
                              (cal_vectors[cal_vec_idx][calibration] - cal_vectors[cal_vec_idx - 1][calibration]) /
                              (cal_vectors[cal_vec_idx]['azimuthTime'] - cal_vectors[cal_vec_idx - 1]['azimuthTime']) *
                              (azi_time - cal_vectors[cal_vec_idx - 1]['azimuthTime']))
    return np.abs(data)**2 / cal_mat**2


def find_azi_block_from_line(azi_noise_vec_list, line_idx):
    """
    Return the index of the azimuth noise vector whose line interval contains a given line.

    Parameters
    ----------
    azi_noise_vec_list : list of dict
        Azimuth noise vectors of one swath, as returned by ``read_azimuth_noise_vectors``.
    line_idx : int
        Azimuth line index.

    Returns
    -------
    int or None
        Index of the first vector with ``firstAzimuthLine <= line_idx <= lastAzimuthLine``, or ``None`` if none.
    """
    for vec_idx, azi_noise_vec in enumerate(azi_noise_vec_list):
        if (azi_noise_vec['firstAzimuthLine'] <= line_idx) and (azi_noise_vec['lastAzimuthLine'] >= line_idx):
            return vec_idx
    return None


def read_range_noise_vectors(noise_et_root):
    """
    Read the range noise vectors from a noise annotation.

    Parameters
    ----------
    noise_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed noise annotation XML.

    Returns
    -------
    list of dict
        One dictionary per ``noiseRangeVector`` with keys ``azimuthTime`` (PreciseDateTime), ``line`` (int),
        ``pixel`` (numpy.ndarray of int) and ``noiseRangeLut`` (numpy.ndarray of float, linear power).
    """
    noise_rng_vec_list = list()
    for noise_rng_vec in noise_et_root.findall('noiseRangeVectorList/noiseRangeVector'):
        noise_rng_vec_dict = dict()
        noise_rng_vec_dict['azimuthTime'] = PreciseDateTime.fromisoformat(noise_rng_vec.find('azimuthTime').text)
        noise_rng_vec_dict['line'] = int(noise_rng_vec.find('line').text)
        noise_rng_vec_dict['pixel'] = np.fromstring(noise_rng_vec.find('pixel').text, sep=' ', dtype=int)
        noise_rng_vec_dict['noiseRangeLut'] = np.fromstring(noise_rng_vec.find('noiseRangeLut').text, sep=' ',
                                                            dtype=float)
        noise_rng_vec_list.append(noise_rng_vec_dict)
    return noise_rng_vec_list


def read_azimuth_noise_vectors(noise_et_root):
    """
    Read the azimuth noise vectors from a noise annotation, grouped by swath.

    Parameters
    ----------
    noise_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed noise annotation XML.

    Returns
    -------
    dict of str to list of dict
        Mapping from swath identifier to its azimuth noise vectors. Each vector has keys ``swath``,
        ``firstAzimuthLine``, ``firstRangeSample``, ``lastAzimuthLine``, ``lastRangeSample`` (int), ``line``
        (numpy.ndarray of int) and ``noiseAzimuthLut`` (numpy.ndarray of float, linear). Empty if the annotation
        has no azimuth noise vectors.
    """
    noise_azi_vec_dict = dict()
    for noise_azi_vec in noise_et_root.findall('noiseAzimuthVectorList/noiseAzimuthVector'):
        noise_azi_vec_curr_swath = dict()
        curr_swath = noise_azi_vec.find('swath').text
        noise_azi_vec_curr_swath['swath'] = curr_swath
        noise_azi_vec_curr_swath['firstAzimuthLine'] = int(noise_azi_vec.find('firstAzimuthLine').text)
        noise_azi_vec_curr_swath['firstRangeSample'] = int(noise_azi_vec.find('firstRangeSample').text)
        noise_azi_vec_curr_swath['lastAzimuthLine'] = int(noise_azi_vec.find('lastAzimuthLine').text)
        noise_azi_vec_curr_swath['lastRangeSample'] = int(noise_azi_vec.find('lastRangeSample').text)
        noise_azi_vec_curr_swath['line'] = np.fromstring(noise_azi_vec.find('line').text, sep=' ', dtype=int)
        noise_azi_vec_curr_swath['noiseAzimuthLut'] = np.fromstring(noise_azi_vec.find('noiseAzimuthLut').text,
                                                                    sep=' ', dtype=float)
        if curr_swath not in noise_azi_vec_dict:
            noise_azi_vec_dict[curr_swath] = [noise_azi_vec_curr_swath]
        else:
            noise_azi_vec_dict[curr_swath].append(noise_azi_vec_curr_swath)
    return noise_azi_vec_dict


def compute_noise_map(noise_et_root, data_size, roi):
    """
    Compute a 2D noise map over a region of interest from the annotated noise vectors.

    Range LUTs are interpolated to every range sample and linearly in azimuth between the range vectors bracketing
    each line (restricted to the vectors falling in the current azimuth block, or in the whole swath as fallback).
    The result is multiplied by the azimuth LUT interpolated on the lines of each azimuth block. If the annotation
    has no azimuth vectors, a unit azimuth LUT over the whole image is used. Pixels not covered by any azimuth
    block are left to zero.

    Parameters
    ----------
    noise_et_root : xml.etree.ElementTree.ElementTree or xml.etree.ElementTree.Element
        Parsed noise annotation XML.
    data_size : tuple of int
        Image size as ``(num_azimuth_lines, num_range_samples)``.
    roi : tuple of int
        Region of interest as ``(start_sample, start_line, width, height)``, i.e. ``S1Roi.as_tuple()``.

    Returns
    -------
    numpy.ndarray
        Noise map of shape ``(height, width)``, square root of the interpolated noise power LUT product (linear
        amplitude scale).

    Raises
    ------
    ValueError
        If the ROI exceeds the image size, or if no range noise vector is found for a swath.
    """

    n_az, n_rg = data_size
    start_sample, start_line, width, height = roi
    end_sample = start_sample + width - 1
    end_line = start_line + height - 1

    if end_sample >= n_rg or end_line >= n_az:
        raise ValueError("ROI outside of bounds")

    range_noise = read_range_noise_vectors(noise_et_root)
    azimuth_noise = read_azimuth_noise_vectors(noise_et_root)

    # Handle missing azimuth noise
    if not azimuth_noise:
        swath = noise_et_root.find("adsHeader/swath").text
        azimuth_noise[swath] = [{
            "swath": swath,
            "firstAzimuthLine": 0,
            "lastAzimuthLine": n_az - 1,
            "firstRangeSample": 0,
            "lastRangeSample": n_rg - 1,
            "line": np.array([0, n_az - 1]),
            "noiseAzimuthLut": np.array([1.0, 1.0]),
        }]

    # Precompute swath limits
    min_line = {k: min(v["firstAzimuthLine"] for v in lst) for k, lst in azimuth_noise.items()}
    max_line = {k: max(v["lastAzimuthLine"] for v in lst) for k, lst in azimuth_noise.items()}

    # Pre-interpolate range LUTs to full pixel resolution
    pixels = np.arange(n_rg)
    for rvec in range_noise:
        if rvec:
            rvec["noiseRangeLut"] = np.interp(pixels, rvec["pixel"], rvec["noiseRangeLut"])

    # Pre-cache line values for range noise vectors
    line_arr_rng = np.array([rvec["line"] for rvec in range_noise])
    noise_map = np.zeros((height, width), dtype=float)

    for swath_name, azi_list in azimuth_noise.items():
        for azi_vec in azi_list:
            first_line, last_line = azi_vec["firstAzimuthLine"], azi_vec["lastAzimuthLine"]

            # Lines overlapping ROI
            valid_lines = np.arange(max(first_line, start_line), min(last_line, end_line) + 1)
            if valid_lines.size == 0:
                continue

            # Interpolate azimuth LUT over valid lines
            line_arr = azi_vec["line"]
            azi_lut = azi_vec["noiseAzimuthLut"]
            azi_interp = np.interp(valid_lines, line_arr, azi_lut) if len(line_arr) > 1 else np.full(valid_lines.shape, azi_lut[0])

            # Valid range samples
            valid_rg = np.arange(max(azi_vec["firstRangeSample"], start_sample),
                                 min(azi_vec["lastRangeSample"], end_sample) + 1)
            if valid_rg.size == 0:
                continue

            # Find range noise vectors once per azimuth segment
            in_swath = (line_arr_rng >= first_line) & (line_arr_rng <= last_line)
            valid_idx = np.where(in_swath)[0]

            if valid_idx.size == 0:
                # fallback to sub-swath range
                in_subswath = (line_arr_rng >= min_line[swath_name]) & (line_arr_rng <= max_line[swath_name])
                valid_idx = np.where(in_subswath)[0]
                if valid_idx.size == 0:
                    raise ValueError(f"{swath_name}: no valid range vectors")

            # Process each azimuth line
            for i, line in enumerate(valid_lines):
                if valid_idx.size == 1:
                    rng_lut = range_noise[valid_idx[0]]["noiseRangeLut"][valid_rg]
                else:
                    # find bracket vectors
                    idx = np.searchsorted(line_arr_rng[valid_idx], line) - 1
                    idx = np.clip(idx, 0, len(valid_idx) - 2)

                    vec1 = range_noise[valid_idx[idx]]
                    vec2 = range_noise[valid_idx[idx + 1]]
                    lut1, lut2 = vec1["noiseRangeLut"][valid_rg], vec2["noiseRangeLut"][valid_rg]
                    w = (line - vec1["line"]) / (vec2["line"] - vec1["line"])
                    rng_lut = lut1 + w * (lut2 - lut1)

                noise_map[line - start_line, valid_rg - start_sample] = rng_lut * azi_interp[i]

    return np.sqrt(noise_map)


def subswaths_from_mode(mode_name):
    """
    Return the swath identifiers of an acquisition mode.

    Parameters
    ----------
    mode_name : str
        Acquisition mode (``IW``, ``EW``, ``WV`` or a stripmap mode ``S1``..``S6``).

    Returns
    -------
    tuple of str
        Swath identifiers (e.g. ``('IW1', 'IW2', 'IW3')``); for stripmap modes, the mode itself.

    Raises
    ------
    KeyError
        If the mode is not supported.
    """
    if is_stripmap_mode(mode_name):
        return (mode_name,)
    return _subswaths[mode_name]


def mode_from_safe_name(safe_name):
    """
    Return the acquisition mode from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name (e.g. ``S1A_IW_SLC__1SDV_...``).

    Returns
    -------
    str
        Two-character acquisition mode (e.g. ``IW``).
    """
    return safe_name[4:6]


def product_type_from_safe_name(safe_name):
    """
    Return the product type from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    str
        Product type with underscores removed (e.g. ``SLC``, ``GRDH``).
    """
    tmp = safe_name[6:11]
    tmp = tmp.replace('_', '')
    return tmp


def polarizations_from_safe_name(safe_name):
    """
    Return the polarizations from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    tuple of str
        Polarizations of the product (e.g. ``('VV', 'VH')`` for ``DV``).

    Raises
    ------
    KeyError
        If the polarization code is not supported.
    """
    return _pols[safe_name[14:16]]


def start_time_str_from_safe_name(safe_name):
    """
    Return the sensing start time string from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    str
        Start time in the format ``YYYYMMDDTHHMMSS``.
    """
    return safe_name[17:32]


def stop_time_str_from_safe_name(safe_name):
    """
    Return the sensing stop time string from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    str
        Stop time in the format ``YYYYMMDDTHHMMSS``.
    """
    return safe_name[33:48]


def is_topsar_mode(mode_name):
    """
    Check whether an acquisition mode is a TOPSAR mode.

    Parameters
    ----------
    mode_name : str
        Acquisition mode.

    Returns
    -------
    bool
        ``True`` for ``IW`` and ``EW``, ``False`` otherwise.
    """
    if mode_name == 'EW' or mode_name == 'IW':
        return True
    return False


def is_topsar(safe_name):
    """
    Check whether a SAFE product was acquired in a TOPSAR mode (IW or EW).

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    bool
        ``True`` if the product is IW or EW.
    """
    mode = mode_from_safe_name(safe_name)
    return is_topsar_mode(mode)


def is_stripmap_mode(mode_name):
    """
    Check whether an acquisition mode is a stripmap mode.

    Parameters
    ----------
    mode_name : str
        Acquisition mode.

    Returns
    -------
    bool
        ``True`` if the mode starts with ``S`` (``S1``..``S6``).
    """
    if mode_name[:1] == 'S':
        return True
    return False


def is_stripmap(safe_name):
    """
    Check whether a SAFE product was acquired in a stripmap mode.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    bool
        ``True`` if the product is a stripmap product.
    """
    mode = mode_from_safe_name(safe_name)
    return is_stripmap_mode(mode)


def is_wave(safe_name):
    """
    Check whether a SAFE product was acquired in wave mode.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    bool
        ``True`` if the mode is ``WV``, ``False`` otherwise.
    """
    return mode_from_safe_name(safe_name) == 'WV'


def is_grd(safe_name):
    """
    Check whether a SAFE product is a GRD product.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    bool
        ``True`` if the product type is ``GR`` followed by two characters (e.g. ``GRDH``, ``GRDM``).
    """
    prod = product_type_from_safe_name(safe_name)
    if prod[:-2] == 'GR':
        return True
    return False


def measurement_file_from_safe_path(safe_path, pol, swath, img_num=None):
    """
    Return the measurement TIFF file of a channel inside a SAFE product.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.
    pol : str
        Polarization (e.g. ``VV``, ``VH``).
    swath : str
        Swath identifier (e.g. ``IW1``, ``EW3``, ``WV1``).
    img_num : str, optional
        Image number, used only for wave mode swaths (``WV1``, ``WV2``). Default is ``None``.

    Returns
    -------
    pathlib.Path
        Path to the first matching file in ``measurement``.

    Raises
    ------
    StopIteration
        If no matching file is found (e.g. for annotation-only products).
    """
    safe_path = Path(safe_path)
    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('measurement/s1*-{}-*-{}-*-{}.tiff'.format(swath.lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('measurement/s1*-{}-*-{}-*.tiff'.format(swath.lower(), pol.lower())))


def calibration_file_from_safe_path(safe_path, pol, swath, img_num=None):
    """
    Return the calibration annotation file of a channel inside a SAFE product.

    The file is searched in ``annotation/calibration`` by swath, product type and polarization. Product type ``SL1`` is
    searched as ``SLC``.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.
    pol : str
        Polarization (e.g. ``VV``, ``VH``).
    swath : str
        Swath identifier (e.g. ``IW1``, ``EW3``, ``WV1``).
    img_num : str, optional
        Image number, used only for wave mode swaths (``WV1``, ``WV2``). Default is ``None``.

    Returns
    -------
    pathlib.Path
        Path to the first matching calibration annotation file.

    Raises
    ------
    StopIteration
        If no matching file is found.
    """
    safe_path = Path(safe_path)
    safe_name = safe_path.name
    prod = product_type_from_safe_name(safe_name)
    if prod == 'SL1':
        prod = 'SLC'
    
    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('annotation/calibration/calibration*-{}-{}-{}-*-{}.xml'.format(swath.lower(), prod[:3].lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('annotation/calibration/calibration*-{}-{}-{}-*.xml'.format(swath.lower(), prod[:3].lower(), pol.lower())))


def noise_file_from_safe_path(safe_path, pol, swath, img_num=None):
    """
    Return the noise annotation file of a channel inside a SAFE product.

    The file is searched in ``annotation/calibration`` by swath, product type and polarization. Product type ``SL1`` is
    searched as ``SLC``.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.
    pol : str
        Polarization (e.g. ``VV``, ``VH``).
    swath : str
        Swath identifier (e.g. ``IW1``, ``EW3``, ``WV1``).
    img_num : str, optional
        Image number, used only for wave mode swaths (``WV1``, ``WV2``). Default is ``None``.

    Returns
    -------
    pathlib.Path
        Path to the first matching noise annotation file.

    Raises
    ------
    StopIteration
        If no matching file is found.
    """
    safe_path = Path(safe_path)
    safe_name = safe_path.name
    prod = product_type_from_safe_name(safe_name)
    if prod == 'SL1':
        prod = 'SLC'

    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('annotation/calibration/noise*-{}-{}-{}-*-{}.xml'.format(swath.lower(), prod[:3].lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('annotation/calibration/noise*-{}-{}-{}-*.xml'.format(swath.lower(), prod[:3].lower(), pol.lower())))


def rfi_file_from_safe_path(safe_path, pol, swath, img_num=None):
    """
    Return the RFI annotation file of a channel inside a SAFE product.

    The file is searched in ``annotation/rfi`` by swath, product type and polarization. Product type ``SL1`` is
    searched as ``SLC``.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.
    pol : str
        Polarization (e.g. ``VV``, ``VH``).
    swath : str
        Swath identifier (e.g. ``IW1``, ``EW3``, ``WV1``).
    img_num : str, optional
        Image number, used only for wave mode swaths (``WV1``, ``WV2``). Default is ``None``.

    Returns
    -------
    pathlib.Path
        Path to the first matching RFI annotation file.

    Raises
    ------
    StopIteration
        If no matching file is found.
    """
    safe_path = Path(safe_path)
    safe_name = safe_path.name
    prod = product_type_from_safe_name(safe_name)
    if prod == 'SL1':
        prod = 'SLC'
    
    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('annotation/rfi/rfi*-{}-{}-{}-*-{}.xml'.format(swath.lower(), prod[:3].lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('annotation/rfi/rfi*-{}-{}-{}-*.xml'.format(swath.lower(), prod[:3].lower(), pol.lower())))


def annotation_file_from_safe_path(safe_path, pol, swath, img_num=None):
    """
    Return the product annotation file of a channel inside a SAFE product.

    The file is searched in ``annotation`` by swath, product type and polarization. Product type ``SL1`` is
    searched as ``SLC``.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.
    pol : str
        Polarization (e.g. ``VV``, ``VH``).
    swath : str
        Swath identifier (e.g. ``IW1``, ``EW3``, ``WV1``).
    img_num : str, optional
        Image number, used only for wave mode swaths (``WV1``, ``WV2``). Default is ``None``.

    Returns
    -------
    pathlib.Path
        Path to the first matching product annotation file.

    Raises
    ------
    StopIteration
        If no matching file is found.
    """
    safe_path = Path(safe_path)
    safe_name = safe_path.name
    prod = product_type_from_safe_name(safe_name)
    if prod == 'SL1':
        prod = 'SLC'
    
    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('annotation/*-{}-{}-{}*{}.xml'.format(swath.lower(), prod[:3].lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('annotation/*-{}-{}-{}-*.xml'.format(swath.lower(), prod[:3].lower(), pol.lower())))


def string_to_datetime(safe_datetime):
    """
    Convert a SAFE datetime string to a datetime object.

    Parameters
    ----------
    safe_datetime : str
        Datetime string in the format ``YYYYMMDDTHHMMSS`` (e.g. ``'20211112T230710'``).

    Returns
    -------
    datetime.datetime
        The corresponding naive datetime.
    """
    dt = datetime(year=int(safe_datetime[:4]),
                  month=int(safe_datetime[4:6]),
                  day=int(safe_datetime[6:8]),
                  hour=int(safe_datetime[9:11]),
                  minute=int(safe_datetime[11:13]),
                  second=int(safe_datetime[13:15]))
    return dt


def datetime_to_string(dt):
    """
    Convert a datetime object to a SAFE datetime string.

    Parameters
    ----------
    dt : datetime.datetime
        Datetime to convert.

    Returns
    -------
    str
        Datetime string in the format ``YYYYMMDDTHHMMSS`` (e.g. ``'20210105T090701'``), inverse of
        `string_to_datetime`. Sub-second precision is discarded.
    """
    safe_datetime_str = dt.strftime('%Y%m%dT%H%M%S')
    return safe_datetime_str


def time_from_safe_name(safe_name):
    """
    Return the sensing start and stop times from a SAFE product name.

    Parameters
    ----------
    safe_name : str
        SAFE product name.

    Returns
    -------
    time_start : datetime.datetime
        Sensing start time.
    time_stop : datetime.datetime
        Sensing stop time.
    """
    time_start = string_to_datetime(start_time_str_from_safe_name(safe_name))
    time_stop = string_to_datetime(stop_time_str_from_safe_name(safe_name))
    return time_start, time_stop


def start_time_str_from_orbit_name(orbit_name):
    """
    Return the validity start time string from an orbit file name.

    Parameters
    ----------
    orbit_name : str
        Orbit file name (e.g. ``S1A_OPER_AUX_POEORB_OPOD_<creation>_V<start>_<stop>.EOF``).

    Returns
    -------
    str
        Validity start time in the format ``YYYYMMDDTHHMMSS``.
    """
    return orbit_name[42:57]


def stop_time_str_from_orbit_name(orbit_name):
    """
    Return the validity stop time string from an orbit file name.

    Parameters
    ----------
    orbit_name : str
        Orbit file name (e.g. ``S1A_OPER_AUX_POEORB_OPOD_<creation>_V<start>_<stop>.EOF``).

    Returns
    -------
    str
        Validity stop time in the format ``YYYYMMDDTHHMMSS``.
    """
    return orbit_name[58:73]


def get_time_from_orbit_name(orbit_name):
    """
    Return the validity start and stop times from an orbit file name.

    Parameters
    ----------
    orbit_name : str
        Orbit file name.

    Returns
    -------
    time_start : datetime.datetime
        Validity start time.
    time_stop : datetime.datetime
        Validity stop time.
    """
    time_start = string_to_datetime(start_time_str_from_orbit_name(orbit_name))
    time_stop = string_to_datetime(stop_time_str_from_orbit_name(orbit_name))
    return time_start, time_stop


class S1Safe:
    """
    Sentinel-1 SAFE product reader.

    Parses the manifest and builds one ``S1Channel`` per polarization and swath (or per wave mode image) from the
    annotation, noise, calibration, RFI and measurement files of the product.

    Parameters
    ----------
    safe_path : str or pathlib.Path
        Path to the SAFE product directory.

    Attributes
    ----------
    path : pathlib.Path
        Path to the SAFE product directory.
    name : str
        SAFE product name.
    manifest : xml.etree.ElementTree.ElementTree
        Parsed ``manifest.safe``.
    mode : str
        Acquisition mode (e.g. ``IW``, ``EW``).
    product_type : str
        Product type (e.g. ``SLC``, ``GRDH``).

    Raises
    ------
    ValueError
        If ``safe_path`` does not exist.
    StopIteration
        If an annotation, noise, calibration or RFI file of a channel is missing.
    """
    def __init__(self, safe_path):
        self.path = Path(safe_path)
        if not self.path.exists():
            raise ValueError('Path does not exist: {}'.format(safe_path))
        self.name = self.path.name
        self.manifest = ET.parse(self.path / 'manifest.safe')
        self.mode = mode_from_safe_name(self.name)
        self.product_type = product_type_from_safe_name(self.name)
        self._channels = self._build_channels_dict()

    def _build_channels_dict(self):
        """
        Build the channels of the product.

        GRD products have a single swath named after the mode. Wave mode channels are discovered from the measurement
        files. A missing measurement file (annotation-only product) results in a channel without measurement.

        Returns
        -------
        dict
            Mapping from ``(pol, swath_id)``, or ``(pol, swath_id, img_num)`` for wave mode, to ``S1Channel``.
        """
        channels = dict()
        pol_list = polarizations_from_safe_name(self.name)
        if self.is_grd():
            swath_id_list = [self.mode]
        else:
            swath_id_list = subswaths_from_mode(self.mode)

        
        if self.mode == 'WV':
            for filename in os.listdir(os.path.join(self.path, 'measurement')):
                swath_id = filename.split('-')[1].upper()
                pol = filename.split('-')[3].upper()
                img_num = (filename.split('.')[0]).split('-')[8]

                annot_file = annotation_file_from_safe_path(self.path, pol, swath_id, img_num)
                noise_file = noise_file_from_safe_path(self.path, pol, swath_id, img_num)
                cal_file = calibration_file_from_safe_path(self.path, pol, swath_id, img_num)
                rfi_file = rfi_file_from_safe_path(self.path, pol, swath_id, img_num)

                try:
                    meas_file = measurement_file_from_safe_path(self.path, pol, swath_id, img_num)
                except StopIteration:
                    #In case of L1 Annotation product
                    meas_file = None
                channels[(pol, swath_id, img_num)] = S1Channel(pol, swath_id, annot_file, noise_file, cal_file, rfi_file, meas_file)
    
        else:
            for pol in pol_list:
                for swath_id in swath_id_list:
                    annot_file = annotation_file_from_safe_path(self.path, pol, swath_id)
                    noise_file = noise_file_from_safe_path(self.path, pol, swath_id)
                    cal_file = calibration_file_from_safe_path(self.path, pol, swath_id)
                    rfi_file = rfi_file_from_safe_path(self.path, pol, swath_id)
                    try:
                        meas_file = measurement_file_from_safe_path(self.path, pol, swath_id)
                    except StopIteration:
                        #In case of L1 Annotation product
                        meas_file = None
                    channels[ch_idx(pol, swath_id)] = S1Channel(pol, swath_id, annot_file, noise_file, cal_file, rfi_file, meas_file)
        
        return channels

    def is_grd(self):
        """
        Check whether the product is a GRD product.

        Returns
        -------
        bool
            ``True`` if the product is GRD.
        """
        return is_grd(self.name)

    def is_topsar(self):
        """
        Check whether the product was acquired in a TOPSAR mode (IW or EW).

        Returns
        -------
        bool
            ``True`` if the product is IW or EW.
        """
        return is_topsar(self.name)

    def is_stripmap(self):
        """
        Check whether the product was acquired in a stripmap mode.

        Returns
        -------
        bool
            ``True`` if the product is a stripmap product.
        """
        return is_stripmap(self.name)

    def is_wave(self):
        """
        Check whether the product was acquired in wave mode.

        Returns
        -------
        bool
            ``True`` if the mode is ``WV``, ``False`` otherwise.
        """
        return is_wave(self.name)

    def channel(self, pol, swath_id, img_num=None):
        """
        Return the channel of a given polarization and swath.

        Parameters
        ----------
        pol : str
            Polarization (e.g. ``VV``).
        swath_id : str
            Swath identifier (e.g. ``IW1``).
        img_num : str, optional
            Image number, used only for wave mode products. Default is ``None``.

        Returns
        -------
        S1Channel
            The requested channel.

        Raises
        ------
        KeyError
            If the channel does not exist in the product.
        """
        
        if self.mode == 'WV' and (img_num is not None):
            return self._channels[(pol, swath_id, img_num)]
        else:
            return self._channels[ch_idx(pol, swath_id)]

    def polarization_list(self):
        """
        Return the polarizations available in the product.

        Returns
        -------
        list of str
            Unique polarizations, in channel order.
        """
        pol_list = [k[0] for k in self._channels.keys()]
        return list(dict.fromkeys(pol_list))

    def swath_id_list(self):
        """
        Return the swath identifiers available in the product.

        Returns
        -------
        list of str
            Unique swath identifiers, in channel order.
        """
        pol_list = [k[1] for k in self._channels.keys()]
        return list(dict.fromkeys(pol_list))

    def read_quicklook(self):
        """
        Read the quick-look image of the product.

        Returns
        -------
        numpy.ndarray or None
            Content of ``preview/quick-look.png``, or ``None`` if the file does not exist.
        """
        ql_path = self.path / 'preview/quick-look.png'
        if not ql_path.exists():
            return None
        ql_image = Image.open(ql_path)
        return np.asarray(ql_image)

class S1Channel:
    """
    Single channel (polarization and swath) of a Sentinel-1 SAFE product.

    Parameters
    ----------
    pol : str
        Polarization (e.g. ``VV``).
    swath_id : str
        Swath identifier (e.g. ``IW1``).
    annnot_file : str or pathlib.Path
        Product annotation XML file.
    noise_file : str or pathlib.Path
        Noise annotation XML file.
    calibration_file : str or pathlib.Path
        Calibration annotation XML file.
    rfi_file : str or pathlib.Path
        RFI annotation XML file.
    meas_file : str or pathlib.Path, optional
        Measurement TIFF file. Default is ``None`` (annotation-only product).

    Attributes
    ----------
    pol : str
        Polarization.
    swath_id : str
        Swath identifier.
    annotation : xml.etree.ElementTree.ElementTree
        Parsed product annotation.
    noise : xml.etree.ElementTree.ElementTree
        Parsed noise annotation.
    calibration : xml.etree.ElementTree.ElementTree
        Parsed calibration annotation.
    rfi : xml.etree.ElementTree.ElementTree
        Parsed RFI annotation.
    measurement : pathlib.Path or None
        Measurement TIFF file, or ``None`` if not available.

    Raises
    ------
    ValueError
        If `meas_file` is given but does not exist.
    """
    def __init__(self, pol, swath_id, annnot_file, noise_file, calibration_file, rfi_file, meas_file=None):
        self.swath_id = swath_id
        self.pol = pol
        self.annotation = ET.parse(annnot_file)
        self.noise = ET.parse(noise_file)
        self.calibration = ET.parse(calibration_file)
        self.rfi = ET.parse(rfi_file)
        if meas_file is not None:
            meas_file = Path(meas_file)
            if not meas_file.exists():
                raise ValueError('File does not exist: {}'.format(meas_file))
            self.measurement = meas_file
        else:
            self.measurement = None

    def size(self):
        """
        Return the image size from the product annotation.

        Returns
        -------
        num_azi : int
            Number of azimuth lines.
        num_rng : int
            Number of range samples.
        """
        num_rng = int(self.annotation.find('imageAnnotation/imageInformation/numberOfSamples').text)
        num_azi = int(self.annotation.find('imageAnnotation/imageInformation/numberOfLines').text)
        return num_azi, num_rng

    @property
    def num_bursts(self):
        """Number of bursts listed in ``swathTiming/burstList`` of the product annotation."""
        return len(self.annotation.find('swathTiming/burstList'))

    def roi_from_burst(self, burst_idx):
        """
        Return the region of interest covering a burst.

        Parameters
        ----------
        burst_idx : int
            Zero-based burst index.

        Returns
        -------
        S1Roi
            ROI spanning ``linesPerBurst`` lines from ``burst_idx * linesPerBurst`` and ``samplesPerBurst`` samples
            from range sample 0.

        Raises
        ------
        ValueError
            If ``burst_idx`` is not lower than the number of bursts.
        """
        num_bursts = self.num_bursts
        if burst_idx >= num_bursts:
            raise ValueError('Invalid burst index: {}. Number of bursts is {}'.format(burst_idx, num_bursts))
        lines_per_burst = int(self.annotation.find('swathTiming/linesPerBurst').text)
        samples_per_burst = int(self.annotation.find('swathTiming/samplesPerBurst').text)
        return S1Roi(range_start_idx=0,
                     azi_start_idx=burst_idx * lines_per_burst,
                     num_range_samples=samples_per_burst,
                     num_azi_lines=lines_per_burst)

    def read_swath_bounds(self):
        """
        Read the swath merging bounds from the product annotation (GRD products).

        Returns
        -------
        dict of str to list of dict
            Mapping from swath identifier to its list of bounds, each with keys ``firstAzimuthLine``,
            ``lastAzimuthLine``, ``firstRangeSample`` and ``lastRangeSample`` (int).
        """
        swath_merge_dict = dict()
        for swathMerge in self.annotation.findall('swathMerging/swathMergeList/swathMerge'):
            swath_id = swathMerge.find('swath').text
            swath_merge_dict[swath_id] = list()
            for swathBound in swathMerge.findall('swathBoundsList/swathBounds'):
                swath_bounds_dict = dict()
                swath_bounds_dict['firstAzimuthLine'] = int(swathBound.findall('firstAzimuthLine')[0].text)
                swath_bounds_dict['lastAzimuthLine'] = int(swathBound.findall('lastAzimuthLine')[0].text)
                swath_bounds_dict['firstRangeSample'] = int(swathBound.findall('firstRangeSample')[0].text)
                swath_bounds_dict['lastRangeSample'] = int(swathBound.findall('lastRangeSample')[0].text)
                swath_merge_dict[swath_id].append(swath_bounds_dict)
        return swath_merge_dict

    def read_range_noise_vectors(self):
        """
        Read the range noise vectors of the channel.

        Returns
        -------
        list of dict
            Range noise vectors, see ``read_range_noise_vectors``.
        """
        return read_range_noise_vectors(self.noise)

    def read_azimuth_noise_vectors(self):
        """
        Read the azimuth noise vectors of the channel, grouped by swath.

        Returns
        -------
        dict of str to list of dict
            Azimuth noise vectors, see ``read_azimuth_noise_vectors``.
        """
        return read_azimuth_noise_vectors(self.noise)

    def read_calibration_vectors(self, calibration):
        """
        Read the calibration vectors of a given type for the channel.

        Parameters
        ----------
        calibration : str
            Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``.

        Returns
        -------
        list of dict
            Calibration vectors, see ``read_calibration_vectors``.
        """
        return read_calibration_vectors(self.calibration, calibration)

    def calibrate(self, data, roi, calibration=None):
        """
        Apply radiometric calibration to channel data over a region of interest.

        Parameters
        ----------
        data : numpy.ndarray
            Data over the ROI, with shape ``(roi.num_azi_lines, roi.num_range_samples)``.
        roi : S1Roi
            Region of interest covered by ``data``.
        calibration : str, optional
            Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``. Default is ``None`` (no
            calibration).

        Returns
        -------
        numpy.ndarray
            Calibrated data in linear power units, or ``data`` unchanged if ``calibration`` is ``None``.
        """
        return calibrate(self.annotation, self.calibration, data, roi, calibration)

    def read_data(self, roi=None, calibration=None):
        """
        Read measurement data over a region of interest, optionally calibrated.

        Parameters
        ----------
        roi : S1Roi, optional
            Region of interest. Default is ``None`` (whole image).
        calibration : str, optional
            Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``. Default is ``None`` (no
            calibration).

        Returns
        -------
        numpy.ndarray
            Data of the first raster band over the ROI (integer data are converted to float), or calibrated power if
            ``calibration`` is given.
        """
        with rasterio.open(self.measurement) as dataset:
            if roi is None:
                roi = S1Roi(0, 0, dataset.width, dataset.height)
            data = dataset.read(1, window=roi.as_window())
            if issubclass(data.dtype.type, np.integer):
                data = data.astype(float)
            return self.calibrate(data, roi, calibration)

    def read_noise(self, roi, calibration=None):
        """
        Compute the annotated noise over a region of interest, optionally calibrated.

        Parameters
        ----------
        roi : S1Roi or None
            Region of interest; ``None`` means the whole image.
        calibration : str, optional
            Calibration type, one of ``dn``, ``betaNought``, ``gamma``, ``sigmaNought``. Default is ``None`` (no
            calibration).

        Returns
        -------
        numpy.ndarray
            Noise amplitude map (see ``compute_noise_map``), or calibrated noise power if ``calibration`` is given.
        """
        data_size = self.size()
        if roi is None:
            roi = S1Roi(0, 0, data_size[1], data_size[0])
        noise = compute_noise_map(self.noise, data_size, roi=roi.as_tuple())
        return self.calibrate(noise, roi, calibration)

class S1Roi:
    """
    Rectangular region of interest in image coordinates.

    Parameters
    ----------
    range_start_idx : int
        First range sample.
    azi_start_idx : int
        First azimuth line.
    num_range_samples : int
        Number of range samples.
    num_azi_lines : int
        Number of azimuth lines.

    Attributes
    ----------
    range_start_idx : int
        First range sample.
    azi_start_idx : int
        First azimuth line.
    num_range_samples : int
        Number of range samples.
    num_azi_lines : int
        Number of azimuth lines.
    """
    def __init__(self, range_start_idx, azi_start_idx, num_range_samples, num_azi_lines):
        self.range_start_idx = range_start_idx
        self.azi_start_idx = azi_start_idx
        self.num_range_samples = num_range_samples
        self.num_azi_lines = num_azi_lines

    def __repr__(self):
        """Return a string listing the ROI fields."""
        return 'range_start_idx:{} azi_start_idx:{} num_range_samples:{} num_azi_lines:{}'.format(self.range_start_idx,
                                                                                                  self.azi_start_idx,
                                                                                                  self.num_range_samples,
                                                                                                  self.num_azi_lines)

    def as_tuple(self):
        """
        Return the ROI as a tuple.

        Returns
        -------
        tuple of int
            ``(range_start_idx, azi_start_idx, num_range_samples, num_azi_lines)``.
        """
        return self.range_start_idx, self.azi_start_idx, self.num_range_samples, self.num_azi_lines

    def as_window(self):
        """
        Return the ROI as a rasterio window.

        Returns
        -------
        rasterio.windows.Window
            Window with column offset, row offset, width and height taken from ``as_tuple``.
        """
        return Window(*self.as_tuple())

