# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Entry point module for the denoising command-line interface."""

import sys
import click

from denoise.cli import denoise_cli

def main():
    # standalone_mode=False lets click errors (e.g. invalid arguments) propagate,
    # so that the processor exits with code 128 also in these cases
    try:
        denoise_cli(standalone_mode=False)
    except click.ClickException as e:
        e.show()
        sys.exit(128)
    except click.Abort:
        click.echo("Aborted!", err=True)
        sys.exit(128)


if __name__ == "__main__":
    main()
