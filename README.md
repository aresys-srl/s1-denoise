# Sentinel-1 Level 1 Noise Processor

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![PyPI - Version](https://img.shields.io/pypi/v/s1-denoise)](https://pypi.org/project/s1-denoise/)
[![Release CI](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml/badge.svg)](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml)

The S1 L1N Processor reconstructs the thermal noise range vectors of Sentinel-1 Level 1 products (IW, EW and Stripmap SLC and GRD, Wave mode SLC) and writes them as XML files in the standard Sentinel-1 noise annotation format. The noise power can optionally be refined with the associated Level 0 Noise (L0N) product. When the SAFE product contains the nominal noise annotations, the Mean Absolute Error (MAE) and maximum absolute error in dB against them are reported.

> [!NOTE]
> The processor has been developed by Aresys within the Copernicus Sentinel-1 SAR Mission Performance Cluster (SAR-MPC), under contract with the European Space Agency (ESA). See [Acknowledgements](#acknowledgements).

📖 **Documentation:** <https://opensource.aresys.it/s1-denoise/>

📄 **Technical Note:** [MPC-0680 – S1 L1 Noise Processor TN, issue 1.4](technical_note/SAR-MPC-0680-ARE-030845_S1_L1_Noise_Processor_TN_v1_4.pdf)

## Installation

Requires Python ≥ 3.11:

```bash
pip install s1-denoise
```

The wheel is also attached to each [GitHub release](https://github.com/aresys-srl/s1-denoise/releases).
This installs the `denoise` command into the active Python environment.

## Usage

### Command line

The `denoise` command processes the L1 products listed in a Job Order XML file (inputs, optional L0N products, auxiliary files, output directory, log level) and writes the noise XML files to the output directory:

```bash
denoise "/path/to/job/order.xml"
```

A template is provided in [`templates/JobOrder.xml`](templates/JobOrder.xml). See the [Job Order documentation](https://opensource.aresys.it/s1-denoise/user-guide/job-order/) for details.

### Python API

The processor can also be used as a Python package, through two functions exposed at package level.

#### `run_job_order` function

This function processes a Job Order exactly as the `denoise` command does: it writes the output noise XML files to the output directory of the Job Order and returns their paths.

```python
import s1_denoise

# writes the XML files and returns their paths
output_files = s1_denoise.run_job_order("/path/to/job/order.xml")
```

#### `reconstruct_noise` function

This function processes a single L1 product from explicit paths, without a Job Order, and returns the reconstructed noise vectors in memory without writing any file:

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

Unlike the `denoise` command, both functions raise Python exceptions instead of exiting with code 128 and create no `denoise.log` file in the working directory.
See the [API reference](https://opensource.aresys.it/s1-denoise/API/reference/) for details.

## Visualisation

To compare the nominal noise annotation with the reconstructed one (`--colorbar` sets the dB range of the difference plot):

```bash
./scripts/compare_noise.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```

## Acknowledgements

The S1 L1N processor has been developed by [Aresys](https://www.aresys.it/) in the framework of the Copernicus Sentinel-1 SAR Mission Performance Cluster (SAR-MPC) service, under contract with the European Space Agency (ESA), as part of Evolution #25 of the MPC activities. The Copernicus programme is funded by the European Union.

The views expressed in this repository and in its documentation can in no way be taken to reflect the official opinion of the European Union or of ESA.

The figures in the documentation contain modified Copernicus Sentinel data processed by Aresys.
