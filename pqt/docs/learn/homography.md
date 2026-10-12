# Homography: from image pixels to floor metres (V3)

A fixed camera looks at a flat floor. Every floor point (X, Y) lands on one pixel (u, v), and the mapping
between the two planes is a **homography**: a 3×3 matrix H, defined up to scale, with

    s · [X, Y, 1]ᵀ = H · [u, v, 1]ᵀ        (s is whatever makes the last coordinate 1)

so X = (h₁₁u + h₁₂v + h₁₃) / (h₃₁u + h₃₂v + h₃₃), and the same for Y with the second row. The bottom
row is what makes far things small: it is perspective.

## Why four points

H has 9 entries but only 8 degrees of freedom (scale does not matter). Each measured pair
(u, v) ↔ (X, Y) gives two equations, so four pairs, no three on a line, determine H. More pairs give a
least-squares fit and let us measure error on points the fit did not use.

## The Direct Linear Transform (what `geometry.dlt` does)

Cross-multiplying the two ratios removes the unknown scale and leaves equations that are *linear* in
the entries of H. Stack them for all points into a matrix A (2 rows per point, 9 columns); the best H is
the unit vector h that makes ‖A·h‖ smallest: the right singular vector of A with the smallest singular
value. Before that, both point sets are shifted to their centroid and scaled to an average distance of
√2 ("Hartley normalisation"); without it, pixel values in the hundreds and metres in single digits make
A badly conditioned and the answer noticeably worse. The fit is mapped back through the two
normalising transforms at the end.

## Robustness and the 15 cm check (what `geometry.fit` does)

- One badly measured point can drag the whole fit. With five or more points, every 4-point subset is
  tried; the subset whose H agrees (within 10 cm) with the most points wins, and H is refitted on
  those inliers. The others are reported so you can re-measure them.
- **Held-out error** is leave-one-out: fit on all inliers but one, measure the distance between where H
  puts that point and where the tape measure says it is, repeat for every point. The plan's bar is a
  maximum below **15 cm**. `pqt-vision calibrate` refuses to write a config that misses it.

## Only feet

The homography is exact only for points on the floor plane. A person's foot point (bottom-centre of
their box) is on the floor; their head is not. That is why zones and speeds are computed from foot
points only.

## Calibrating a real camera (P3 onwards)

1. Put 6–10 strips of tape on the floor in the camera's view, spread over the area that matters
   (the queue, both ends of the aisle). Avoid putting them all on one line.
2. Measure each strip's position in metres from one corner (X along one wall, Y along the other).
3. Grab one frame and read each strip's pixel position in any image viewer.
4. Write `points.yaml` (see `vision/lab/config/points.example.yaml`) and run:

       .venv/bin/pqt-vision calibrate --points points.yaml --out config/cameras/<camera>.yaml

5. Draw the zones in floor metres in that file. In Tier 3 the onboarding wizard does steps 3–5 with clicks.
