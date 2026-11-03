# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately, through GitHub's private vulnerability
reporting on this repository (Security → Report a vulnerability) or by contacting the
maintainer, [@Ashin-06](https://github.com/Ashin-06), directly. Do not open a public issue.

Include what you found, how to reproduce it and what an attacker could do with it. You should
get an acknowledgement within 5 working days. Please give us 90 days to fix an issue before
disclosing it, and don't test against anyone else's deployment.

## Supported versions

| Version | Supported |
|---|---|
| 1.1.x | yes |
| 1.0.x | no (loopback-only release without API keys) |

## Scope

In scope: the workbench server and its API keys, rate limits and host checks, trace validation, the
reducer and repair validation, case storage and evidence bundles, SQLite import and ADB acquisition,
the plugin worker, the container image and the CI configuration. A crafted trace, SQLite file or
plugin result that escapes the case directory, runs code in the server, corrupts another case or
makes a bundle verify when it was altered is in scope.

Out of scope: the deliberately seeded parser defect (it is the demonstration), and findings that
need an already-compromised host or a leaked API key.

What the lab itself does and does not claim to test is in [SECURITY_SCOPE.md](SECURITY_SCOPE.md).

This project has **not** had an independent security review or penetration test.
