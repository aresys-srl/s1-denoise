# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Configuration and constants module for the S1 L1N processor."""

__version__ = '1.1.0'

PROCESSOR_NAME = 's1_l1n_processor'

# CONSTANTS
R_REF = 8e5  # [m]

NOISE_LOWER_BOUND, NOISE_UPPER_BOUND = 3.0, 1.0  # [dB] bounds for noise pulse outlier detection