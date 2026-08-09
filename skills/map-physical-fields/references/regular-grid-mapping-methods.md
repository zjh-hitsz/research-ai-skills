# Regular-grid conservative mapping methods

## Frozen v0.1 method

The only implemented method is:

```text
area-overlap-piecewise-constant
```

Interpret each source value as the average scalar value over its rectangular cell. For each target cell, compute the overlap area with every contributing source cell:

```text
target_value = sum(source_value * overlap_area) / target_cell_area
coverage     = sum(overlap_area) / target_cell_area
```

This transfers the cell integral exactly apart from floating-point arithmetic when source and target grids cover the same physical domain. It is not bilinear interpolation and does not claim pointwise accuracy.

## Required grid conditions

- two-dimensional Cartesian axes;
- regular, axis-aligned rectangles;
- cell-centered source and target values;
- identical declared physical domain boundaries;
- no missing source cells;
- one scalar component;
- `outside_domain_policy: reject`;
- `missing_value_policy: reject`.

The method may change grid resolution. It may also use unequal x/y spacing. It may not rotate a grid or infer cell boundaries from scattered points.

Normalize and map cell centers with the same equivalence rule. Resolve every accepted center to its canonical integer `(i, j)` cell index before value lookup; never use a parsed floating-point coordinate tuple as an exact dictionary key. This preserves deterministic lookup after declared decimal scale and offset transforms without changing the output coordinates or mapping method.

## Coverage

Coverage is area weighted over the target domain. The request supplies a coverage criterion and its source reference. The code does not insert `1.0` or any other acceptance threshold. In the recommended equal-domain profile, full coverage is normally declared by the caller, but that decision remains in the request.

When domains differ, v0.1 returns `TARGET_DOMAIN_MISMATCH` and releases no mapped field. This restriction avoids comparing the full source integral with an integral over a different target domain.

## Integral verification

For cell averages:

```text
source_integral = sum(source_value * source_cell_area)
target_integral = sum(target_value * target_cell_area)
relative_error  = abs(target_integral - source_integral) / abs(source_integral)
```

If both integrals are zero, relative error is zero. If the source integral is zero and the target integral is non-zero, relative error is undefined; v0.1 reports `PARTIAL / ZERO_REFERENCE_INTEGRAL` rather than inventing a denominator.

The request supplies the relative-error operator, threshold, and source reference. An integral PASS proves only that the declared numerical criterion was met. It does not validate local features, extrema, gradients, physics, or scientific meaning.

## Method provenance

The mapping report records method name, caller parameters, source and target shape, cell representation, coverage, output reference, and output SHA-256. The final field manifest binds these to the original source artifact identity and the shared gate report.
