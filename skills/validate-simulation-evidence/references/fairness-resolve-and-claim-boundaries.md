# Fairness, independent re-solve, and claim boundaries

## Fair comparison contract

Before comparing case A with case B, explicitly decide which conditions must match. Typical candidates include domain, active area, material properties, boundary conditions, heat input, flow definition, mesh policy, solver version, stopping rule, design budget, and post-processing definitions. These are prompts for human selection, not automatic defaults.

Use exact equality for categorical or identity fields. For numeric fields, provide each tolerance and its rule:

- `absolute`;
- `relative`, with reference `case-a`, `case-b`, or `max-absolute`;
- `absolute-or-relative`;
- `absolute-and-relative`.

Do not silently normalize mismatched units or infer that similarly named variables are equivalent. Resolve units and semantics before calling the fairness script.

## Relaxed result versus independent re-solve

Keep these identities separate:

1. optimization or relaxed-field result;
2. post-processed or thresholded design;
3. independently reconstructed model;
4. independently solved result;
5. qualified evidence after gates.

A converged relaxed objective does not automatically qualify the thresholded or reconstructed design. Freeze the design identity, run the independent solve through an authorized external workflow, and submit its evidence as separate gates.

## Claim boundary

For legacy `1.0.0` reports, use three explicit lists in the request:

- `supported`: statements directly supported by passed required gates;
- `conditional`: statements that remain limited by partial, skipped, optional, sensitivity, or model-scope evidence;
- `unsupported`: statements ruled out by failed or absent evidence.

For the additive `1.1.0` profile, use `claim_boundary.claims`. Every claim object contains `claim_id`, `text`, `status`, `supporting_gate_ids`, and `limitations`. Supporting gate IDs must exist in the same report. Claim status remains user-supplied; the evaluator checks structure and references but does not infer or approve scientific meaning.

The evaluator preserves either profile and renders it; it does not write scientific claims. Do not mix the legacy lists and object claims in one report. Review claims manually whenever overall is not PASS or an optional gate is non-PASS.

## Prohibited inference

Do not infer any of the following from a gate report alone:

- physical correctness;
- experimental validity;
- global optimality;
- generalization beyond the tested domain;
- causal mechanism from a multi-change comparison;
- novelty or priority;
- publication readiness.
