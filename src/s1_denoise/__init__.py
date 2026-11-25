# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Sentinel-1 Level 1 Noise processor package.

Defines the package version, the processor name used in log records, the reference slant range ``R_REF``
(in meters) and the noise pulse outlier detection bounds ``NOISE_LOWER_BOUND`` / ``NOISE_UPPER_BOUND`` (in dB),
and re-exports the Python entry points `run_job_order` and `reconstruct_noise` (see `s1_denoise.api`) and the
exceptions they raise (see `s1_denoise.exceptions`).
"""

__version__ = '1.2.0'

PROCESSOR_NAME = 's1_l1n_processor'

# CONSTANTS
R_REF = 8e5  # [m]

NOISE_LOWER_BOUND, NOISE_UPPER_BOUND = 3.0, 1.0  # [dB] bounds for noise pulse outlier detection

# Python entry points, imported after the constants above since the submodules import them from the package
from s1_denoise.api import run_job_order, reconstruct_noise  # noqa: E402
from s1_denoise.exceptions import (  # noqa: E402
    DenoiseError, JobOrderError, InputNotFoundError, InvalidInputError, UnsupportedProductError
)

__all__ = [
    "run_job_order", "reconstruct_noise",
    "DenoiseError", "JobOrderError", "InputNotFoundError", "InvalidInputError", "UnsupportedProductError",
]
