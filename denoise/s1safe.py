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

from arepytools.timing.precisedatetime import PreciseDateTime

_subswaths = {'EW': ('EW1', 'EW2', 'EW3', 'EW4', 'EW5'),
              'IW': ('IW1', 'IW2', 'IW3'),
              'WV': ('WV1', 'WV2')}

_pols = {'DV': ('VV', 'VH'),
         'DH': ('HH', 'HV'),
         'SH': ('HH',),
         'SV': ('VV',)}

calibration_types = ('dn', 'betaNought', 'gamma', 'sigmaNought')

def ch_idx(pol, swath_id):
    return pol, swath_id

def read_calibration_vectors(calibration_et_root, calibration):
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
    for vec_idx, azi_noise_vec in enumerate(azi_noise_vec_list):
        if (azi_noise_vec['firstAzimuthLine'] <= line_idx) and (azi_noise_vec['lastAzimuthLine'] >= line_idx):
            return vec_idx
    return None


def read_range_noise_vectors(noise_et_root):
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
    Compute a 2D noise map given noise vectors and region of interest.

    Args:
        noise_et_root: XML root containing noise metadata.
        data_size: Tuple (num_azimuth_lines, num_range_samples)
        roi: (start_sample, start_line, width, height)
    Returns:
        2D numpy array of noise values (sqrt of interpolated LUTs)
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
    if is_stripmap_mode(mode_name):
        return (mode_name,)
    return _subswaths[mode_name]


def mode_from_safe_name(safe_name):
    return safe_name[4:6]


def product_type_from_safe_name(safe_name):
    tmp = safe_name[6:11]
    tmp = tmp.replace('_', '')
    return tmp


def polarizations_from_safe_name(safe_name):
    return _pols[safe_name[14:16]]


def start_time_str_from_safe_name(safe_name):
    return safe_name[17:32]


def stop_time_str_from_safe_name(safe_name):
    return safe_name[33:48]


def is_topsar_mode(mode_name):
    if mode_name == 'EW' or mode_name == 'IW':
        return True
    return False


def is_topsar(safe_name):
    mode = mode_from_safe_name(safe_name)
    return is_topsar_mode(mode)


def is_stripmap_mode(mode_name):
    if mode_name[:1] == 'S':
        return True
    return False


def is_stripmap(safe_name):
    mode = mode_from_safe_name(safe_name)
    return is_stripmap_mode(mode)


def is_wave(safe_name):
    mode = mode_from_safe_name(safe_name)
    if mode == 'WV':
        return True


def is_grd(safe_name):
    prod = product_type_from_safe_name(safe_name)
    if prod[:-2] == 'GR':
        return True
    return False


def measurement_file_from_safe_path(safe_path, pol, swath, img_num=None):
    safe_path = Path(safe_path)
    if (swath in ['WV1', 'WV2']) and (img_num is not None):
        return next(safe_path.glob('measurement/s1*-{}-*-{}-*-{}.tiff'.format(swath.lower(), pol.lower(), img_num)))
    else:
        return next(safe_path.glob('measurement/s1*-{}-*-{}-*.tiff'.format(swath.lower(), pol.lower())))


def calibration_file_from_safe_path(safe_path, pol, swath, img_num=None):
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
    '''
    Datetime representation of SAFE datetime string.

            Parameters:
                    safe_datetime (str): String like '20211112T230710'
            Returns:
                    dt (datetime): The datetime object
    '''
    dt = datetime(year=int(safe_datetime[:4]),
                  month=int(safe_datetime[4:6]),
                  day=int(safe_datetime[6:8]),
                  hour=int(safe_datetime[9:11]),
                  minute=int(safe_datetime[11:13]),
                  second=int(safe_datetime[13:15]))
    return dt


