# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Exceptions raised by the denoising processor.

All the errors detected by the processor derive from `DenoiseError`, so that callers can catch them with a single
``except s1_denoise.DenoiseError``. Each subclass also derives from the closest built-in exception (e.g.
`InputNotFoundError` is a ``FileNotFoundError``), so that generic handlers keep working. The ``denoise`` command
maps any of them to exit code 128.
"""


class DenoiseError(Exception):
    """Base class of all the errors raised by the denoising processor."""


class JobOrderError(DenoiseError, ValueError):
    """The Job Order is invalid: wrong file type, missing auxiliary files or output path inside an input folder."""


class InputNotFoundError(DenoiseError, FileNotFoundError):
    """An input product, auxiliary file or ``manifest.safe`` does not exist."""


class InvalidInputError(DenoiseError, ValueError):
    """An input product or auxiliary file exists but its content cannot be used by the processor."""


class UnsupportedProductError(DenoiseError, NotImplementedError):
    """The input product, or one of its features, is not supported by the processor."""
