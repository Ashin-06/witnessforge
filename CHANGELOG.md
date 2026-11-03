# Changelog

All notable changes to this project. Versions follow [semantic versioning](https://semver.org/).

## 1.1.0 — 2026-11-03

### Added
- The workbench can run as a service: settings from `WITNESSFORGE_*` environment variables, checked
  at startup; production refuses to start without hashed API keys, and the server never binds a
  non-loopback address without keys. Keys carry roles (`analyst`, `viewer`, `monitor`, `admin`).
  The page asks for a key once per browser session and sends it with every API call and download.
- Per-client rate limit, request IDs, JSON access logs without submitted content, `/healthz`,
  `/readyz` and Prometheus `/metrics` (`python -m witnessforge.ops keygen` makes keys). Still no
  third-party runtime dependencies.
- `WITNESSFORGE_ALLOWED_HOSTS` for names other than loopback; loopback is accepted on any port, so
  Docker port mapping works.
- A container image (slim, non-root, health check), pinned development tools in
  `requirements-dev.lock`, and a CI job with ruff, bandit, pip-audit and coverage next to the
  existing Ubuntu/Windows matrix, now on Python 3.12 and 3.13.
- `SECURITY.md`, `docs/threat-model.md`, `docs/runbook.md`, MIT licence.
- The CI workflow, installation check, ADB and external-evidence hardening and `test_hardening.py`
  from the 11 October work, first published here.

### Changed
- Python 3.12 or newer is required (was 3.10).

### Unchanged
- Replay, reduction, repair validation and bundles; the 73 earlier tests pass unchanged.

## 1.0.0 — 2026-10-10

First release: the synthetic reference app, reducer, repair validation, workbench and CLI.
