# Sentinel-1 Level 1 Noise Processor

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![PyPI - Version](https://img.shields.io/pypi/v/s1-denoise)](https://pypi.org/project/s1-denoise/)
[![Release CI](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml/badge.svg)](https://github.com/aresys-srl/s1-denoise/actions/workflows/release.yml)

#### Thermal noise vector reconstruction for Sentinel-1 Level 1 products

The S1 L1N Processor reconstructs the thermal noise range vectors for Sentinel-1 IW and EW products (both SLC and GRD) and writes them to output XML files that follow the standard Sentinel-1 annotation format. The reconstructed vectors can then be compared against the nominal noise annotations embedded in the SAFE product to assess noise estimation quality.

For each processed channel the processor:

1. reads acquisition geometry and calibration data from auxiliary files (PP1, CAL, INS);
2. models the thermal noise using range spread loss, elevation antenna pattern, and calibration LUT;
3. optionally refines the noise power correction using an associated Level 0 Noise (L0N) product;
4. discards noise pulses whose power deviates from the expected noise level in AUX_INS by more than −3/+1 dB;
5. for GRD products, handles sub-swath separation, per-subswath noise reconstruction and cross-subswath merging;
6. writes the output noise XML files and, if nominal noise annotations are present in the SAFE product, reports the Mean Absolute Error (MAE) and maximum absolute error in dB.

📖 **Documentation:** <https://opensource.aresys.it/s1-denoise/>

---

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

---

## Usage

The processor is driven by a **Job Order XML file** that specifies input products, auxiliary files, and the output path.

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

### Job Order structure

The Job Order is an XML file that configures:

| Field | Description |
|---|---|
| Input L1 SAFE products | One or more Sentinel-1 SAFE folders to process |
| Input L0N products (optional) | Associated Level 0 Noise products for refined power correction |
| Auxiliary files | AUX_PP1, AUX_CAL, AUX_INS product paths |
| Output path | Directory where output XML files are written |
| Log level | Verbosity of the processor log |

A Job Order template is provided in [`templates/JobOrder.xml`](templates/JobOrder.xml). See the [Job Order documentation](https://opensource.aresys.it/s1-denoise/user-guide/job-order/) for the supported file types.

---

## Visualisation

To visually compare the original noise annotation with the reconstructed one:

```bash
./scripts/compare_noise.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```

The `--colorbar` argument sets the symmetric boundary (in dB) for the 2D difference plot colorbar.

---

## Documentation

The documentation is built with [Zensical](https://zensical.org/). To build it locally:

```bash
pip install zensical mkdocstrings-python
python tools/build_doc.py
```

The site is generated in the `site/` folder. Use `zensical serve` for a live preview while editing the pages in `docs/`.
