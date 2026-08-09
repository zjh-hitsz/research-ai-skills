# Simulation validation checklist

Use this checklist to decide which gates a request needs. It is not a source of thresholds.

| Check family | Evidence to provide | Criterion that must come from input | What a non-PASS result means |
|---|---|---|---|
| Input identity | Input manifest, hashes, units, coordinate/sign conventions | Required identity and allowed version | Evidence may belong to a different input or configuration. |
| Evidence integrity | Structured evidence manifest, declared media type, parse record, and hash result | Required parser/format/hash policy | A referenced artifact may exist but be unreadable, changed, or attached to the wrong snapshot. |
| Solver execution | Exit state, logs, stored solution identity | Qualifying completion state | A file may exist without a qualifying solved state. |
| Numerical convergence | Residual/history or configured stopping record | Variable-specific convergence rule | Solver completion alone does not prove convergence. |
| Conservation | Source/target or inlet/outlet integral values | Error metric, operator, threshold, units, source | Balance does not satisfy the declared tolerance or cannot be computed. |
| Connectivity | Explicit graph nodes, edges, direction, ports/pairs | Connectivity mode and expected boolean | Required regions are unreachable under the supplied topology contract. |
| Boundary-condition identity | Boundary/selection manifest | Required selections and values | Cases may not implement the intended physical setup. |
| Fair comparison | Case A/B condition manifests | Fields that must match and explicit numeric tolerances | A performance difference may be confounded by unequal conditions. |
| Independent re-solve | Frozen design identity and independent run record | Required solver identity and allowed difference | A relaxed or displayed design is not yet qualified as an independent result. |
| Mesh/time-step sensitivity | Resolution series and response values | Acceptance metric and tolerance | Resolution dependence remains unresolved. |
| Reproducibility | Environment, seed, input hash, entrypoint, output hash | Required repeat count and allowed variation | The result is not yet reproducibly frozen. |
| Surrogate/high-fidelity consistency | Candidate identity and high-fidelity verification | Constraint and error criteria | Surrogate feasibility does not establish high-fidelity feasibility. |
| Experimental comparison | Measurement identity and uncertainty | Comparison metric and uncertainty rule | Numerical agreement or disagreement remains unqualified. |

## v0.1 automation boundary

The bundled scripts implement only:

- scalar conservation error;
- explicit graph connectivity;
- case-condition fairness;
- required-gate aggregation;
- JSON-to-Markdown rendering.

Prepare all other checks externally as gate objects that satisfy the common contract. Do not represent an unimplemented check as PASS. Use PARTIAL or SKIPPED with a reason when evidence is incomplete or the check was not run.

## Minimum evidence hygiene

- Give every PASS or FAIL gate at least one evidence reference.
- Keep raw evidence outside the gate report; store only stable string references or structured evidence manifests and small measured summaries.
- State units and sign conventions where values are dimensional.
- Record the criterion source separately from the evidence source.
- Separate solver state from validation state: solved, converged, conserved, connected, fair, and reproducible are distinct gates.
- Record externally checked artifact parsing or hash state under `evidence-integrity`; the Skill does not perform that check automatically.
