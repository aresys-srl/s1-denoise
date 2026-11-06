---
icon: lucide/scale
title: "Noise normalization factor"
tags:
    - algorithm
    - normalization
---

# Noise normalization factor

One of the most challenging tasks was to reverse-engineer how the IPF computes its processing normalization factor,
which accounts for the whitening, the filtering and the multi-looking processes. This quantity is not only product
dependent, but **burst dependent**. The only way to retrieve its correct value was to re-implement all the IPF
processing steps involved in its estimation, including Hamming windowing, angle computation, antenna pattern
interpolation and correction, and window normalization.

The noise normalization factor is computed as the product of an azimuth and a range contribution,
$k_{\text{norm}} = k_{\text{norm,az}} \cdot k_{\text{norm,rg}}$.

## Azimuth contribution

The azimuth contribution is the average over the $L$ looks of the noise normalization factor of each look. For the
$l$-th look:

$$
k_{\text{norm},l} = \frac{G_l}{W_l}
$$

where $G_l$ is the noise processing gain and $W_l$ is the window normalization factor of the look.

The looks are extracted on an azimuth FFT grid of $N_{\text{FFT}}$ samples, i.e. the smallest valid FFT length not
lower than the number of lines per burst, with frequency resolution $\Delta f = f_{\text{az}} / N_{\text{FFT}}$, where
$f_{\text{az}}$ is the azimuth sampling frequency. The quantities involved are derived from the `azimuthProcessing`
section of the product annotation:

- $N_{\text{bw}} = \operatorname{round}(B_{\text{az}} / \Delta f)$ is the processing bandwidth in samples, with
  $B_{\text{az}}$ the annotated `processingBandwidth`;
- $N_w = \operatorname{round}(B_{\text{look}} / \Delta f)$ is the length of each look, with $B_{\text{look}}$ the
  annotated `lookBandwidth`;
- $w(\cdot)$ is the Hamming window of length $N_w$, whose coefficient is the annotated `windowCoefficient`;
- $N_{\text{ov}}$ is the look overlap in samples, computed as in the IPF:

    $$
    N_{\text{ov}} = \left\lceil \frac{L \cdot N_w - N_{\text{bw}}}{L - 1} \right\rceil
    $$

    for $L > 1$, and $N_{\text{ov}} = 0$ for single-look processing;

- $k_l$ is the start index of look $l$ on the FFT grid:

    $$
    k_l = \left[ \left\lfloor \frac{1 - N_{\text{ext}}}{2} \right\rfloor + (l - 1)(N_w - N_{\text{ov}}) \right] \bmod N_{\text{FFT}},
    \qquad N_{\text{ext}} = N_w + (L - 1)(N_w - N_{\text{ov}})
    $$

- $S(k)$ is the azimuth antenna pattern (AAP) read from AUX_CAL, in linear amplitude, interpolated in dB on the FFT
  grid. The angular step corresponding to one frequency bin is $\frac{\lambda}{2 v} \, \Delta f \, (1 - k_{\text{DC}} / k_{\text{FM}})$,
  where $v$ is the satellite velocity and $1 - k_{\text{DC}} / k_{\text{FM}}$ is the TopSAR shrink factor. The Doppler
  centroid rate $k_{\text{DC}}$ is derived from the azimuth steering rate of the subswath read from AUX_INS
  (`radarParams/azimuthSteeringRate`), and the azimuth FM rate $k_{\text{FM}}$ from the `azimuthFmRateList` of the
  product annotation, both evaluated at mid-range. For SLC products the FM rate record of the burst is used; for GRD
  products, whose FM rate records are not aligned with the blocks of each subswath, the record closest in time to the
  block is used.

The expressions of $G_l$ and $W_l$ depend on whether the AAP correction has been applied by the IPF, as reported by the
`antennaAzimuthPatternApplied` flag of the product annotation.

### AAP correction applied

The spectrum is divided by the AAP, which also boosts the noise at the spectrum edges:

$$
G_l = \frac{1}{N_{\text{bw}}} \sum_{k=0}^{N_w - 1} \left[ \frac{w(k)}{S(k_l + k)} \right]^2,
\qquad
W_l = \frac{\sum_{k=0}^{N_w - 1} w(k)^2}{\sum_{k \in \mathcal{B}} S(k)^2}
$$

### AAP correction not applied

The AAP is not divided out and remains in the spectrum of each look:

$$
G_l = \frac{1}{N_{\text{bw}}} \sum_{k=0}^{N_w - 1} w(k)^2,
\qquad
W_l = \frac{\sum_{k=0}^{N_w - 1} \left[ S(k_l + k) \cdot w(k) \right]^2}{\sum_{k \in \mathcal{B}} S(k)^2}
$$

where $\mathcal{B}$ denotes the $N_{\text{bw}}$ samples of the processing bandwidth, centered on the zero frequency.
All indices are taken modulo $N_{\text{FFT}}$.

## Range contribution

In range the spectrum is flat, so the noise normalization only depends on the rounding of the processing bandwidth to
an integer number of samples:

$$
k_{\text{norm,rg}} = \frac{N_{\text{bw,rg}}}{\operatorname{round}(N_{\text{bw,rg}})},
\qquad N_{\text{bw,rg}} = \frac{B_{\text{rg}}}{\Delta f_{\text{rg}}},
\qquad \Delta f_{\text{rg}} = \frac{f_s}{N_{\text{FFT,rg}}}
$$

where $B_{\text{rg}}$ is the annotated range processing bandwidth, $f_s$ is the range sampling frequency after
decimation and $N_{\text{FFT,rg}}$ is the smallest valid FFT length not lower than the number of samples per burst.
This contribution is close to 1, but not negligible when targeting small reconstruction errors.

## Remarks

With the support of multi-look processing and of products without AAP correction, no empirical correction of the
noise normalization factor is needed anymore (previous releases applied one to the EW1 subswath of EW GRDM products).

Within the same subswath, the value of the normalization factor varies by no more than 0.0001% between consecutive
bursts (due to small differences in consecutive FM rate values, incidence angle variance, floating-point imprecision,
and so on), while differences across subswaths can reach up to 26%. Reporting this value directly in the Level 1
annotations would eliminate the need for these re-computations altogether (see
[Proposal for additional annotations](additional-annotations.md)).
