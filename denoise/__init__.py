# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Configuration and constants module for the S1 L1N processor."""

__version__ = '1.0.0'

PROCESSOR_NAME = 's1_l1n_processor'

# CONSTANTS
R_REF = 8e5  # [m]

RFI_DETECTION_CONSTANT = 4.0
"""Detection threshold in standard deviations (Z-score): the lower the value, the more RFI samples are removed from the noise power correction."""