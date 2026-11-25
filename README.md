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

The processing steps can also be called directly from Python, without a Job Order. A minimal example that reconstructs the noise vectors of one product and writes them to XML:

```python
from pathlib import Path

from s1_denoise.auxiliary_files import AuxiliaryFiles
from s1_denoise.noise_profiles import generate_noise_profiles
from s1_denoise.output import write_to_xml

aux_files = AuxiliaryFiles(
    mission_id="S1A",
    pp1_path=Path("/path/to/S1A_AUX_PP1_....SAFE"),
    cal_path=Path("/path/to/S1A_AUX_CAL_....SAFE"),
    ins_path=Path("/path/to/S1A_AUX_INS_....SAFE"),
)

product = Path("/path/to/S1A_IW_SLC__1SDV_....SAFE")
noise_vectors = generate_noise_profiles(prod=product, aux_files=aux_files)  # optionally: l0n_file=Path(...)

for channel in noise_vectors:
    out_file = write_to_xml(noise=channel, output_path=Path("/path/to/output"), safe_path=product)
    print(out_file)
```

`generate_noise_profiles` returns one element per output noise file (channel). See the [API reference](https://opensource.aresys.it/s1-denoise/API/reference/) for the full list of modules and functions.

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
