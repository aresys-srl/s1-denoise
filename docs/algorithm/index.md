---
title: "Algorithm"
tags:
    - algorithm
    - thermal noise
---

# Algorithm

## Denoising vector model

Thermal noise affects the quality of SAR images from both a radiometric and an interferometric point of view. The
problem of estimating the noise level from SAR data is discussed in detail in [[RD1]](../API/bibliography.md#reference-documents).

The SAR processor performs range and azimuth compression of the acquired raw data and applies a set of corrections
compensating for the distortions introduced by the instrument (e.g. the antenna patterns) and by the acquisition
geometry (e.g. the spreading losses). These corrections, applied by the S1 IPF during L1 product generation, affect
both signal and noise.

The denoising procedure consists of subtracting the expected noise level to obtain an unbiased estimate of the signal
component. The noise level model computed by the processor is:

$$
n_{\text{vect}}(i,j) = P_n(i) \cdot \frac{G_{\text{pg}}(i) \cdot G_{\text{ds}}(i)}{G_{\text{eap}}(i,j) \cdot G_{\text{rsl}}(j)} \cdot \sqrt{k_{\text{proc}}} \cdot \sqrt{k_{\text{noise}}} \cdot \sqrt{k_{\text{norm}}}
$$

The Sentinel-1 IPF generates the noise annotations included in Level 1 products, containing the estimated noise maps.
The L1N processor reconstructs them starting from the information available in the Level 1 annotations and in the
auxiliary files, as follows:

| Term | Name | Source |
|------|------|--------|
| $P_n(i)$ | Noise power | Noise list annotated in the product annotation XML, or [extracted from the L0N product](noise-power-extraction.md) if provided |
| $G_{\text{pg}}(i)$ | Internal calibration (PG) gain correction | Replica list annotated in the product annotation XML (only records with both `relativePgProductValidFlag` and `absolutePgProductValidFlag` set to `true`), corrected for $PG_{\text{ref}}$ and interpolated at the burst center |
| $G_{\text{ds}}(i)$ | De-scalloping function (azimuth elementary antenna pattern gain) | Noise azimuth vectors, unchanged (TopSAR data only) |
| $G_{\text{eap}}(i,j)$ | Elevation antenna pattern gain | Antenna pattern list annotated in the product annotation XML, re-interpolated over the denoising vector axis |
| $G_{\text{rsl}}(j)$ | Range spreading loss correction | Computed from slant range times (see below) |
| $k_{\text{proc}}$ | Processing gain | AUX_PP1, corrected for the resampling factor for all subswaths after the first one |
| $k_{\text{noise}}$ | Noise calibration factor, aligning the denoising vectors to the actual noise level in the data | AUX_CAL |
| $k_{\text{norm}}$ | Processor normalization factor | [Reconstructed](normalization-factor.md) from the product annotations, AUX_CAL and AUX_INS |

The range spreading loss is computed from the slant range times, elevated to the power of 4:

$$
G_{\text{rsl}}(j) = \left( \frac{R_{\text{ref}}}{t_{\text{range}}(j) \cdot c / 2} \right)^4
$$

where $c = 3 \cdot 10^8$ m/s is the speed of light and $R_{\text{ref}} = 8 \cdot 10^5$ m.

The normalization factor $k_{\text{norm}}$ accounts for the noise scaling introduced by the IPF processing (whitening,
filtering and multi-looking). It is the product of an azimuth and a range contribution:

$$
k_{\text{norm}} = k_{\text{norm,az}} \cdot k_{\text{norm,rg}}, \qquad k_{\text{norm,az}} = \frac{1}{L} \sum_{l=1}^{L} \frac{G_l}{W_l}
$$

where $L$ is the number of looks, $G_l$ is the noise processing gain of look $l$ and $W_l$ its window normalization
factor. See [Noise normalization factor](normalization-factor.md) for the full derivation.

Some of these quantities are directly annotated in the SAFE product, while others are harder to reconstruct: the
critical points are discussed in the following pages.

## Noise power from noise pulses

Sentinel-1A and B acquire dedicated noise pulses at the beginning (pre-amble) and at the end (post-amble) of each
data-take to estimate the noise power used to generate the denoising vectors. Since the start of the mission, the
estimated noise power has shown a bimodal behavior related to the area within the antenna footprint during the
acquisition of the noise pulses: the different Earth emissivity of land and sea produces a noise level variation of
approximately 1 dB between the two scenes. Relying only on pre- and post-amble noise pulses therefore does not allow an
accurate estimation of the noise level across the whole acquisition, leading to poorly calibrated denoising vectors.

To better track the noise power evolution within the data-takes, since June 2018 the **rank echoes** (i.e. the first
echoes of each burst, which are signal-free and can hence be used as noise proxy) are used to generate the noise
vectors of TopSAR L1 products.

Sentinel-1C and D instruments have been slightly evolved to improve the electronic design, reducing the number of
internal calibration pulses from 5 to 3. This enabled the acquisition of noise pulses during the data-takes.
Additionally, noise pulses before and after each imagette in Wave mode have been included to properly track the noise
evolution within long wave acquisitions.

## Rank echoes

For Sentinel-1A/B, the use of rank echoes significantly improved the accuracy of the noise estimation compared to the
approach based only on pre-amble and post-amble noise pulses. With the latter, inaccuracies in the noise estimation can
produce visible discontinuities at the boundaries between adjacent subswaths; with rank echoes, the noise level is
characterized more consistently across the entire swath, leading to a more uniform radiometric appearance of the
processed products.

For Sentinel-1C/D, after the introduction of noise pulses within the data-takes, rank echoes are no longer strictly
necessary, but they are still available, and an assessment of their usage is ongoing. A second reason why rank echoes
could be beneficial for S1C/D is RFI pre-screening: L0N products are also used by the L1 IPF to identify RFI
contamination and activate RFI mitigation on the corresponding L0S product. For S1A/B the pre-screening is performed
with a temporal rate lower than 1 second, while for S1C/D L0N products without rank echoes there can be gaps of 3
seconds, which could result in missed RFI contamination.

## Usage scenarios

The processor generates Level 1 noise annotations for S1A, S1B, S1C and S1D TopSAR data-takes, including acquisitions
prior to June 2018 (before the introduction of rank echoes for S1A and S1B). Used in combination with L0N products, it
enables the regeneration of higher-quality denoising vectors for L1 products generated before 2018, and more generally
provides a systematic framework for the retro-calibration of existing denoising vectors.