def datetime_to_string(dt):
    '''
    SAFE datetime string from datetime object.

            Parameters:
                    dt (datetime): Datetime object
            Returns:
                    safe_datetime_str (str): The SAFE datetime string representation
    '''
    safe_datetime_str = '{}{}{}T{}{}{}'.format(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    return safe_datetime_str


def time_from_safe_name(safe_name):
    time_start = string_to_datetime(start_time_str_from_safe_name(safe_name))
    time_stop = string_to_datetime(stop_time_str_from_safe_name(safe_name))
    return time_start, time_stop


def start_time_str_from_orbit_name(orbit_name):
    return orbit_name[42:57]


def stop_time_str_from_orbit_name(orbit_name):
    return orbit_name[58:73]


def get_time_from_orbit_name(orbit_name):
    time_start = string_to_datetime(start_time_str_from_orbit_name(orbit_name))
    time_stop = string_to_datetime(stop_time_str_from_orbit_name(orbit_name))
    return time_start, time_stop


class S1Safe:
    def __init__(self, safe_path, img_num=None):
        self.path = Path(safe_path)
        if not self.path.exists():
            raise ValueError('Path does not exist: {}'.format(safe_path))
        self.name = self.path.name
        self.manifest = ET.parse(self.path / 'manifest.safe')
        self.mode = mode_from_safe_name(self.name)
        self.product_type = product_type_from_safe_name(self.name)
        self._channels = self._build_channels_dict()
        self.ads_header = self.get_ads_header()

    def _build_channels_dict(self):
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

    def get_ads_header(self):
        ads_header = []
        return ads_header

    def is_grd(self):
        return is_grd(self.name)

    def is_topsar(self):
        return is_topsar(self.name)

    def is_stripmap(self):
        return is_stripmap(self.name)

    def is_wave(self):
        return is_wave(self.name)

    def channel(self, pol, swath_id, img_num=None):
        
        if self.mode == 'WV' and (img_num is not None):
            return self._channels[(pol, swath_id, img_num)]
        else:
            return self._channels[ch_idx(pol, swath_id)]

    def polarization_list(self):
        pol_list = [k[0] for k in self._channels.keys()]
        return list(dict.fromkeys(pol_list))

    def swath_id_list(self):
        pol_list = [k[1] for k in self._channels.keys()]
        return list(dict.fromkeys(pol_list))

    def read_quicklook(self):
        ql_path = self.path / 'preview/quick-look.png'
        if not ql_path.exists():
            return None
        ql_image = Image.open(ql_path)
        return np.asarray(ql_image)

class S1Channel:
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
                ValueError('File does not exist: {}'.format(meas_file))
            self.measurement = meas_file
        else:
            self.measurement = None

    def size(self):
        num_rng = int(self.annotation.find('imageAnnotation/imageInformation/numberOfSamples').text)
        num_azi = int(self.annotation.find('imageAnnotation/imageInformation/numberOfLines').text)
        return num_azi, num_rng

    @property
    def num_bursts(self):
        return len(self.annotation.find('swathTiming/burstList'))

    def roi_from_burst(self, burst_idx):
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
        return read_range_noise_vectors(self.noise)

    def read_azimuth_noise_vectors(self):
        return read_azimuth_noise_vectors(self.noise)

    def read_calibration_vectors(self, calibration):
        return read_calibration_vectors(self.calibration, calibration)

    def calibrate(self, data, roi, calibration=None):
        return calibrate(self.annotation, self.calibration, data, roi, calibration)

    def read_data(self, roi=None, calibration=None):
        with rasterio.open(self.measurement) as dataset:
            if roi is None:
                roi = S1Roi(0, 0, dataset.width, dataset.height)
            data = dataset.read(1, window=roi.as_window())
            if issubclass(data.dtype.type, np.integer):
                data = data.astype(float)
            return self.calibrate(data, roi, calibration)

    def read_noise(self, roi, calibration=None):
        data_size = self.size()
        if roi is None:
            roi = S1Roi(0, 0, data_size[1], data_size[0])
        noise = compute_noise_map(self.noise, data_size, roi=roi.as_tuple())
        return self.calibrate(noise, roi, calibration)

class S1Roi:
    def __init__(self, range_start_idx, azi_start_idx, num_range_samples, num_azi_lines):
        self.range_start_idx = range_start_idx
        self.azi_start_idx = azi_start_idx
        self.num_range_samples = num_range_samples
        self.num_azi_lines = num_azi_lines

    def __repr__(self):
        return 'range_start_idx:{} azi_start_idx:{} num_range_samples:{} num_azi_lines:{}'.format(self.range_start_idx,
                                                                                                  self.azi_start_idx,
                                                                                                  self.num_range_samples,
                                                                                                  self.num_azi_lines)

    def as_tuple(self):
        return self.range_start_idx, self.azi_start_idx, self.num_range_samples, self.num_azi_lines

    def as_window(self):
        return Window(*self.as_tuple())

