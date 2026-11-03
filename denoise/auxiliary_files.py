# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module providing the AuxiliaryFiles class for parsing and accessing Sentinel-1
auxiliary configuration files (AUX_CAL, AUX_PP1, AUX_INS).

Handles reading of calibration parameters, processing parameters, application LUTs,
and internal calibration references, exposing them through dedicated getter methods
for use in the denoising pipeline.
"""

from lxml import etree 
from pathlib import Path
import numpy as np

from denoise.logger import denoise_logger

class AuxiliaryFiles:
    
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
        self.read_from_aux_ins()


    def read_from_aux_cal(self):
        """Read from appropriate AUX_CAL file"""
        
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
            quit()
    
    def read_from_aux_pp1(self):
        """Read from appropriate AUX_PP1 file"""
        
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
            quit()

    def read_from_aux_ins(self):
        """Read from appropriate AUX_INS file"""
        
        tree = etree.parse(self.ins_filepath.resolve())
        root = tree.getroot()

        for int_cal_param in root.findall(".//internalCalibrationParams"):
            swath = int_cal_param.findtext("swath")
            pol = int_cal_param.findtext("polarisation")
            pg_ref = int_cal_param.find("pgReference")

            self.pg_references.update({
                (swath, pol): pg_ref
            })

        # if no matching swath/polarisation is found
        if not self.processing_parameters:
            denoise_logger.error(f"Error reading INS file. Aborting...")
            quit()
    
    def get_gain_from_pp1(self, product_id, subswath, pol):
        """Return windowCoefficient for given product_id, subswath and polarization from proper aux-pp1 file"""

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
        """Return windowCoefficient for given product_id and subswath from proper AUX_PP1 file"""

        alpha_value = float(self.processing_parameters.get((product_id, subswath))['alpha_value'])
        slc_alpha_value = float(self.processing_parameters.get((product_id[:2] + '_SLC__1', subswath))['alpha_value'])
        denoise_logger.debug(f"Window coefficient for {product_id}, {subswath}: {alpha_value}")

        return alpha_value, slc_alpha_value

    def get_abs_cal(self, swath: str, polarisation: str) -> float:
        """Return absolute calibration constant from proper AUX_CAL file"""

        acf = float(self.calibration_parameters.get((swath, polarisation))['absolute_calibration_constant'])
        return acf

    def get_noise_cal(self, swath: str, polarisation: str) -> float:
        """Return k_n parameter written in AUX_CAL file"""        

        k_n = float(self.calibration_parameters.get((swath, polarisation))['noise_calibration_factor'])
        return k_n
    
    def get_aap_gain(self, swath, polarisation):

        aap_gain = np.array(self.calibration_parameters.get((swath, polarisation))['aap_gain'])
        return aap_gain
    
    def get_aap_gain_count(self, swath, polarisation):

        aap_gain_count = int(self.calibration_parameters.get((swath, polarisation))['aap_gain_count'])
        return aap_gain_count
    
    def get_azi_angle_incr(self, swath, polarisation):

        azi_angle_incr = float(self.calibration_parameters.get((swath, polarisation))['azi_angle_incr'])
        return azi_angle_incr
    
    def get_dn_value(self, mode, product_type):
        '''Return digital number value (= betaNought * calibrationFactor) written in AUX_PP1 file for conversion in denoise output file'''

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

        val = np.array([float(self.pg_references.get((swath, polarisation))[0].text), 
                        float(self.pg_references.get((swath, polarisation))[1].text)])

        return np.linalg.norm(val)