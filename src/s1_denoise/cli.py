# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
CLI module for the denoising processor, built with Click.

Defines the ``denoise_cli`` command, which reads a Job Order file to locate input L1 SAFE products and auxiliary
files, generates noise profiles, writes output XML files, and, when the nominal noise annotations are available
in the input product, computes the mean and maximum absolute error against them.
"""

import click
from pathlib import Path
import sys

from s1_denoise import __version__
from s1_denoise.api import run_job_order
from s1_denoise.exceptions import DenoiseError
from s1_denoise.logger import denoise_logger


@click.command()
@click.version_option(__version__, prog_name="denoise")
@click.argument("job_order_path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path)
)
def denoise_cli(job_order_path):
    """
    Generate noise profiles starting from L1 SAFE files.
    \f
    Runs `s1_denoise.api.run_job_order` on the Job Order, mapping any error to exit code 128.

    Parameters
    ----------
    job_order_path : pathlib.Path
        Path to the Job Order XML file.

    Raises
    ------
    SystemExit
        With code 128 on any error. Errors detected by the processor (`DenoiseError`) are logged with their message
        only, unexpected errors with their traceback. The exit code is always logged before leaving.
    """

    return_code = 0
    try:
        run_job_order(job_order_path)

    except DenoiseError as e:
        denoise_logger.error(f"{e}. Aborting...")
        return_code = 128
        sys.exit(128)
    except Exception:
        # any unhandled error stops the processor with exit code 128
        denoise_logger.exception("Unexpected error. Aborting...")
        return_code = 128
        sys.exit(128)
    finally:
        denoise_logger.info(f"Exit code: {return_code}")
