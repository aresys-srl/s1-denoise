# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""Entry point module for the denoising command-line interface."""

import sys
import click

from s1_denoise.cli import denoise_cli

def main():
    """
    Run the denoising CLI, mapping Click errors to exit code 128.

    The command is invoked with ``standalone_mode=False`` so that Click usage errors and user aborts are
    reported here and terminate the processor with exit code 128 instead of Click's default exit codes.

    Raises
    ------
    SystemExit
        With code 128 on a Click usage error or abort; exit codes raised by the CLI itself are propagated.
    """
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
