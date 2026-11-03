---
icon: lucide/message-square-plus
title: "Proposal for additional annotations"
tags:
    - algorithm
    - annotations
---

# Proposal for additional annotations

The following annotations are not currently included in the IPF outputs, but would facilitate the reconstruction of
the noise vectors if added to the official SAFE products in future versions.

- **Noise normalization factor** $k_{\text{norm}}$.
  This quantity is product, channel (subswath and polarization) and burst dependent. Since its variation within the
  same channel is negligible, a single annotation per channel would be sufficient. See
  [Noise normalization factor](normalization-factor.md).

- **Quality flags for noise pulses**, indicating which noise sequences should be used and which should be discarded
  for the noise power extraction from L0N products.
  This information is channel dependent. See [Noise power extraction](noise-power-extraction.md). It would also be
  useful to annotate which noise sequences are actually discarded by the IPF, whereas the L1N processor discards the
  measurements outside an interval around the expected noise level read from AUX_INS (see
  [Discard of noise pulses](noise-pulse-discard.md)).

- **Quality flags for PG values**, indicating which PG measurements should be used and which should be discarded.
