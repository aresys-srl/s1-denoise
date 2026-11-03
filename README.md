# Sentinel-1 Level 1 Noise Processor

#### Thermal noise vector reconstruction for Sentinel-1 Level 1 products

The S1 L1N Processor reconstructs the thermal noise range vectors for Sentinel-1 IW and EW products (both SLC and GRD) and writes them to output XML files that follow the standard Sentinel-1 annotation format. The reconstructed vectors can then be compared against the nominal noise annotations embedded in the SAFE product to assess noise estimation quality.

For each processed channel the processor:

1. reads acquisition geometry and calibration data from auxiliary files (PP1, CAL, INS);
2. models the thermal noise using range spread loss, elevation antenna pattern, and calibration LUT;
3. optionally refines the noise power correction using an associated Level 0 Noise (L0N) product, applying a Z-score outlier filter to remove spurious noise pulses;
4. for GRD products, handles sub-swath separation, per-subswath noise reconstruction and cross-subswath merging (see [Sub-swath merging](#sub-swath-merging));
5. writes the output noise XML files and, if nominal noise annotations are present in the SAFE product, reports the Mean Absolute Error (MAE) and maximum absolute error in dB.

---

## Installation

The processor can be deployed as a **Docker image** or installed directly as a **Python package**.

### Option 1 — Docker image

The Docker image bundles all dependencies and requires no Python environment on the host. A pre-built image is distributed as part of the official release package.

To load the pre-built image:

```bash
docker load -i s1_l1n_processor_<version>.tar.gz
```

To build the image locally, pass the desired version tag to the build script:

```bash
./build_docker.sh 1.0.1
```

This produces a Docker image named `s1_l1n_processor:1.0.1`.

Run the processor with:

```bash
docker run --rm \
  --volume /path/to/data:/path/to/data \
  s1_l1n_processor:1.0.1 "/path/to/job/order.xml"
```

The `--volume` flag must expose every path referenced inside the Job Order file (input products, auxiliary files, output directory) to the container.

### Option 2 — Python package

**Requirements:** Linux x86_64, Python ≥ 3.8, pip, `libgomp1` (OpenMP runtime).

To install `libgomp1` if not already present:

```bash
sudo apt-get install libgomp1
```

> The `Sentinel1L0ImportQC` binary is only available for Linux x86_64. The Python wheel itself is platform-independent, but the full processor only runs on Linux.

The release archive contains the following layout:

```
denoise-<version>/
├── denoise-<version>-py3-none-linux_x86_64.whl
├── wheels/
│   ├── arepytools-<version>-py3-none-any.whl
│   └── arepyextras_eo_products-<version>-py3-none-any.whl
├── bin/
│   └── Sentinel1L0ImportQC
└── install.sh
```

Run the install script from the archive root:

```bash
chmod +x install.sh
./install.sh
```

The script creates a virtual environment at `~/.local/share/denoise/env`, installs all dependencies there, copies the `Sentinel1L0ImportQC` binary to `~/.local/bin/`, and adds a `denoise` symlink to `~/.local/bin/` so the command is available without activating the virtual environment. The archive can be deleted after installation.

After installation, set the following environment variable so that `denoise` can locate the binary:

```bash
export S1_L0IMPORTQC_PATH="$HOME/.local/bin/Sentinel1L0ImportQC"
```

To make it permanent, add the line above to your `~/.bashrc`.

---

## Usage

The processor is driven by a **Job Order XML file** that specifies input products, auxiliary files, and the output path. Both the Docker image and the Python package expose the same `denoise` CLI command.

```bash
denoise "/path/to/job/order.xml"
```

The processor reads each L1 SAFE product listed in the Job Order, generates the noise profiles, and writes the output XML files to the configured output directory. If nominal noise annotations are found inside the SAFE product, the MAE against them is logged automatically.

For all available options:

```bash
denoise --help
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

A sample Job Order file is provided in the repository root (`JobOrder.template.xml`).

---

## Visualisation

To visually compare the original noise annotation with the reconstructed one:

```bash
./scripts/noise_compare.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```

The `--colorbar` argument sets the symmetric boundary (in dB) for the 2D difference plot colorbar.

---

## Current status

The chart below summarises processor quality over approximately 240 annotation files.

![Current status](./current_status.png)
