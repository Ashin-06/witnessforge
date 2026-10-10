# Research context and contribution boundary

The idea was screened against public research and repositories in October 2026. The screening is not exhaustive and cannot establish that no similar unpublished, private, or unindexed work exists.

## Closest work found

| Existing work | What it already contributes | Implication for this project |
| --- | --- | --- |
| [Puma / Netherlands Forensic Institute](https://github.com/NetherlandsForensicInstitute/puma) and its 2025 paper | Mobile action automation and generation of forensic reference data triggered by app updates | Mobile replay and update-triggered testing are not novel claims here. |
| [ADARE, 2026](https://arxiv.org/abs/2605.28476) | Executable forensic expectations, action-level state transition tests, and cross-version desktop tool evaluation | Action-by-action forensic testing and regression testing already exist. |
| [Resilience of forensic evidence acquisition under database schema drift, 2026](https://www.eurecom.fr/publication/8521) | Study of 320 versions of 20 Android apps, including invalid extraction and missed evidence | Database drift and silent omission are established problems, not discoveries of this prototype. |
| [ALEAPP](https://github.com/abrignoni/ALEAPP) | Open-source Android artifact parsers and testing facilities | Production parser support should be integrated and evaluated rather than recreated under a novelty claim. |

Delta debugging, dependency handling, and automated program repair are also established techniques. Combining their names is not sufficient evidence of a research contribution.

## Proposed contribution to investigate

Automatically reduce a real mobile action history while preserving a specified forensic semantic error, retain paired evidence witnesses, and evaluate proposed repairs against independent actions and app versions. An exact end-to-end implementation of that narrower combination was not located in the reviewed sources. This is a qualified search result, not a proof of novelty.

## What this delivery demonstrates

- A working dependency-aware reducer with repeated semantic observations.
- A real SQLite reference app with three storage schemas and a controlled attribution defect.
- Evidence bundles that retain original and reduced observations.
- A repair test matrix and a custom parser execution interface.
- Explicit budget, unresolved-result, and minimality boundaries.

## What would strengthen a paper or startup claim

1. Validate one real app/parser pairing, including historical bugs discovered independently of this project.
2. Compare manual debugging, generic sequence reduction, and the dependency-aware reducer under the same budget.
3. Measure analyst time, replay count, witness size, repeated-replay success, and incorrectly accepted repairs.
4. Include failures: unsupported artifacts, emulator instability, false positives, and cases that cannot be reduced.
5. Publish fixtures and evaluation procedures where data rights allow.
6. Interview forensic-tool developers and laboratories before claiming commercial demand.

The bundled bug is deliberately inserted, all names/messages are synthetic, and no claim of finding a new vulnerability in a commercial forensic tool is made.
