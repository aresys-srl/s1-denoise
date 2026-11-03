# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Entry point module for the denoising command-line interface."""

from denoise.cli import denoise_cli
from denoise.logger import denoise_logger

def main():
    
    return_code = 0
    try:
        denoise_cli()
        pass
    except SystemExit as e:
        return_code = e.code
        raise
    finally:
        denoise_logger.info(f"Exit code: {return_code}")
    

if __name__ == "__main__":
    main()
