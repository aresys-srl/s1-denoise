# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for parsing and validating Sentinel-1 Job Order XML files.

Provides the JobOrder class, which extracts processor configuration, input product
paths, auxiliary file references (AUX_PP1, AUX_CAL, AUX_INS), and output paths,
with coherence checks against the product manifest. Also includes utilities to match
L1 products with their corresponding L0N files.
"""
 
import xmltodict
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

from denoise.logger import denoise_logger

FIELDS                  = ["Processor_Name", "Version", "Stdout_Log_Level", "Stderr_Log_Level", "Test", "Breakpoint_Enable"]
INPUT_L0N_FILES         = ["IW_RAW__0N", "EW_RAW__0N"]
INPUT_PROCESSING_FILES  = ["IW_SLC__1A", "IW_SLC__1S", "EW_SLC__1A", "EW_SLC__1S", 
                           "IW_GRDH_1A", "IW_GRDH_1S", "IW_GRDM_1A", "IW_GRDM_1S",
                           "EW_GRDH_1A", "EW_GRDH_1S", "EW_GRDM_1A", "EW_GRDM_1S"]
INPUT_AUX_FILES         = ["AUX_PP1", "AUX_CAL", "AUX_INS"]
INPUT_FILE_TYPES        = INPUT_L0N_FILES + INPUT_PROCESSING_FILES + INPUT_AUX_FILES
OUTPUT_FILES_TYPES      = ["DENOISE"]

def xml_to_dict(xml_path: str):
    """Convert an XML file to a Python dictionary with lists preserved."""
    with open(xml_path, "r", encoding="utf-8") as f:
        xml_content = f.read()
    
    # parse XML, enabling list detection
    parsed = xmltodict.parse(xml_content, force_list={
        "Dynamic_Processing_Parameter",
        "Ipf_Proc",
        "Input",
        "Output",
        "File_Name"
    })
    
    # remove XML attributes like 'count' if you don’t need them
    def strip_attributes(obj):
        if isinstance(obj, dict):
            return {k: strip_attributes(v) for k, v in obj.items() if not k.startswith("@")}
        elif isinstance(obj, list):
            return [strip_attributes(item) for item in obj]
        else:
            return obj

    return strip_attributes(parsed)

class JobOrder:
    """
    Class containing parsed info from job order xml file
    """

    def __init__(self, joborder_path: Path):
        """Load job order and parse xml file"""

        if joborder_path.suffix != ".xml":
            raise RuntimeError(f"Job order {joborder_path} file is not an .xml file")
        else:
            data = xml_to_dict(joborder_path)

        # IPF conf
        ipf_conf = data['Ipf_Job_Order']['Ipf_Conf']
        self.mission_id = ipf_conf['List_of_Dynamic_Processing_Parameters']['Dynamic_Processing_Parameter'][0]['Value']
        self.log_level = ipf_conf['Stdout_Log_Level']

        # IPF input files
        ipf_proc = data['Ipf_Job_Order']['List_of_Ipf_Procs']['Ipf_Proc'][0]
        self.input_files = ipf_proc['List_of_Inputs']['Input']
        self.input_files_to_process = [ elem for elem in self.input_files if elem['File_Type'] in INPUT_PROCESSING_FILES ]
        self.input_l0n_files = [Path(elem['List_of_File_Names']['File_Name'][0]) for elem in self.input_files if elem['File_Type'] in INPUT_L0N_FILES]

        # auxiliary files
        pp1_list = [elem for elem in self.input_files if elem['File_Type'] == "AUX_PP1"]
        if len(pp1_list) != 0:
            self.aux_pp1_filepath = Path(pp1_list[0]['List_of_File_Names']['File_Name'][0])
            denoise_logger.info(f"AUX_PP1: {self.aux_pp1_filepath}")
        else:
            denoise_logger.error("Missing AUX_PP1 file in Job Order. Aborting...")
            sys.exit(128)

        cal_list = [elem for elem in self.input_files if elem['File_Type'] == "AUX_CAL"]
        if len(cal_list) != 0:
            self.aux_cal_filepath = Path(cal_list[0]['List_of_File_Names']['File_Name'][0])
            denoise_logger.info(f"AUX_CAL: {self.aux_cal_filepath}")
        else:
            denoise_logger.error("Missing AUX_CAL file in Job Order. Aborting...")
            sys.exit(128)

        ins_list = [elem for elem in self.input_files if elem['File_Type'] == "AUX_INS"]
        if len(ins_list) != 0:
            self.aux_ins_filepath = Path(ins_list[0]['List_of_File_Names']['File_Name'][0])
            denoise_logger.info(f"AUX_INS: {self.aux_ins_filepath}")
        else:
            denoise_logger.error("Missing AUX_INS file in Job Order. Aborting...")
            sys.exit(128)

        # output filepath
        self.output_path = Path(ipf_proc['List_of_Outputs']['Output'][0]['File_Name'][0])
        # the processor must not be able to write inside input folders
        input_dirs = [Path(f['List_of_File_Names']['File_Name'][0]) for f in self.input_files]
        if any(
            self.output_path == input_dir or self.output_path.is_relative_to(input_dir)
            for input_dir in input_dirs
        ):
            denoise_logger.error("Cannot write results in input folders. Aborting...")
            sys.exit(128)

        self.check_input_files_exist()
        self.check_manifest_file()

    def check_input_files_exist(self):
        missing = []

        for f in self.input_l0n_files:
            if not f.exists():
                missing.append(f)

        for f in [self.aux_pp1_filepath, self.aux_cal_filepath, self.aux_ins_filepath]:
            if not f.exists():
                missing.append(f)

        if missing:
            for f in missing:
                denoise_logger.error(f"Input file not found: {f}")
            sys.exit(128)

    def check_manifest_file(self):
    
        for prod in self.input_files_to_process:
            prod_name = Path(prod['List_of_File_Names']['File_Name'][0])
            manifest_path = prod_name / "manifest.safe"

            if not prod_name.exists():
                denoise_logger.error(f"Input product not found: {prod_name}. Aborting...")
                sys.exit(128)
            if not manifest_path.exists():
                denoise_logger.error(f"Manifest file not found: {manifest_path}. Aborting...")
                sys.exit(128)

            tree = ET.parse(manifest_path)
            root = tree.getroot()

            ns = {
                "safe": "http://www.esa.int/safe/sentinel-1.0"
            }

            for res in root.findall(".//safe:resource", ns):
                if res.get("role") == "AUX_INS":
                    manifest_ins = Path(res.get("name")).name
                    self.check_coherence(manifest_ins, self.aux_ins_filepath.name, prod_name.stem)
                elif res.get("role") == "AUX_PP1":
                    manifest_pp1 = Path(res.get("name")).name
                    self.check_coherence(manifest_pp1, self.aux_pp1_filepath.name, prod_name.stem)
                elif res.get("role") == "AUX_CAL":
                    manifest_cal = Path(res.get("name")).name
                    self.check_coherence(manifest_cal, self.aux_cal_filepath.name, prod_name.stem)

        return manifest_ins, manifest_pp1, manifest_cal
    
    def check_coherence(self, manifest, jo_aux_file, prod):
        if manifest != jo_aux_file:
            denoise_logger.warning(f"Using {jo_aux_file} for {prod}, while input data was processed with {manifest}. Calculated error could not be precise.")

def find_l0n(l1a: Path, l0n_list) -> Path | None:

    parts = l1a.stem.split('_')
    orbit_number, datatake_id = parts[-3], parts[-2]

    # look for element in list with same orbit_number and datatake_id
    for item in l0n_list:
        l0n_name = item.stem
        if orbit_number in l0n_name and datatake_id in l0n_name:
            return item # give back first match
    return None
