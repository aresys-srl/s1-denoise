# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Entry point module for the denoising command-line interface."""

from denoise.cli import denoise_cli

def main():
    denoise_cli()


if __name__ == "__main__":
    main()
