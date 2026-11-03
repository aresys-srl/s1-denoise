---
icon: lucide/merge
title: "Subswath merging"
tags:
    - algorithm
    - GRD
---

# Subswath merging

The denoising process relies on a set of parameters that are specific to each subswath, and therefore cannot be
applied uniformly across the entire image at once. When the input is a GRD product, the processor first identifies and
separates the individual subswaths merged together in a single annotation file. It then computes the thermal noise
independently for each subswath, and finally recombines them into a single, continuous cross-subswath output file.

Once all the noise values have been computed, the merging is carried out through the following steps:

1. the noise vectors of each subswath are extended via interpolation, so that they cover the full azimuth extent of the
   scene;
2. for each azimuth time step, the subswaths are concatenated along the range direction, properly handling the regions
   where neighboring subswaths overlap;
3. the merged noise values are consolidated into a single noise vector for each azimuth line.

The outcome is the final cross-subswath denoise annotation, which provides a seamless and consistent noise
characterization across the entire image.
