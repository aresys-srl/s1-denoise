---
icon: lucide/waves
title: "Stripmap and Wave mode"
tags:
    - algorithm
    - stripmap
    - wave mode
---

# Stripmap and Wave mode

Stripmap (SM, beams S1 to S6) and Wave mode (WV, swaths WV1 and WV2) products are processed with the same
[noise model](index.md#denoising-vector-model) as TopSAR products, with the following differences, all verified
against the IPF noise annotations.

- **No bursts.** SM images and WV imagettes are not split into bursts: one noise vector is computed for each record of
  the antenna pattern list falling inside the image (or imagette), at the azimuth time of the record, i.e. about one
  vector per second. The line of each vector is $\operatorname{round}((t - t_0) / \Delta t)$, where $t$ is the time of
  the record, $t_0$ the time of the first line and $\Delta t$ the azimuth time interval, as in the IPF annotation.
- **Shrink factor.** SM and WV acquisitions do not steer the antenna in azimuth (the AUX_INS azimuth steering rate is
  zero): the Doppler centroid rate is zero and the shrink factor $1 - k_{\text{DC}} / k_{\text{FM}}$ used for the
  [noise normalization factor](normalization-factor.md) is exactly 1.
- **Processing gain.** The IPF does not divide the processing gain of SM and WV noise vectors by the range
  oversampling factor $f_s / B_{\text{rg}}$, as done for TopSAR products. Applying it produces a constant bias equal to
  $-10 \log_{10}(f_s / B_{\text{rg}})$: from 0.46 to 0.58 dB for the SM beams, 1.28 dB for WV1 and 0.54 dB for WV2.
  This was verified on SM products of all beams processed with different AUX files, and on WV products of both swaths.
- **GRD products.** SM GRD products contain a single swath and no swath merging information: the swath is processed as
  a single subswath covering the whole image. SM products with full resolution GRD (GRDF) are not supported. WV products
  are only available as SLC.
- **Wave mode imagettes.** Each WV imagette is a separate channel, with its own annotation and noise files, identified
  by swath, polarization and image number: one denoise file is generated for each imagette.
- **PG correction.** SM GRD slices in the middle of a data-take carry no PG replica record (SM SLC slices annotate the
  replicas of the whole data-take): the PG correction cannot be computed for them, and the processing stops with an
  error.

## L0N products

SM and WV L0N products contain only noise pulses (no rank echoes): pre-amble and post-amble of the data-take for SM,
and noise sequences before and after each imagette for WV. They are decoded as described in
[Noise power extraction](noise-power-extraction.md), with two specific aspects:

- the swath of the SM noise packets is derived from the ECC number (e.g. `STRIPMAP_5_N_WO_INTERL_CAL` for beam S5),
  since the swath number field of the packets does not match the beam number; WV noise packets (ECC number
  `WAVE_MODE`) have swath number 30 for WV1 and 31 for WV2;
- the IPF annotates SM and WV noise measurements at the packet time shifted by a constant offset (about 0.3–0.4 s,
  depending on the data-take, the swath and the imagette), which cannot be derived from the L1 annotations: the L0N
  measurement times are therefore shifted by the difference between the first annotated and the first L0N measurement
  of the channel.
