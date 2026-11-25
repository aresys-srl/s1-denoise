# Sentinel-1 Level 1 Noise Processor

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![PyPI - Version](https://img.shields.io/pypi/v/s1-denoise)](https://pypi.org/project/s1-denoise/)
[![Release CI](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml/badge.svg)](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml)

The S1 L1N Processor reconstructs the thermal noise range vectors for Sentinel-1 IW, EW and Stripmap products (both SLC and GRD) and Wave mode products (SLC) and writes them to output XML files that follow the standard Sentinel-1 annotation format. The reconstructed vectors can then be compared against the nominal noise annotations embedded in the SAFE product to assess noise estimation quality.

> [!NOTE]
> The processor has been developed by Aresys within the Copernicus Sentinel-1 SAR Mission Performance Cluster (SAR-MPC), under contract with the European Space Agency (ESA). See [Acknowledgements](#acknowledgements).

For each processed channel the processor:

1. reads acquisition geometry and calibration data from auxiliary files (PP1, CAL, INS);
2. models the thermal noise using range spread loss, elevation antenna pattern, and calibration LUT;
3. optionally refines the noise power correction using an associated Level 0 Noise (L0N) product;
4. discards noise pulses whose power deviates from the expected noise level in AUX_INS by more than −3/+1 dB;
5. for GRD products, handles sub-swath separation, per-subswath noise reconstruction and cross-subswath merging;
6. writes the output noise XML files and, if nominal noise annotations are present in the SAFE product, reports the Mean Absolute Error (MAE) and maximum absolute error in dB.

📖 **Documentation:** <https://opensource.aresys.it/s1-denoise/>

📄 **Technical Note:** [MPC-0680 – S1 L1 Noise Processor TN, issue 1.4](technical_note/SAR-MPC-0680-ARE-030845_S1_L1_Noise_Processor_TN_v1_4.pdf)

## Installation

**Requirements:** Python ≥ 3.11, pip.

Install the package from PyPI into a virtual environment of your choice:

```bash
pip install s1-denoise
```

Alternatively, install the wheel attached to a [GitHub release](https://github.com/aresys-srl/s1-denoise/releases):

```bash
pip install s1_denoise-<version>-py3-none-any.whl
```

Both options install the `denoise` command into the active Python environment.

## Usage

The processor can be run from the command line, driven by a Job Order, or imported as a Python package.

### Command line

The `denoise` command is driven by a **Job Order XML file** that specifies input products, auxiliary files, and the output path.

```bash
denoise "/path/to/job/order.xml"
```

The processor reads each L1 SAFE product listed in the Job Order, generates the noise profiles, and writes the output XML files to the configured output directory. If nominal noise annotations are found inside the SAFE product, the MAE against them is logged automatically.

For all available options:

```bash
denoise --help
```

To print the installed version:

```bash
denoise --version
```

#### Job Order structure

The Job Order is an XML file that configures:

| Field | Description |
|---|---|
| Input L1 SAFE products | One or more Sentinel-1 SAFE folders to process |
| Input L0N products (optional) | Associated Level 0 Noise products for refined power correction |
| Auxiliary files | AUX_PP1, AUX_CAL, AUX_INS product paths |
| Output path | Directory where output XML files are written |
| Log level | Verbosity of the processor log |

A Job Order template is provided in [`templates/JobOrder.xml`](templates/JobOrder.xml). See the [Job Order documentation](https://opensource.aresys.it/s1-denoise/user-guide/job-order/) for the supported file types.

### Python API

The processor can also be used as a Python package, through two functions exposed at package level.

`run_job_order` processes a Job Order exactly as the `denoise` command does: it writes the output noise XML files to the output directory of the Job Order and returns their paths.

```python
import s1_denoise

output_files = s1_denoise.run_job_order("/path/to/job/order.xml")
```

`reconstruct_noise` processes a single L1 product from explicit paths, without a Job Order, and returns the reconstructed noise vectors in memory without writing any file:

```python
import s1_denoise

channels = s1_denoise.reconstruct_noise(
    "/path/to/S1A_IW_GRDH_1SDV_....SAFE",
    aux_pp1="/path/to/S1A_AUX_PP1_....SAFE",
    aux_cal="/path/to/S1A_AUX_CAL_....SAFE",
    aux_ins="/path/to/S1A_AUX_INS_....SAFE",
    l0n="/path/to/S1A_IW_RAW__0NDV_....SAFE",  # optional
)

for channel in channels:  # one per polarisation (and per swath for SLC products)
    print(channel.ads_header["polarisation"], channel.ads_header["swath"])
    for vector in channel.noise_range_vectors:
        vector.azimuthTime, vector.line, vector.pixels, vector.noise_values  # noise in linear scale
```

Unlike the `denoise` command, both functions raise Python exceptions instead of exiting with code 128. Errors detected by the processor derive from `s1_denoise.DenoiseError`: `JobOrderError` (invalid Job Order), `InputNotFoundError` (missing input, also a `FileNotFoundError`), `InvalidInputError` (unusable auxiliary file or annotation) and `UnsupportedProductError` (product or feature not supported).

```python
try:
    s1_denoise.run_job_order("/path/to/job/order.xml")
except s1_denoise.DenoiseError as e:
    print(f"Processing failed: {e}")
```

Importing the package has no side effects: unlike the `denoise` command, it neither prints log messages nor creates `denoise.log`. The processor logs through the standard `logging` module on the `"denoise"` logger, so its messages follow the logging configuration of your application, e.g.:

```python
import logging

logging.basicConfig(level=logging.INFO)
```

`run_job_order` sets the level of the `"denoise"` logger to the one of the Job Order. See the [API reference](https://opensource.aresys.it/s1-denoise/API/reference/) for details.

## Visualisation

To visually compare the original noise annotation with the reconstructed one:

```bash
./scripts/compare_noise.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```

The `--colorbar` argument sets the symmetric boundary (in dB) for the 2D difference plot colorbar.

## Acknowledgements

The S1 L1N processor has been developed by [Aresys](https://www.aresys.it/) in the framework of the Copernicus Sentinel-1 SAR Mission Performance Cluster (SAR-MPC) service, under contract with the European Space Agency (ESA), as part of Evolution #25 of the MPC activities. The Copernicus programme is funded by the European Union.

The views expressed in this repository and in its documentation can in no way be taken to reflect the official opinion of the European Union or of ESA.

The figures in the documentation contain modified Copernicus Sentinel data processed by Aresys.
