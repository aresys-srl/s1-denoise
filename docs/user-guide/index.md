---
title: "User Guide"
tags:
    - usage
    - cli
---

# User Guide

The S1 L1N processor generates new Level 1 denoising annotations starting from L1A/S SAFE products. Since the
processor only uses the product metadata, L1S products can be passed as inputs as well: their binary files are
ignored.

## Inputs

The processor expects the following inputs:

1. **Job Order**: an XML file providing the location of all input and output files and folders. See
   [Job Order](job-order.md) for details.
2. **L1A/S SAFE product(s)**: the target products for which denoising files are generated. Level 1 (A/S) SLC and GRD
   TopSAR acquisitions from S1A, S1B, S1C and S1D are supported.
3. **Auxiliary data files**: some parameters of the original IPF processing are mission-dependent and stored in the
   AUX_PP1, AUX_CAL and AUX_INS products, which must be provided by the user. AUX_INS also provides the expected noise
   power used to [discard noise pulse outliers](../algorithm/noise-pulse-discard.md) and the azimuth steering rate
   used for the [noise normalization factor](../algorithm/normalization-factor.md).
4. **L0N SAFE product(s), optional**: if a matching Level 0 Noise product is provided, the processor
   [re-computes the noise power](../algorithm/noise-power-extraction.md) instead of reading it from the Level 1
   annotations of the target product.

## Working directory

An example of a configured working directory is reported below.

```
working_directory/
├── aux-files/
│   ├── S1D_AUX_CAL_V20260106T000000_G20260120T110643.SAFE
│   ├── S1D_AUX_INS_V20251110T000000_G20251217T143507.SAFE
│   └── S1D_AUX_PP1_V20251110T000000_G20251105T150133.SAFE
├── input/
│   ├── S1D_EW_GRDM_1ADH_20260202T084121_20260202T084145_001305_00154E_08FF.SAFE
│   ├── S1D_EW_RAW__0NDH_20260202T084121_20260202T084144_001305_00154E_F66D.SAFE
│   └── S1D_IW_SLC__1ADV_20260205T171338_20260205T171405_001354_00174E_B095.SAFE
├── JobOrder.s1d-ew-grd-l0n.xml
├── JobOrder.s1d-iw-slc.xml
└── output/
    ├── S1D_EW_GRDM_1ADH_20260202T084121_20260202T084145_001305_00154E_08FF
    │   ├── denoise-s1d-ew-grd-hh-20260202t084121-20260202t084145-001305-00154e-001.xml
    │   └── denoise-s1d-ew-grd-hv-20260202t084121-20260202t084145-001305-00154e-002.xml
    └── S1D_IW_SLC__1ADV_20260205T171338_20260205T171405_001354_00174E_B095
        ├── denoise-s1d-iw1-slc-vh-20260205t171339-20260205t171404-001354-00174e-001.xml
        ├── denoise-s1d-iw1-slc-vv-20260205t171339-20260205t171404-001354-00174e-004.xml
        ├── denoise-s1d-iw2-slc-vh-20260205t171340-20260205t171405-001354-00174e-002.xml
        ├── denoise-s1d-iw2-slc-vv-20260205t171340-20260205t171405-001354-00174e-005.xml
        ├── denoise-s1d-iw3-slc-vh-20260205t171338-20260205t171403-001354-00174e-003.xml
        └── denoise-s1d-iw3-slc-vv-20260205t171338-20260205t171403-001354-00174e-006.xml
```

Input SAFE products and auxiliary files do not need to be collected in a single folder, as long as the Job Order
correctly points to the target data paths.

## Running the processor

After preparing the working directory, the processor can be run with:

```bash
denoise "/path/to/job/order.xml"
```

The installed version can be checked with `denoise --version`, and all available options can be listed with
`denoise --help`.

## Exit codes

| Value | Mnemonic  | Description                                                                                                                       |
|:-----:|:---------:|-----------------------------------------------------------------------------------------------------------------------------------|
| 0     | OK        | The processor execution was successfully completed.                                                                               |
| 128   | FAILURE   | Error exit code, returned also for invalid command line arguments and unexpected errors. The processing is considered failed.     |
