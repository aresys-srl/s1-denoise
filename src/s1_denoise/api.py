# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Python entry points of the denoising processor, re-exported at package level.

- `run_job_order` processes all the L1 products listed in a Job Order and writes the output noise XML files, as
  the ``denoise`` command does;
- `reconstruct_noise` reconstructs the noise vectors of a single L1 product from explicit paths, without a Job
  Order, and returns them in memory without writing any file.

Unlike the command line interface, these functions do not map errors to exit codes: errors detected by the
processor are raised as `DenoiseError` subclasses (see `s1_denoise.exceptions`), and any other exception raised during
processing is propagated to the caller.
"""

from pathlib import Path

from perseo_core.timing import PreciseDateTime

from s1_denoise.logger import denoise_logger
from s1_denoise.noise_profiles import generate_noise_profiles, print_exec_time
from s1_denoise.jo_parser import JobOrder, find_l0n
from s1_denoise.auxiliary_files import AuxiliaryFiles
from s1_denoise.output import write_to_xml, OutputNoiseVectors_SLC, OutputNoiseVectors_GRD
from s1_denoise.error_calculation import calculate_error
from s1_denoise.exceptions import InputNotFoundError


def run_job_order(job_order_path: str | Path) -> list[Path]:
    """
    Process all the L1 products listed in a Job Order and write the output noise XML files.

    The Job Order is parsed and validated and its log level is applied. For each L1 product the associated L0N
    product is looked up, noise vectors are reconstructed and written as XML files in the output directory. If the
    product contains the corresponding nominal noise annotation, the mean and maximum absolute errors (dB) are
    logged.

    Parameters
    ----------
    job_order_path : str or pathlib.Path
        Path to the Job Order XML file.

    Returns
    -------
    list of pathlib.Path
        Paths of the written output noise XML files.

    Raises
    ------
    JobOrderError
        If the Job Order is invalid (see `JobOrder`).
    InputNotFoundError
        If an input product or auxiliary file listed in the Job Order does not exist.
    InvalidInputError
        If an auxiliary file or product annotation cannot be used.
    UnsupportedProductError
        If a product, or one of its features, is not supported.
    """

    jo = JobOrder(Path(job_order_path))

    # set log level
    denoise_logger.setLevel(jo.log_level)
    denoise_logger.debug("Starting noise processor...")

    # read auxiliary files
    aux_files = AuxiliaryFiles(mission_id = jo.mission_id,
                               pp1_path = jo.aux_pp1_filepath,
                               cal_path = jo.aux_cal_filepath,
                               ins_path = jo.aux_ins_filepath)

    # read file to process from Job Order
    product_path_list = [
        Path(file_name)
        for file_names in jo.input_files_to_process
        for file_name in file_names['List_of_File_Names']['File_Name']
    ]

    out_filepath_list = []

    start_time = PreciseDateTime.now()

    for current_prod in product_path_list:

        # respective L0N product
        associated_l0n = find_l0n(l1a=current_prod, l0n_list=jo.input_l0n_files)

        # denoise product
        noise_vectors_list = generate_noise_profiles(prod=current_prod, l0n_file=associated_l0n, aux_files=aux_files)

        # generate output files
        for elem in noise_vectors_list:

            out_filepath = write_to_xml(noise=elem, output_path=jo.output_path, safe_path=current_prod)
            out_filepath_list.append(out_filepath)

            # if nominal noise files exist, calculate MAE
            nominal_noise = current_prod / "annotation/calibration" / (out_filepath.stem[2:] + ".xml")
            if nominal_noise.exists():

                mae_on_denoise, max_err_on_denoise = calculate_error(nominal_noise_filepath=nominal_noise,
                                                   denoise_filepath=out_filepath)

                if abs(mae_on_denoise) < 1.0:
                    denoise_logger.info(f"Mean absolute error on denoising: {mae_on_denoise:.2e} dB")
                else:
                    denoise_logger.warning(f"High mean absolute error on denoising: {mae_on_denoise:.2e} dB")

                denoise_logger.info(f"Maximum absolute error on denoising: {max_err_on_denoise:.2e} dB")

    denoise_logger.info("Denoising process terminated successfully")

    print_exec_time(elapsed_seconds=PreciseDateTime.now() - start_time)

    return out_filepath_list


def reconstruct_noise(product: str | Path,
                      aux_pp1: str | Path,
                      aux_cal: str | Path,
                      aux_ins: str | Path,
                      l0n: str | Path | None = None) -> list[OutputNoiseVectors_SLC] | list[OutputNoiseVectors_GRD]:
    """
    Reconstruct the noise range vectors of a single Sentinel-1 L1 product.

    No Job Order is needed and no file is written: the reconstructed vectors are returned in memory. The mission
    identifier used to locate the auxiliary files is taken from the first three characters of the product name
    (e.g. ``S1A``).

    Parameters
    ----------
    product : str or pathlib.Path
        Path to the input L1 SAFE product (SLC or GRD).
    aux_pp1, aux_cal, aux_ins : str or pathlib.Path
        Paths to the AUX_PP1, AUX_CAL and AUX_INS SAFE directories.
    l0n : str or pathlib.Path, optional
        Path to the associated L0N SAFE product. If None (default), the noise power annotated in the product is
        used.

    Returns
    -------
    list of OutputNoiseVectors_SLC or list of OutputNoiseVectors_GRD
        One element per output channel, i.e. per polarisation (and per swath for SLC products). The channel is
        identified by the ``ads_header`` attribute; the vectors are in ``noise_range_vectors``, each with its
        ``azimuthTime``, ``line``, ``pixels`` and ``noise_values`` (linear).

    Raises
    ------
    InputNotFoundError
        If the product, one of the auxiliary files or the L0N product does not exist.
    InvalidInputError
        If an auxiliary file or product annotation cannot be used.
    UnsupportedProductError
        If the product, or one of its features, is not supported.
    """

    product = Path(product)
    aux_paths = {"AUX_PP1": Path(aux_pp1), "AUX_CAL": Path(aux_cal), "AUX_INS": Path(aux_ins)}
    l0n = Path(l0n) if l0n is not None else None

    for label, path in [("L1 product", product), *aux_paths.items(), ("L0N product", l0n)]:
        if path is not None and not path.exists():
            raise InputNotFoundError(f"{label} not found: {path}")

    aux_files = AuxiliaryFiles(mission_id = product.name[:3].upper(),
                               pp1_path = aux_paths["AUX_PP1"],
                               cal_path = aux_paths["AUX_CAL"],
                               ins_path = aux_paths["AUX_INS"])

    return generate_noise_profiles(prod=product, aux_files=aux_files, l0n_file=l0n)
