---
icon: lucide/folder-output
title: "Outputs"
tags:
    - usage
    - outputs
---

# Outputs

The processor creates, inside the output directory set in the Job Order, a folder named after each input L1A/S
product, containing the denoise files. The input SAFE products are never modified.

The denoise files are split by subswath and polarization for SLC products, and by polarization only for GRD products.
Each file is named after the corresponding noise annotation of the input product, with the `denoise-` prefix in place
of `noise-`.

```
output/
├── S1A_EW_GRDM_1ADV_20250915T223810_20250915T223914_061004_0799D6_EA11/
│   ├── denoise-s1a-ew-grd-vh-20250915t223810-20250915t223914-061004-0799d6-002.xml
│   └── denoise-s1a-ew-grd-vv-20250915t223810-20250915t223914-061004-0799d6-001.xml
└── S1A_EW_SLC__1ADH_20250906T230229_20250906T230314_060873_079499_988E/
    ├── denoise-s1a-ew1-slc-hh-20250906t230229-20250906t230312-060873-079499-001.xml
    ├── denoise-s1a-ew1-slc-hv-20250906t230229-20250906t230312-060873-079499-006.xml
    ├── ...
    ├── denoise-s1a-ew5-slc-hh-20250906t230231-20250906t230314-060873-079499-005.xml
    └── denoise-s1a-ew5-slc-hv-20250906t230231-20250906t230314-060873-079499-010.xml
```

## File content

The denoise files mirror the noise annotations inside the input SAFE product, whose structure is described in the
Sentinel-1 Product Specification [[RD4]](../API/bibliography.md#reference-documents), section 6.3.3. Noise range vectors are
reconstructed by the processor, while noise azimuth vectors are copied unchanged.

```xml title="denoise file"
<?xml version='1.0' encoding='UTF-8'?>
<noise>
  <adsHeader>
    <missionId>S1B</missionId>
    <productType>SLC</productType>
    <polarisation>VH</polarisation>
    <mode>IW</mode>
    <swath>IW2</swath>
    <startTime>2021-12-15T17:19:30.517919</startTime>
    <stopTime>2021-12-15T17:19:55.698484</stopTime>
    <absoluteOrbitNumber>30039</absoluteOrbitNumber>
    <missionDataTakeId>235062</missionDataTakeId>
    <imageNumber>002</imageNumber>
  </adsHeader>
  <noiseRangeVectorList count="9">
    <noiseRangeVector>
      <azimuthTime>2021-12-15T17:19:30.517920</azimuthTime>
      <line>0</line>
      <pixel count="669">0 40 80 120 160 ... 26692</pixel>
      <noiseRangeLut count="669">3.800881e+02 ... 4.273933e+02</noiseRangeLut>
    </noiseRangeVector>
    ...
  </noiseRangeVectorList>
  <noiseAzimuthVectorList count="1">
    <noiseAzimuthVector>
      <swath>IW2</swath>
      <firstAzimuthLine>0</firstAzimuthLine>
      <firstRangeSample>0</firstRangeSample>
      <lastAzimuthLine>13598</lastAzimuthLine>
      <lastRangeSample>26692</lastRangeSample>
      <line count="1368">0 10 20 30 40 ... 13598</line>
      <noiseAzimuthLut count="1368">1.111833e+00 ... 1.112657e+00</noiseAzimuthLut>
    </noiseAzimuthVector>
  </noiseAzimuthVectorList>
</noise>
```

## Error report

If nominal noise annotations are present in the input SAFE product, the processor parses them together with the
generated denoise files (interpolating them in the GRD case) and logs the Mean Absolute Error (MAE) and the maximum
absolute error in dB, as an estimate of the reconstruction quality.

## Visual comparison

The repository provides a script to visually compare a nominal noise annotation with the reconstructed one:

```bash
./scripts/compare_noise.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```

The `--colorbar` argument sets the symmetric boundary (in dB) of the colorbar of the 2D difference plot.
