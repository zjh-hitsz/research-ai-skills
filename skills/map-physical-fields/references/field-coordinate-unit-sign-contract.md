# Field, coordinate, unit, and sign contract

## Required declarations

A v0.1 field is one scalar component sampled as a cell average on a regular two-dimensional grid. The request must name the field, physical role, scalar component/CSV column, field units, and sign convention. Matching strings are not proof that two variables are physically equivalent; a researcher or upstream model audit owns that judgement.

Coordinates must declare:

- Cartesian system;
- exactly two physical axis names and their order;
- origin and direction for both axes;
- one coordinate-unit declaration;
- regular grid shape, spacing, physical minimum/maximum, and cell centering.

The source CSV coordinate-column list must equal the declared source `axis_order`. This prevents a syntactically valid table from silently swapping axes.

## Explicit normalization

The v0.1 transform is a signed, axis-aligned affine transform:

```text
output[j] = scale[j] * input[permutation[j]] + offset[j]
```

The request must provide all three arrays. `permutation` must be `[0, 1]` or `[1, 0]`; each scale must be finite and non-zero; offsets must be finite. The declared permutation must map the source axis names to the target axis names.

Use scale for an explicitly authorized coordinate-unit conversion or direction change and offset for an explicitly authorized origin shift. The Skill does not derive either from coordinate ranges, file names, software conventions, or apparent symmetry.

The request must also declare the normalized source grid. After transformation, every point must match that grid's declared cell centers. This second declaration is deliberate: it makes the transform auditable instead of inferred.

## Field units and sign

v0.1 does not convert scalar-field units or silently flip scalar signs. Source and target field units and sign-convention text must match exactly. If a future workflow needs a value conversion, it requires a separately reviewed contract and evals; do not encode it as a coordinate scale.

Examples of distinctions that must be resolved upstream include:

- signed normal flux versus vector magnitude;
- positive into a surface versus positive out of a surface;
- density versus cell-integrated quantity;
- temperature versus temperature difference;
- solver variable aliases that happen to share units.

## Data-table rules

- One row per source cell center.
- Exactly two declared coordinate columns and one declared scalar component are consumed.
- Values and coordinates must parse as finite numbers.
- Missing values, duplicate coordinate pairs, unexpected grid shape, or coordinate locations inconsistent with declared centers fail validation.
- Row order is irrelevant; output order is deterministic with the second axis outermost and first axis innermost.

Passing these checks means the table matches its declarations. It does not mean the values are physically correct.
