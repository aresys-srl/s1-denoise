# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
CLI module for the denoising processor, built with Click.

Defines the denoise_cli command, which reads a Job Order file to locate input L1 SAFE
products and auxiliary files, generates noise profiles, writes output XML files, and
optionally computes the mean absolute error against nominal noise annotations.
"""

import click
from pathlib import Path
import sys

from perseo_core.timing import PreciseDateTime

from denoise.logger import denoise_logger
from denoise.noise_profiles import generate_noise_profiles, print_exec_time
from denoise.__init__ import __version__
from denoise.jo_parser import JobOrder, find_l0n
from denoise.auxiliary_files import AuxiliaryFiles
from denoise.output import write_to_xml
from denoise.error_calculation import calculate_error


@click.command()
@click.version_option(__version__, prog_name="denoise")
@click.argument("job_order_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path)
)
def denoise_cli(job_order_path):
    """Generate noise profiles starting from L1 SAFE files."""

    return_code = 0
    try:
        jo = JobOrder(job_order_path)

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

    except SystemExit as e:
        return_code = e.code
        raise
    finally:
        denoise_logger.info(f"Exit code: {return_code}")
