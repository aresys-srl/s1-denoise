# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Provide the AuxiliaryFiles class for parsing and accessing the Sentinel-1 auxiliary files (AUX_CAL, AUX_PP1,
AUX_INS).

Handles the reading of calibration parameters, processing parameters, application LUTs and internal calibration
references, exposing them through dedicated getter methods for use in the denoising pipeline.
"""

from lxml import etree 
from pathlib import Path
import numpy as np
import sys

from s1_denoise.logger import denoise_logger

class AuxiliaryFiles:
    """
    Parsed content of the AUX_PP1, AUX_CAL and AUX_INS auxiliary files of a mission.

    All three files are read at construction time.

    Parameters
    ----------
    mission_id : str
        Mission identifier (e.g. ``"S1A"``), used to build the auxiliary file names.
    pp1_path : pathlib.Path
        Path of the AUX_PP1 SAFE directory, containing ``data/<mission>-aux-pp1.xml``.
    cal_path : pathlib.Path
        Path of the AUX_CAL SAFE directory, containing ``data/<mission>-aux-cal.xml``.
    ins_path : pathlib.Path
        Path of the AUX_INS SAFE directory, containing ``data/<mission>-aux-ins.xml``.

    Attributes
    ----------
    mission_id : str
        Mission identifier.
    pp1_filepath, cal_filepath, ins_filepath : pathlib.Path
        Paths of the AUX_PP1, AUX_CAL and AUX_INS XML files.
    calibration_parameters : dict
        AUX_CAL calibration parameters, keyed by ``(swath, polarisation)``.
    processing_parameters : dict
        AUX_PP1 processing parameters (``"gain"`` and ``"alpha_value"``), keyed by ``(product_id, swath)``.
    app_lut_values : dict
        AUX_PP1 scaling LUTs, keyed by application LUT identifier.
    pg_references : dict
        AUX_INS ``pgReference`` XML elements, keyed by ``(swath, polarisation)``.
    expected_noise_values : dict
        AUX_INS expected ``noise`` XML elements, keyed by ``(swath, polarisation)``.
    azimuth_steering_rates : dict
        AUX_INS azimuth steering rates [deg/s], keyed by swath.

    Raises
    ------
    SystemExit
        If any of the auxiliary files contains no usable entries (exit code 128).
    """
    
    def __init__(self, mission_id, pp1_path: Path, cal_path: Path, ins_path: Path):
        
        self.mission_id = mission_id
        self.pp1_filepath = pp1_path / f'data/{self.mission_id.lower()}-aux-pp1.xml'
        self.cal_filepath = cal_path / f'data/{self.mission_id.lower()}-aux-cal.xml'
        self.ins_filepath = ins_path / f'data/{self.mission_id.lower()}-aux-ins.xml'

        self.calibration_parameters = {}
        self.read_from_aux_cal()

        self.processing_parameters = {}
        self.app_lut_values = {}
        self.read_from_aux_pp1()

        self.pg_references = {}
        self.expected_noise_values = {}
        self.azimuth_steering_rates = {}
        self.read_from_aux_ins()


    def read_from_aux_cal(self):
        """
        Read the calibration parameters from the AUX_CAL file into `calibration_parameters`.

        For each ``(swath, polarisation)``, the azimuth antenna pattern values and count, the azimuth angle increment, the
        noise calibration factor and the absolute calibration constant are stored.

        Raises
        ------
        SystemExit
            If no calibration parameters are found (exit code 128).
        """
        
        tree = etree.parse(self.cal_filepath.resolve())
        root = tree.getroot()

        for cal_params in root.findall(".//calibrationParams"):
            swath = cal_params.find("swath").text
            polarisation = cal_params.find("polarisation").text
            aap_values = cal_params.find("azimuthAntennaPattern/values")
            aap_values_text = aap_values.text.strip()

            self.calibration_parameters[(swath, polarisation)] = {
                "aap_gain_count": int(aap_values.attrib['count']),
                "aap_gain": [float(val) for val in aap_values_text.split()],
                "azi_angle_incr": float(cal_params.find("azimuthAntennaPattern/azimuthAngleIncrement").text),
                "noise_calibration_factor": float(cal_params.find("noiseCalibrationFactor").text),
                "absolute_calibration_constant": float(cal_params.find("absoluteCalibrationConstant").text)
            }

        # if no matching swath/polarisation is found
        if not self.calibration_parameters:
            denoise_logger.error(f"Error reading calibration file. Aborting...")
            sys.exit(128)
    
    def read_from_aux_pp1(self):
        """
        Read the application LUTs and the processing parameters from the AUX_PP1 file.

        Scaling LUTs are stored in `app_lut_values`; the per-swath gains (``swathParams``) and azimuth window coefficients
        (``azimuthParams``) are stored in `processing_parameters`.

        Raises
        ------
        SystemExit
            If no processing parameters are found (exit code 128).
        """
        
        tree = etree.parse(self.pp1_filepath.resolve())
        root = tree.getroot()

        for app_lut in root.findall(".//applicationLut"):
            app_id = app_lut.findtext("applicationLutId")
            scaling_luts = []

            for lut in app_lut.findall("scalingLutList/scalingLut"):
                output_pixels = lut.findtext("outputPixels")
                inc_angle_start = float(lut.findtext("incidenceAngleStart"))
                angle_increment = float(lut.findtext("angleIncrement"))

                # Split and convert numeric values
                values_text = lut.findtext("values")
                values = [float(v) for v in values_text.split()] if values_text else []

                scaling_luts.append({
                    "outputPixels": output_pixels,
                    "incidenceAngleStart": inc_angle_start,
                    "angleIncrement": angle_increment,
                    "values": values
                })

            self.app_lut_values.update({
                app_id: scaling_luts
            })

        # Find the calibrationParams
        for product in root.findall(".//product"):
            prod_id = product.find("productId").text
                
            for swath_param in product.findall(".//swathParams"): # swathParams is present only in slcProcParams
                swath = swath_param.find("swath").text

                # for pol in pp1_gain_map:
                self.processing_parameters[(prod_id, swath)] = {
                    "gain": list(swath_param.find("gain").text.split())                    
                }          

            for azi_param in product.findall('.//azimuthParams'): # azimuthParams is present only in postProcParams
                swath = azi_param.find("swath").text
                self.processing_parameters[(prod_id, swath)].update({
                    "alpha_value": float(azi_param.find('windowCoefficient').text)
                })
            

        # if no matching swath/polarisation is found
        if not self.processing_parameters:
            denoise_logger.error(f"Error reading PP1 file. Aborting...")
            sys.exit(128)

    def read_from_aux_ins(self):
        """
        Read the internal calibration parameters and azimuth steering rates from the AUX_INS file.

        PG references and expected noise values are stored in `pg_references` and `expected_noise_values`; azimuth
        steering rates [deg/s] in `azimuth_steering_rates`.

        Raises
        ------
        SystemExit
            If no internal calibration parameters are found (exit code 128).
        """
        
        tree = etree.parse(self.ins_filepath.resolve())
        root = tree.getroot()

        for int_cal_param in root.findall(".//internalCalibrationParams"):
            swath = int_cal_param.findtext("swath")
            pol = int_cal_param.findtext("polarisation")
            pg_ref = int_cal_param.find("pgReference")
            expected_noise = int_cal_param.find("noise")

            self.pg_references.update({
                (swath, pol): pg_ref
            })

            self.expected_noise_values.update({
                (swath, pol): expected_noise
            })

        for swath_param in root.findall(".//swathParams"):
            swath = swath_param.findtext("swath")
            steering_rate = swath_param.findtext("radarParams/azimuthSteeringRate")

            if steering_rate is not None:
                self.azimuth_steering_rates.update({
                    swath: float(steering_rate)     # [deg/s]
                })

        # if no matching swath/polarisation is found
        if not self.pg_references:
            denoise_logger.error(f"Error reading INS file. Aborting...")
            sys.exit(128)
    
    def get_gain_from_pp1(self, product_id, subswath, pol):
        """
        Return the processing gain from AUX_PP1 for a product type, subswath and polarization.

        Parameters
        ----------
        product_id : str
            AUX_PP1 product identifier (e.g. ``"IW_SLC__1"``).
        subswath : str
            Subswath identifier.
        pol : str
            Polarization, one of ``"HH"``, ``"HV"``, ``"VV"``, ``"VH"``.

        Returns
        -------
        float
            Gain value.
        """

        pp1_gain_map = {
            "HH": 0,
            "HV": 1,
            "VV": 2,
            "VH": 3
        }

        gain_value = float(self.processing_parameters.get((product_id, subswath))['gain'][pp1_gain_map[pol]])
        denoise_logger.debug(f"Gain value from AUX_PP1: {gain_value}")

        return gain_value
    
    def get_alpha_from_pp1(self, product_id, subswath):
        """
        Return the azimuth window coefficients (``windowCoefficient``) from AUX_PP1.

        Parameters
        ----------
        product_id : str
            AUX_PP1 product identifier (e.g. ``"IW_GRDH_1"``).
        subswath : str
            Subswath identifier.

        Returns
        -------
        alpha_value : float
            Window coefficient of the given product type.
        slc_alpha_value : float
            Window coefficient of the SLC product of the same mode (``<mode>_SLC__1``).
        """

        alpha_value = float(self.processing_parameters.get((product_id, subswath))['alpha_value'])
        slc_alpha_value = float(self.processing_parameters.get((product_id[:2] + '_SLC__1', subswath))['alpha_value'])
        denoise_logger.debug(f"Window coefficient for {product_id}, {subswath}: {alpha_value}")

        return alpha_value, slc_alpha_value

    def get_abs_cal(self, swath: str, polarisation: str) -> float:
        """
        Return the absolute calibration constant from AUX_CAL.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        float
            Absolute calibration constant.
        """

        acf = float(self.calibration_parameters.get((swath, polarisation))['absolute_calibration_constant'])
        return acf

    def get_noise_cal(self, swath: str, polarisation: str) -> float:
        """
        Return the noise calibration factor k_N from AUX_CAL.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        float
            Noise calibration factor.
        """

        k_n = float(self.calibration_parameters.get((swath, polarisation))['noise_calibration_factor'])
        return k_n
    
    def get_aap_gain(self, swath, polarisation):
        """
        Return the azimuth antenna pattern values from AUX_CAL.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        numpy.ndarray
            Azimuth antenna pattern values, as annotated (dB).
        """

        aap_gain = np.array(self.calibration_parameters.get((swath, polarisation))['aap_gain'])
        return aap_gain
    
    def get_aap_gain_count(self, swath, polarisation):
        """
        Return the number of azimuth antenna pattern values from AUX_CAL.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        int
            Number of azimuth antenna pattern values.
        """

        aap_gain_count = int(self.calibration_parameters.get((swath, polarisation))['aap_gain_count'])
        return aap_gain_count
    
    def get_azi_angle_incr(self, swath, polarisation):
        """
        Return the azimuth angle increment of the azimuth antenna pattern from AUX_CAL.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        float
            Azimuth angle increment, as annotated.
        """

        azi_angle_incr = float(self.calibration_parameters.get((swath, polarisation))['azi_angle_incr'])
        return azi_angle_incr
    
    def get_dn_value(self, mode, product_type):
        """
        Return the digital number scaling value (betaNought * calibrationFactor) from the AUX_PP1 application LUT.

        The first value of the ``<mode>_Default`` scaling LUT matching the output pixel type of the product is returned:
        16 bit signed integer for SLC, 16 bit unsigned integer for GRD, 8 bit unsigned integer otherwise.

        Parameters
        ----------
        mode : str
            Acquisition mode (e.g. ``"IW"``).
        product_type : str
            Product type (e.g. ``"SLC"``, ``"GRDH"``).

        Returns
        -------
        float
            Digital number value, used to scale the output noise vectors.
        """

        if product_type == "SLC":
            output_pixels = "16 bit Signed Integer"
        elif product_type.startswith("GRD"):
            output_pixels = "16 bit Unsigned Integer"
        else:
            output_pixels = "8 bit Unsigned Integer"

        dn_value = next(
                        (d["values"][0] for d in self.app_lut_values[f"{mode}_Default"] if d["outputPixels"] == output_pixels),
                        None
                    )


        return float(dn_value)
    
    def get_pg_reference(self, swath, polarisation):
        """
        Return the magnitude of the PG reference from AUX_INS.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        float
            Euclidean norm of the two ``pgReference`` components.
        """

        val = np.array([float(self.pg_references.get((swath, polarisation))[0].text), 
                        float(self.pg_references.get((swath, polarisation))[1].text)])

        return np.linalg.norm(val)
    
    def get_expected_noise_value(self, swath, polarisation):
        """
        Return the expected noise power from AUX_INS, used as reference for noise pulse outlier rejection.

        Parameters
        ----------
        swath : str
            Swath identifier.
        polarisation : str
            Polarization (e.g. ``"VV"``).

        Returns
        -------
        float
            Expected noise power (linear scale).
        """

        val = float(self.expected_noise_values.get((swath, polarisation)).text)

        return val

    def get_azimuth_steering_rate(self, swath):
        """
        Return the azimuth antenna steering rate from AUX_INS.

        Parameters
        ----------
        swath : str
            Swath identifier.

        Returns
        -------
        float
            Azimuth steering rate [rad/s].

        Raises
        ------
        ValueError
            If no steering rate is annotated for `swath`.
        """

        if swath not in self.azimuth_steering_rates:
            raise ValueError(f"Azimuth steering rate for swath {swath} not found in AUX_INS file")

        return np.deg2rad(self.azimuth_steering_rates[swath])