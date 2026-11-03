"""Service plumbing for the standard-library workbench server: settings, API keys and roles, rate
limits, JSON logs with request IDs, Prometheus metrics and health checks. No third-party packages.

Settings come from WITNESSFORGE_* environment variables and are checked once at startup. With
WITNESSFORGE_ENV=production the server refuses to start without hashed API keys, and it never listens
on a non-loopback address without them.

    python -m witnessforge.ops keygen      # a new API key and the line to add to WITNESSFORGE_API_KEYS
"""
from __future__ import annotations

import contextvars
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- what differs between labs -------------------------------------------------------
PREFIX = "WITNESSFORGE"
DEFAULT_PORT = 8765
DEFAULT_DATA_DIR = "cases"
# Every caller's key carries one role; each route asks for one permission. Static pages and
# /healthz, /readyz are public; every /api route needs "read" (GET) or "run" (POST).
ROLES: dict[str, set[str]] = {
    "analyst": {"run", "read"},     # people running experiments
    "viewer": {"read"},             # reviewers opening saved cases and reports
    "monitor": {"metrics"},
    "admin": {"run", "read", "metrics"},
}
# --------------------------------------------------------------------------------------

log = logging.getLogger(PREFIX.lower())
request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_HASHED = re.compile(r"^sha256:[0-9a-f]{64}$")


class ConfigError(RuntimeError):
    """A setting is missing or unsafe; the server refuses to start."""


@dataclass
class Settings:
    env: str = "development"
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: str = DEFAULT_DATA_DIR
    api_keys: dict[str, dict[str, str]] = field(default_factory=dict)  # sha256 hex -> {user, role}
    rate_limit: int = 120  # requests per minute per client
    allowed_hosts: tuple[str, ...] = ()
    log_level: str = "INFO"

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def auth(self) -> bool:
        return bool(self.api_keys)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _keys(raw: str, production: bool) -> dict[str, dict[str, str]]:
    try:
        table = json.loads(raw)
    except ValueError:
        raise ConfigError(f"{PREFIX}_API_KEYS is not valid JSON") from None
    if not isinstance(table, dict):
        raise ConfigError(f'{PREFIX}_API_KEYS must be {{"sha256:<hex>": {{"user": ..., "role": ...}}}}')
    out = {}
    for key, ident in table.items():
        if not isinstance(ident, dict) or ident.get("role") not in ROLES:
            raise ConfigError(f"every API key needs a role; roles are {', '.join(sorted(ROLES))}")
        if _HASHED.match(key):
            digest = key.split(":", 1)[1]
        elif production:
            raise ConfigError(f"in production API keys must be stored as sha256:<hex> (python -m {PREFIX.lower()}.ops keygen)")
        else:
            digest = hash_key(key)  # development only: a plain key, hashed here
        out[digest] = {"user": str(ident.get("user") or ""), "role": ident["role"]}
    return out


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def get(name, default=""):
        return env.get(f"{PREFIX}_{name}", default).strip()

    mode = get("ENV", "development")
    if mode not in ("development", "production"):
        raise ConfigError(f"{PREFIX}_ENV must be development or production")
    production = mode == "production"
    raw = get("API_KEYS")
    if not raw and get("KEYS_FILE"):
        try:
            raw = Path(get("KEYS_FILE")).read_text(encoding="utf-8")
        except OSError:
            raise ConfigError(f"{PREFIX}_KEYS_FILE can't be read") from None
    keys = _keys(raw, production) if raw else {}
    host = get("HOST", "127.0.0.1")
    try:
        port, rate = int(get("PORT", str(DEFAULT_PORT))), int(get("RATE_LIMIT", "120"))
    except ValueError:
        raise ConfigError(f"{PREFIX}_PORT and {PREFIX}_RATE_LIMIT must be integers") from None
    if not 0 <= port <= 65535 or rate < 1:
        raise ConfigError(f"{PREFIX}_PORT must be 0-65535 and {PREFIX}_RATE_LIMIT at least 1")
    if production and not keys:
        raise ConfigError(f"production needs API keys ({PREFIX}_API_KEYS or {PREFIX}_KEYS_FILE)")
    if not _loopback(host) and not keys:
        raise ConfigError(f"refusing to listen on {host} without API keys")
    hosts = tuple(h.strip().lower() for h in get("ALLOWED_HOSTS").split(",") if h.strip())
    if "*" in hosts:
        raise ConfigError(f"{PREFIX}_ALLOWED_HOSTS can't be *; list the names clients use")
    return Settings(env=mode, host=host, port=port, data_dir=get("DATA_DIR", DEFAULT_DATA_DIR), api_keys=keys,
                    rate_limit=rate, allowed_hosts=hosts, log_level=get("LOG_LEVEL", "INFO").upper())


