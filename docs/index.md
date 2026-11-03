---
icon: lucide/sparkles
title: "Home"
tags:
    - s1-denoise
    - sentinel-1
---

# Sentinel-1 Level 1 Noise Processor

The **Sentinel-1 Level 1 Noise (S1 L1N) processor**, distributed as the `s1-denoise` package, reconstructs the thermal
noise range vectors of Sentinel-1 IW and EW Level 1 products (both SLC and GRD) and writes them to XML files that follow
the standard Sentinel-1 noise annotation format. The reconstructed vectors can then be compared against the nominal noise
annotations embedded in the SAFE product to assess the noise estimation quality.

The processor was developed by Aresys in the framework of the Evolution #25 of the Sentinel-1 SAR Mission Performance
Cluster (MPC) contract. Its purpose is to allow the **retro-calibration of the denoising vectors**, in particular:

- to generate more accurate denoising vectors for S1 products generated before 26/06/2018 (IPF 2.91 deployment,
  activating the processing of L0N products with rank echoes);
- to fix denoising vector bugs from IPF versions before 3.7.1, released in October 2023.

<div class="result" markdown>

[__Install__](install.md){ data-preview } : install the package from PyPI or from the release wheel

[__User Guide__](user-guide/index.md){ data-preview } : prepare the working directory, write the Job Order and run the processor

[__Algorithm__](algorithm/index.md){ data-preview } : the denoising vector model and how the processor reconstructs it

[__Validation__](validation.md){ data-preview } : reconstruction accuracy against the nominal IPF annotations

</div>

## What the processor does

For each processed channel the processor:

1. reads acquisition geometry and calibration data from the product annotations and the auxiliary files
   (AUX_PP1, AUX_CAL, AUX_INS);
2. models the thermal noise using range spreading loss, elevation antenna pattern and calibration LUT;
3. optionally refines the noise power using an associated Level 0 Noise (L0N) product;
4. discards noise pulses whose power deviates from the expected noise level in AUX_INS by more than −3/+1 dB;
5. for GRD products, handles sub-swath separation, per-subswath noise reconstruction and cross-subswath merging;
6. writes the output noise XML files and, if nominal noise annotations are present in the SAFE product, reports the
   Mean Absolute Error (MAE) and maximum absolute error in dB.

Supported inputs are Level 1 SLC and GRD TopSAR (IW, EW) products of all Sentinel-1 units: S1A, S1B, S1C and S1D.

## Getting Started

```bash title="install with pip"
pip install s1-denoise
```

```bash title="run the processor"
denoise "/path/to/job/order.xml"
```

> :lucide-circle-chevron-right: Refer to the [installation](install.md) and [user guide](user-guide/index.md) pages
> for further information.
