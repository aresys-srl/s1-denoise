---
icon: lucide/arrow-big-down-dash
title: "Install"
tags:
    - python
    - pip
    - install
---

# Installation

!!! note "Requirements"

    The S1 L1N processor requires a Python version **equal or higher than 3.11**.

The processor is delivered as a platform-independent Python package, exposing the `denoise` command line interface.
All dependencies are automatically resolved from PyPI by `pip` at install time.

## From PyPI

```bash title="install with pip"
pip install s1-denoise
```

A specific version can be installed by pinning it:

```bash title="install a specific version"
pip install s1-denoise==M.m.p
```

where `M`, `m` and `p` indicate respectively the major, minor and patch version.

## From the release wheel

Each [GitHub release](https://github.com/aresys-srl/s1-denoise/releases) provides the package as a single wheel,
which can be installed with:

```bash title="install from wheel"
pip install s1_denoise-M.m.p-py3-none-any.whl
```

## Check the installation

Both options install the `denoise` command into the active Python environment:

```bash
denoise --version
```

!!! tip "Virtual Environments"

    We recommend using a dedicated virtual environment to install the package.
    This will ensure that the software is installed in a separate environment and avoids conflicts with other packages
    or dependencies.
