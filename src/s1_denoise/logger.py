# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Logging configuration module for the denoising processor.

Exposes the module-level ``denoise_logger`` (the ``denoise`` logger) used across the package. When the package is
imported as a library the logger only has a ``logging.NullHandler``: no file is created, nothing is printed and the
records propagate to the logging configuration of the host application.

The ``denoise`` command calls `setup_logger`, which adds colorized console output and plain-text file output, using
a structured log format that includes timestamp, hostname, processor name, version, and process ID.
"""

import logging
import colorlog
import socket 

from s1_denoise import __version__, PROCESSOR_NAME

def setup_logger():
    """
    Set up the ``denoise`` logger with colorized console output and file output.

    Called by the ``denoise`` command at startup; it is not called on import, so that using the package as a library
    has no side effects on the logging configuration and the file system. Handlers are attached only if the
    ``denoise`` logger has no handlers yet other than the ``NullHandler`` added on import, so repeated calls do not
    duplicate output. Propagation to ancestor loggers is disabled, so that
    records are not printed twice when the root logger is configured by the host application. Log levels
    are renamed to short tags (``[D]``, ``[I]``, ``[W]``, ``[E]``, ``[P]``), records are written both to the console
    (colorized) and to ``denoise.log`` in the current working directory, and the level is set to ``INFO``.

    Returns
    -------
    logging.Logger
        The configured ``denoise`` logger.
    """
    
    # Check if the logger has already handlers attached
    logger = logging.getLogger("denoise")

    # Only set up logging if no handlers other than the NullHandler added on import are attached to this logger
    # (hasHandlers() would also look at ancestor loggers, e.g. a root logger configured by the host application)
    if all(isinstance(handler, logging.NullHandler) for handler in logger.handlers):

        logging.addLevelName(logging.NOTSET, "")
        logging.addLevelName(logging.DEBUG, "[D]")
        logging.addLevelName(logging.INFO, "[I]")
        logging.addLevelName(logging.WARNING, "[W]")
        logging.addLevelName(logging.ERROR, "[E]")
        logging.addLevelName(logging.CRITICAL, "[P]")
        
        log_colors = {
            '': 'green',
            '[D]': 'cyan',
            # '[I]': 'white',
            '[W]': 'yellow',
            '[E]': 'red',
            '[P]': 'bold_red',
        }

        HOSTNAME=socket.gethostname()

        LOG_STRING = f'%(asctime)s.%(msecs)03d000 {HOSTNAME} {PROCESSOR_NAME} {__version__} [%(process)d] %(levelname)s %(message)s'
        COLORED_LOG_STRING = "%(log_color)s" + LOG_STRING
        DATE_FMT = "%Y-%m-%dT%H:%M:%S"

        # Create a colored formatter
        formatter = colorlog.ColoredFormatter(COLORED_LOG_STRING, log_colors=log_colors, datefmt=DATE_FMT)

        # Create console handler for colored logs
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        # Create file handler to log to "denoise.log"
        file_handler = logging.FileHandler("denoise.log")
        file_handler.setFormatter(logging.Formatter(LOG_STRING, datefmt=DATE_FMT))

        # Create logger and set level
        logger = logging.getLogger("denoise")

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        logger.setLevel(logging.INFO)
        logger.propagate = False

    return logger

denoise_logger = logging.getLogger("denoise")
denoise_logger.addHandler(logging.NullHandler())