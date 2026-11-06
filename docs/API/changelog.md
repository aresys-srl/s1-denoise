---
icon: lucide/history
title: "Changelog"
tags:
    - changelog
    - release notes
---

# Changelog

## v1.1.0

**Breaking changes**

- Package renamed to `s1-denoise` and migrated to the `src` layout (wheel `s1_denoise-<version>-py3-none-any.whl`); the `denoise` command is unchanged.
- Python 3.11 or higher is now required.

**Additional features**

- New algorithm for [noise pulse discarding](../algorithm/noise-pulse-discard.md): noise measurements are compared with the expected noise level read from AUX_INS (−3/+1 dB).
- For GRD products, the azimuth FM rate record closest in time to each block is used for the [noise normalization factor](../algorithm/normalization-factor.md).
- Package published on PyPI.
- Documentation site with user guide, algorithm description, validation results and API reference.

**Bug fixes**

- L1 and L0N products are now paired on exact mission, mode, orbit and datatake.
- Warning when an AUX file is not referenced in the product manifest, instead of a crash.
- Fixed the MAE computation when nominal and reconstructed noise vectors start at different times.
- Explicit errors and warnings instead of unclear failures in several edge cases (missing DN values, unknown subswath, missing EW3 rank echoes, invalid window coefficient).
- Log output now works also when the root logger is already configured.

## v1.0.2

- Removed the processor's dependency on the statically linked C++ binary and on private wheels.

## v1.0.1

- Introduced I/Q bias correction for [L0N noise power extraction](../algorithm/noise-power-extraction.md).
- Improved the [sub-swath merging](../algorithm/subswath-merging.md) strategy for GRD products.

## v1.0.0

First official release.
