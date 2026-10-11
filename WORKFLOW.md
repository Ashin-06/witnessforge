# How the project works

For an already-exported real database, use the separate [external evidence workflow](INTEGRATION.md): preserve database/WAL bytes, recover a disposable copy, extract supported SMS records, inspect uncertainty, verify the manifest, then export a ZIP. For an owned debuggable Android app, the same workflow can begin with an explicitly allowlisted ADB capture. Imported files have no action ground truth, so the reduction procedure below applies to the controlled reference application only.

## Worked example

1. Noor sends message `source-42` to Maya.
2. Maya forwards it to Ishan as `forwarded-42`.
3. Maya edits the forwarded message.
4. Maya deletes the forwarded message.

The reference app retains a tombstone with message identity, sender, recipient, source reference, flags, and reactions. Text is `null`. This is the reference app's explicit behavior, not a claim about how a real messaging app retains deleted content.

The expected sender of the forward is Maya. In schema 2 or 3, the intentionally buggy parser uses Noor, the original author. The comparison produces `FIELD_SENDER` for `forwarded-42`.

The default trace mixes these four events with sixteen irrelevant actions. The minimizer is not told which four matter.

## Reduction procedure

1. Validate the complete trace and construct its dependency graph.
2. Rebuild the database and run the selected parser repeatedly. Stop without minimizing if there is no stable failure.
3. Save the first mismatch's type/field and message ID as the target signature.
4. Split the current trace into chunks. Try removing a chunk and its dependent actions.
5. Replay that smaller candidate from a fresh database. Keep it only if the same target failure reproduces in every repeat.
6. Increase removal granularity when larger chunks cannot be removed.
7. Verify every remaining single-action removal, including dependency propagation.
8. Stop when all removals are resolved and fail to reproduce the target, or when the evaluation budget is exhausted.
9. Preserve fresh original and minimized SQLite witnesses and compare their observations with the oracle results.
10. Write the report, traces, case JSON, and hash manifest.

In the demonstration, removing the initial send also removes the dependent forward and its edits/deletion, so it cannot preserve the target. Removing forward, edit, or delete individually also eliminates the target. The four-action result is therefore 1-minimal under this removal rule.

## Dependency examples

| Operation | Required earlier action |
| --- | --- |
| send | None; creates a unique message |
| forward | Creator of a live source message; forwarding chains preserve ancestor creators transitively |
| edit | Creator of the referenced live message |
| react | Creator of the referenced live message |
| delete | Creator of the referenced live message |

Removing an earlier edit may change a later forward's text; the smaller trace is replayed rather than reusing the original database. This is intentional: the oracle, not a stale snapshot, decides whether the candidate still reproduces the target.

## Why outcomes matter

- A parser returning valid but incorrect canonical records is a semantic FAIL.
- A parser crashing, returning malformed output, changing its database, timing out, or disagreeing between repeats is UNRESOLVED.
- A candidate producing a different bug is not accepted just because it also fails.
- An exhausted search retains its best confirmed witness but does not advertise a completed minimality proof.
- A fixed-parser run is PASS and retains the original trace. It does not claim to have reduced a nonexistent failure.

## Repair workflow

Run the fixed parser on the minimized trace first, then use `validate-repair` for independent scenarios. The full validation compares all fields, including reactions and forwarding references. A change that fixes sender attribution but drops reactions should fail repair validation.

For custom parsers, implement `parse(database_path)` and supply it through the CLI. Parser exceptions become unresolved experiments. The repair suite's overall status is FAIL if any case fails or is unresolved. A passed suite is scoped to its tested schemas and scenarios.

## Presenting this project

Use three visible runs: failing schema 2 with 20→4 reduction, fixed parser on the same trace, and the independent repair matrix. Show an original/minimized database and verify its manifest. Explain the seeded defect and synthetic scope. A strong next research contribution is reproducing the workflow on a real application/parser pair with independently validated ground truth and withheld versions.
