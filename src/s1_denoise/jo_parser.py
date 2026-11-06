# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for parsing and validating Sentinel-1 Job Order XML files.

Provides the ``JobOrder`` class, which extracts processor configuration, input product paths, auxiliary file
references (AUX_PP1, AUX_CAL, AUX_INS) and the output path, with coherence checks against the product manifest.
Also includes utilities to match L1 products with their corresponding L0N files.
"""
 
import xmltodict
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

from s1_denoise.logger import denoise_logger

FIELDS                  = ["Processor_Name", "Version", "Stdout_Log_Level", "Stderr_Log_Level", "Test", "Breakpoint_Enable"]
INPUT_L0N_FILES         = ["IW_RAW__0N", "EW_RAW__0N"]
INPUT_PROCESSING_FILES  = ["IW_SLC__1A", "IW_SLC__1S", "EW_SLC__1A", "EW_SLC__1S", 
                           "IW_GRDH_1A", "IW_GRDH_1S", "IW_GRDM_1A", "IW_GRDM_1S",
                           "EW_GRDH_1A", "EW_GRDH_1S", "EW_GRDM_1A", "EW_GRDM_1S"]
INPUT_AUX_FILES         = ["AUX_PP1", "AUX_CAL", "AUX_INS"]
INPUT_FILE_TYPES        = INPUT_L0N_FILES + INPUT_PROCESSING_FILES + INPUT_AUX_FILES
OUTPUT_FILES_TYPES      = ["DENOISE"]

def xml_to_dict(xml_path: str):
    """
    Convert an XML file to a Python dictionary, preserving lists and dropping XML attributes.

    Elements that may be repeated in a Job Order (``Dynamic_Processing_Parameter``, ``Ipf_Proc``, ``Input``,
    ``Output``, ``File_Name``) are always returned as lists, even when they occur once. Attribute keys (those
    starting with ``@``, e.g. ``@count``) are removed recursively.

    Parameters
    ----------
    xml_path : str or pathlib.Path
        Path to the XML file.

    Returns
    -------
    dict
        Nested dictionary representation of the XML document.
    """
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
        """Recursively remove keys starting with ``@`` (XML attributes) from dicts and lists."""
        if isinstance(obj, dict):
            return {k: strip_attributes(v) for k, v in obj.items() if not k.startswith("@")}
        elif isinstance(obj, list):
            return [strip_attributes(item) for item in obj]
        else:
            return obj

    return strip_attributes(parsed)

class JobOrder:
    """
    Parsed content of a Sentinel-1 Job Order XML file.

    On construction the Job Order is parsed and validated: the AUX_PP1, AUX_CAL and AUX_INS inputs must be present,
    the output path must not lie inside any input path, the L0N and auxiliary inputs must exist on disk and each
    L1 input product must contain a ``manifest.safe``. Any failure is logged and terminates the process with exit
    code 128.

    Parameters
    ----------
    joborder_path : pathlib.Path
        Path to the Job Order XML file.

    Attributes
    ----------
    mission_id : str
        Value of the first dynamic processing parameter (the mission identifier, e.g. ``S1A``).
    log_level : str
        Logging level from ``Stdout_Log_Level``.
    input_files : list of dict
        All ``Input`` entries of the first ``Ipf_Proc``.
    input_files_to_process : list of dict
        ``Input`` entries whose ``File_Type`` is an L1 product type (L1A/L1S SLC and GRD in IW/EW modes).
    input_l0n_files : list of pathlib.Path
        Paths of the L0N input products (``IW_RAW__0N``, ``EW_RAW__0N``).
    aux_pp1_filepath : pathlib.Path
        Path of the AUX_PP1 auxiliary file.
    aux_cal_filepath : pathlib.Path
        Path of the AUX_CAL auxiliary file.
    aux_ins_filepath : pathlib.Path
        Path of the AUX_INS auxiliary file.
    output_path : pathlib.Path
        Output directory (first file name of the first ``Output`` entry).

    Raises
    ------
    RuntimeError
        If ``joborder_path`` does not have an ``.xml`` suffix.
    SystemExit
        With code 128 if any of the validation checks fails.
    """

    def __init__(self, joborder_path: Path):
        """Parse the Job Order file and validate its content."""

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
        """
        Check that the L0N input products and the auxiliary files exist on disk.

        Every missing file is logged before exiting.

        Raises
        ------
        SystemExit
            With code 128 if at least one file is missing.
        """
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
        """
        Check the L1 input products and the coherence of their auxiliary files with the Job Order.

        For each product to process, verify that the product directory and its ``manifest.safe`` exist, then compare
        the AUX_INS, AUX_PP1 and AUX_CAL names referenced in the manifest with those given in the Job Order. A mismatch,
        or an auxiliary file not referenced in the manifest, only produces a warning.

        Raises
        ------
        SystemExit
            With code 128 if a product directory or its manifest file does not exist.
        """
    
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

            jo_aux_files = {
                "AUX_INS": self.aux_ins_filepath.name,
                "AUX_PP1": self.aux_pp1_filepath.name,
                "AUX_CAL": self.aux_cal_filepath.name,
            }
            checked_roles = set()

            for res in root.findall(".//safe:resource", ns):
                role = res.get("role")
                if role in jo_aux_files:
                    manifest_aux = Path(res.get("name")).name
                    self.check_coherence(manifest_aux, jo_aux_files[role], prod_name.stem)
                    checked_roles.add(role)

            for role in jo_aux_files.keys() - checked_roles:
                denoise_logger.warning(f"{role} not referenced in the manifest of {prod_name.stem}: "
                                       f"cannot verify that {jo_aux_files[role]} was used to process the input data.")
    
    def check_coherence(self, manifest, jo_aux_file, prod):
        """
        Log a warning if the auxiliary file used to generate a product differs from the Job Order one.

        Parameters
        ----------
        manifest : str
            Auxiliary file name referenced in the product manifest.
        jo_aux_file : str
            Auxiliary file name given in the Job Order.
        prod : str
            Product name, used in the warning message.
        """
        if manifest != jo_aux_file:
            denoise_logger.warning(f"Using {jo_aux_file} for {prod}, while input data was processed with {manifest}. Calculated error could not be precise.")

def _safe_name_fields(safe_path: Path):
    """
    Return the fields identifying the acquisition from a Sentinel-1 SAFE product name.

    Parameters
    ----------
    safe_path : pathlib.Path
        Path of the SAFE product, named ``MMM_BB_TTTR_LFPP_<start>_<stop>_<orbit>_<datatake>_<crc>[.SAFE]``.

    Returns
    -------
    tuple of str
        Mission, acquisition mode, absolute orbit number and mission datatake ID.
    """
    name = safe_path.stem
    parts = name.split('_')
    return name[0:3].upper(), name[4:6].upper(), parts[-3], parts[-2].upper()


def find_l0n(l1a: Path, l0n_list) -> Path | None:
    """
    Find the L0N product associated with an L1 product.

    The mission, acquisition mode, absolute orbit number and mission datatake ID are read from the product names
    and must all match exactly. If several L0N products match, the first one is returned and a warning is issued.

    Parameters
    ----------
    l1a : pathlib.Path
        Path of the L1 SAFE product.
    l0n_list : list of pathlib.Path
        Paths of the candidate L0N products.

    Returns
    -------
    pathlib.Path or None
        The first matching L0N product, or ``None`` if no match is found.
    """

    l1a_fields = _safe_name_fields(l1a)

    # look for L0N products of the same mission, mode, orbit and datatake
    matches = [item for item in l0n_list if _safe_name_fields(item) == l1a_fields]

    if len(matches) > 1:
        denoise_logger.warning(f"Multiple L0N products match {l1a.stem}: using {matches[0].stem}")

    return matches[0] if matches else None # give back first match