# ---- requests ------------------------------------------------------------------------

def host_allowed(header: str, settings: Settings) -> bool:
    """The Host header must name this server: a loopback name (any port, so port mapping works),
    or a name listed in ALLOWED_HOSTS ("lab.example.org" matches any port, "lab.example.org:443" one)."""
    host = header.strip().lower()
    name = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    if name in ("127.0.0.1", "localhost", "[::1]"):
        return True
    return any(host == h or (":" not in h and name == h) for h in settings.allowed_hosts)


def origin_allowed(origin: str, settings: Settings) -> bool:
    if "://" not in origin:
        return False
    scheme, rest = origin.split("://", 1)
    return scheme in ("http", "https") and "/" not in rest and host_allowed(rest, settings)


def identify(headers, settings: Settings) -> dict[str, str] | None:
    key = headers.get("X-API-Key", "")
    auth = headers.get("Authorization", "")
    if not key and auth.lower().startswith("bearer "):
        key = auth[7:].strip()
    return settings.api_keys.get(hash_key(key)) if key else None


def permitted(ident: dict[str, str] | None, permission: str) -> bool:
    return ident is not None and permission in ROLES.get(ident["role"], set())


class RateLimiter:
    """A token bucket per client: `rate` requests a minute, bursts up to `rate`."""

    def __init__(self, rate: int):
        self.rate, self.buckets, self.lock = rate, {}, threading.Lock()

    def allow(self, client: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(client, (float(self.rate), now))
            tokens = min(float(self.rate), tokens + (now - last) * self.rate / 60)
            if tokens < 1:
                self.buckets[client] = (tokens, now)
                return False, max(1, int((1 - tokens) * 60 / self.rate) + 1)
            self.buckets[client] = (tokens - 1, now)
            if len(self.buckets) > 10_000:  # forget idle clients
                self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 120}
            return True, 0


class Metrics:
    """Request counters and latency sums in the Prometheus text format."""

    def __init__(self):
        self.lock, self.requests, self.seconds, self.decisions = threading.Lock(), {}, {}, {}

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self.lock:
            k = (method, route, str(status))
            self.requests[k] = self.requests.get(k, 0) + 1
            s = self.seconds.setdefault((method, route), [0, 0.0])
            s[0] += 1
            s[1] += seconds

    def decision(self, name: str) -> None:
        with self.lock:
            self.decisions[name] = self.decisions.get(name, 0) + 1

    def text(self) -> str:
        p = PREFIX.lower()
        with self.lock:
            lines = ["# TYPE http_requests_total counter"]
            lines += [f'http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}'
                      for (m, r, s), n in sorted(self.requests.items())]
            lines += ["# TYPE http_request_duration_seconds summary"]
            for (m, r), (n, t) in sorted(self.seconds.items()):
                lines += [f'http_request_duration_seconds_count{{method="{m}",route="{r}"}} {n}',
                          f'http_request_duration_seconds_sum{{method="{m}",route="{r}"}} {t:.6f}']
            lines += [f"# TYPE {p}_decisions_total counter"]
            lines += [f'{p}_decisions_total{{decision="{d}"}} {n}' for d, n in sorted(self.decisions.items())]
        return "\n".join(lines) + "\n"


def new_request_id(headers) -> str:
    rid = headers.get("X-Request-ID", "")
    return rid if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rid) else uuid.uuid4().hex


# ---- logging -------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": request_id.get()}
        out.update(getattr(record, "fields", {}))
        if record.exc_info:
            out["error"] = record.exc_info[0].__name__ if record.exc_info[0] else "error"
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.captureWarnings(True)


def keygen() -> None:
    key = f"{PREFIX.lower()}_{secrets.token_urlsafe(32)}"
    print(f"API key (give to the client, store nowhere else): {key}")
    print(f'Add to {PREFIX}_API_KEYS:  {{"sha256:{hash_key(key)}": {{"user": "<name>", "role": "<{"|".join(sorted(ROLES))}>"}}}}')


if __name__ == "__main__":
    if sys.argv[1:] != ["keygen"]:
        sys.exit(f"usage: python -m {PREFIX.lower()}.ops keygen")
    keygen()
