# Architecture

## Data flow

```text
Browser / CLI
      |
      v
Strict trace validation ---- malformed actions -> reject request
      |
      +------> Action-only reference specification -> expected records
      |
      +------> SQLite reference app (schema 1 / 2 / 3)
                        |
                        v
                 Selected parser -> canonical records
                        |
                semantic comparison
                        |
              repeated execution oracle
                        |
     PASS / FAIL(field + message) / UNRESOLVED
                        |
       dependency-aware trace reduction
                        |
         single-action minimality checks
                        |
       preserved fresh SQLite witnesses
                        |
      JSON + HTML + SHA-256 manifest + ZIP
```

## Core components

### Action specification

`validate_trace` checks exact fields, field types, unique action/message IDs, live sources, and ordering. Maximum input is 500 actions; text is limited to 10,000 characters per field. The HTTP layer additionally limits the request to 1 MiB.

`_ground_truth` reduces the action trace in memory. It never reads the SQLite database or calls the parser. This separates the intended semantics from the storage and extraction implementations. All three are controlled reference implementations, so independent real-app validation remains future work.

### SQLite reference application

`replay_to_database` creates a new database using exclusive file creation. It refuses to overwrite an existing file. Schema 1 stores flat message columns; schema 2 renames columns and changes edit/deletion representations; schema 3 stores an envelope and payload as JSON with state flags. All support reactions and retain tombstone metadata.

### Parser boundary

Built-in parsers open SQLite read-only. A canonical record contains message identity, actor, recipient, content, forwarding source, edit/deletion flags, and reactions. Record ordering is normalized before comparison. Missing records and incorrect fields are FAIL; malformed records, duplicates, or parser exceptions are UNRESOLVED.

The defective parser intentionally confuses the original author's identity with the forwarding actor when an edited forward is deleted in schemas 2 and 3. The corrected parser always uses the forwarding actor. Schema 1 is a compatibility control.

### Repeated oracle and minimizer

Each candidate runs `repeats` independent replays. Every repeat must agree on status, signature, findings, actual records, and errors. Inconsistent candidates are UNRESOLVED.

The signature is the initial finding's **error field/type and message ID**. It is deliberately not a hash of the whole trace or every expected/actual value. Reductions may change unrelated evidence. This scope is included in the documentation and must not be represented as preserving all semantics of the original session.

The reduction budget counts individual parser executions, not merely candidate traces. The algorithm first removes chunks, propagates removal through dependencies, and then performs a single-action removal sweep. Unknown results never support a 1-minimality claim.

### Repair evaluation

Nine independently authored scenarios across three versions cover empty data, Unicode, normal tombstones, live forwards, unedited forward tombstones, edited forward tombstones, source edits, reactions, and multi-hop forwarding. A candidate must match all canonical fields. The known buggy parser is evaluated alongside it as a baseline. These examples are separate from the minimizer's default trace; they are not a statistically representative production dataset.

### Case persistence

Experiments are written into a newly created temporary directory beneath the selected cases directory. A completed directory is renamed to its final ID. Failed writes remove only that exact temporary directory. Original and minimized witness materializations are checked against the oracle observations. A disagreement marks the case UNRESOLVED.

Each case stores `case.json`, traces, HTML, original/minimized SQLite witnesses and observations, and `manifest.json`. All evidence paths inside reports are relative. A verified ZIP can be opened without this application. Verification detects modified, missing, or unexpected files and rejects unsafe manifest paths.

### UI and HTTP layer

The frontend uses plain browser JavaScript and local CSS. No build tooling or external CDN is required. `ThreadingHTTPServer` binds only to 127.0.0.1. A lock admits one experiment at a time. Static files are explicitly enumerated; there is no arbitrary directory browsing. Host and Origin checks reduce cross-site access to the loopback service.

## API

| Method / path | Request | Response |
| --- | --- | --- |
| GET `/api/health` | — | Service health and lab scope |
| GET `/api/demo` | — | Default trace, supported versions and parsers |
| POST `/api/run` | `{trace,version,parser,repeats,max_evaluations}` | Saved case with reduction result and artifact URLs |
| POST `/api/validate-repair` | `{parser:"fixed"}` or `{}` | Overall result, summary, scenario/version rows |
| GET `/api/cases` | — | Existing case summaries |
| GET `/api/cases/{id}/json` | — | Full case JSON |
| GET `/api/cases/{id}/report` | — | Standalone HTML download |
| GET `/api/cases/{id}/bundle` | — | Verified ZIP download |
| GET `/api/cases/{id}/verify` | — | Current manifest check |

HTTP requests use JSON. Version values are strings `"1"`, `"2"`, `"3"`; parser values are `"buggy"`, `"fixed"`. The browser API permits 1–5 repeats and an evaluation budget of 2–1000. The CLI permits 1–10 repeats and a budget of 1–10000. A budget too small for the requested baseline repeats produces UNRESOLVED.

## Deliberate design choices

- SQLite and the Python standard library make the experiment reproducible offline.
- Dependency closure preserves executable traces instead of accepting broken references as parser bugs.
- Explicit unknown results prevent exceptions and unstable observations from becoming false findings.
- Separate ground truth and parser paths make semantic regressions visible.
- A bounded lab exposes what has actually been validated; replacing the simulator with a real application is a distinct integration effort.
