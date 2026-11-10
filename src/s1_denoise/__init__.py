# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Configuration and constants module for the S1 L1N processor.

Defines the package version, the processor name used in log records, the reference slant range ``R_REF``
(in meters) and the noise pulse outlier detection bounds ``NOISE_LOWER_BOUND`` / ``NOISE_UPPER_BOUND`` (in dB).
"""

__version__ = '1.2.0.dev0'

PROCESSOR_NAME = 's1_l1n_processor'

# CONSTANTS
R_REF = 8e5  # [m]

NOISE_LOWER_BOUND, NOISE_UPPER_BOUND = 3.0, 1.0  # [dB] bounds for noise pulse outlier detection