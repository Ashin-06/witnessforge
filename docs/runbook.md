# Runbook

How to deploy and operate the workbench as a service. Commands assume the repository root.

## Deploy

```bash
docker build -t witnessforge:1.1.0 .
python -m witnessforge.ops keygen          # one key per person; repeat for each
docker run -d --name witnessforge -p 8765:8765 -v witnessforge-data:/data \
  -e WITNESSFORGE_KEYS_FILE=/run/secrets/witnessforge-keys.json \
  -e WITNESSFORGE_ALLOWED_HOSTS=lab.example.org \
  witnessforge:1.1.0
curl -fsS http://127.0.0.1:8765/readyz
```

The image has no third-party packages, runs as a non-root user and keeps cases in `/data`. It
refuses to start, and says why, without hashed API keys. Put it behind a TLS-terminating proxy
and list the proxy's public name in `WITNESSFORGE_ALLOWED_HOSTS`.

## Configure

| Variable | Required in production | Meaning |
|---|---|---|
| `WITNESSFORGE_ENV` | — | `development` (default) or `production` |
| `WITNESSFORGE_API_KEYS` / `WITNESSFORGE_KEYS_FILE` | yes | JSON `{"sha256:<hex>": {"user": "...", "role": "..."}}`. Roles: `analyst` (run, read), `viewer` (read), `monitor` (metrics), `admin` |
| `WITNESSFORGE_HOST`, `WITNESSFORGE_PORT` | — | default `127.0.0.1:8765`; the image uses `0.0.0.0` |
| `WITNESSFORGE_ALLOWED_HOSTS` | when reached by name | comma-separated host names clients use (with or without a port) |
| `WITNESSFORGE_DATA_DIR` | — | case storage; `/data` in the image |
| `WITNESSFORGE_RATE_LIMIT` | — | requests per minute per client, default 120 |
| `WITNESSFORGE_LOG_LEVEL` | — | default `INFO` |

## Watch

- `GET /healthz` (process up), `GET /readyz` (data directory usable), `GET /metrics` (role
  `monitor`): `http_requests_total`, `http_request_duration_seconds`,
  `witnessforge_decisions_total{decision="experiment_run|repair_validated"}`.
- Logs are JSON lines on stdout with a request ID; they hold no trace or parser content.

## Rotate keys

Add the new key's hash, restart, hand over the key, remove the old hash, restart. Browsers keep a
key only for the tab's session.

## Back up and restore

Cases are plain folders with a SHA-256 manifest. Back up the volume; after a restore, run
`python -m witnessforge verify <case folder>` on any case you rely on.

## Incidents

1. **Leaked key.** Remove its hash and restart; review the access log for that user.
2. **A case fails `verify`.** Treat it as altered; re-run the experiment from its saved trace.
