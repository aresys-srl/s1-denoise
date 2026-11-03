# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Logging configuration module for the denoising processor.

Sets up a named logger with colorized console output and plain-text file output, using a structured log format
that includes timestamp, hostname, processor name, version, and process ID. Exposes a module-level
``denoise_logger`` instance for use across the package.
"""

import logging
import colorlog
import socket 

from s1_denoise import __version__, PROCESSOR_NAME

def setup_logger():
    """
    Set up the ``denoise`` logger with colorized console output and file output.

    Handlers are attached only if the logger has none yet, so repeated calls do not duplicate output. Log levels
    are renamed to short tags (``[D]``, ``[I]``, ``[W]``, ``[E]``, ``[P]``), records are written both to the console
    (colorized) and to ``denoise.log`` in the current working directory, and the level is set to ``INFO``.

    Returns
    -------
    logging.Logger
        The configured ``denoise`` logger.
    """
    
    # Check if the logger has already handlers attached
    logger = logging.getLogger("denoise")

    # Only set up logging if no handlers are already attached
    if not logger.hasHandlers():

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

    return logger

denoise_logger = setup_logger()