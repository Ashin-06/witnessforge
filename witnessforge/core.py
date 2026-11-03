"""Deterministic, synthetic SQLite forensic-parser experiments.

This is a reference messaging application, NOT an Android emulator or validation
of any real application's artifacts. Ground truth is reduced from actions before
the independently implemented SQLite simulator or parser runs.

Parser plugin contract: ``parser(database_path: pathlib.Path) -> list[dict]``.
Each result must have exactly these canonical fields:
  message_id: str, sender: str, recipient: str, text: str | None,
  source_id: str | None, edited: bool, deleted: bool,
  reactions: list[{"sender": str, "text": str}].
Deleted messages retain metadata and reactions but have text=None. Results and
reactions may be returned in any order. Unknown/missing fields, duplicate IDs,
exceptions, and inconsistent repeat results are UNRESOLVED, not parser failures.
Plugins are trusted local code; this module does not sandbox their execution.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Callable

Parser = str | Callable[[Path], list[dict]]
VERSIONS = ("1", "2", "3")
MAX_ACTIONS = 500
MAX_TEXT_LENGTH = 10_000
FIELDS = {"message_id", "sender", "recipient", "text", "source_id", "edited", "deleted", "reactions"}

DEFAULT_TRACE = [
    {"id": "a01", "op": "send", "message_id": "chat-01", "sender": "Ravi", "recipient": "Maya", "text": "Meeting at ten"},
    {"id": "a02", "op": "react", "message_id": "chat-01", "sender": "Maya", "text": "thumbs-up"},
    {"id": "a03", "op": "send", "message_id": "source-42", "sender": "Noor", "recipient": "Maya", "text": "Draft budget"},
    {"id": "a04", "op": "send", "message_id": "chat-02", "sender": "Maya", "recipient": "Ravi", "text": "Confirmed"},
    {"id": "a05", "op": "edit", "message_id": "chat-01", "text": "Meeting at eleven"},
    {"id": "a06", "op": "forward", "message_id": "forwarded-42", "source_id": "source-42", "sender": "Maya", "recipient": "Ishan"},
    {"id": "a07", "op": "send", "message_id": "chat-03", "sender": "Ishan", "recipient": "Maya", "text": "Thanks"},
    {"id": "a08", "op": "react", "message_id": "forwarded-42", "sender": "Ishan", "text": "eyes"},
    {"id": "a09", "op": "edit", "message_id": "chat-02", "text": "Confirmed for eleven"},
    {"id": "a10", "op": "edit", "message_id": "forwarded-42", "text": "Approved budget"},
    {"id": "a11", "op": "forward", "message_id": "chat-04", "source_id": "chat-01", "sender": "Maya", "recipient": "Noor"},
    {"id": "a12", "op": "send", "message_id": "chat-05", "sender": "Noor", "recipient": "Ravi", "text": "Lunch?"},
    {"id": "a13", "op": "delete", "message_id": "chat-03"},
    {"id": "a14", "op": "react", "message_id": "chat-05", "sender": "Ravi", "text": "yes"},
    {"id": "a15", "op": "delete", "message_id": "forwarded-42"},
    {"id": "a16", "op": "send", "message_id": "chat-06", "sender": "Ravi", "recipient": "Noor", "text": "At noon"},
    {"id": "a17", "op": "edit", "message_id": "chat-06", "text": "At twelve thirty"},
    {"id": "a18", "op": "react", "message_id": "chat-04", "sender": "Noor", "text": "ok"},
    {"id": "a19", "op": "delete", "message_id": "chat-02"},
    {"id": "a20", "op": "send", "message_id": "chat-07", "sender": "Maya", "recipient": "Noor", "text": "See you"},
]


def demo_trace() -> list[dict]:
    return copy.deepcopy(DEFAULT_TRACE)


def _string(value, label: str, limit: int = 256, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > limit:
        raise ValueError(f"{label} must be {'a' if allow_empty else 'a nonempty'} string of at most {limit} characters")


def validate_trace(trace: list[dict]) -> None:
    """Validate format, size, unique IDs, and live-message causal dependencies."""
    if not isinstance(trace, list):
        raise ValueError("trace must be a JSON array of actions")
    if len(trace) > MAX_ACTIONS:
        raise ValueError(f"trace may contain at most {MAX_ACTIONS} actions")
    action_ids, live, created = set(), set(), set()
    required = {
        "send": {"id", "op", "message_id", "sender", "recipient", "text"},
        "forward": {"id", "op", "message_id", "source_id", "sender", "recipient"},
        "edit": {"id", "op", "message_id", "text"},
        "delete": {"id", "op", "message_id"},
        "react": {"id", "op", "message_id", "sender", "text"},
    }
    for index, action in enumerate(trace):
        prefix = f"action {index + 1}"
        if not isinstance(action, dict):
            raise ValueError(f"{prefix} must be an object")
        op = action.get("op")
        if not isinstance(op, str) or op not in required:
            raise ValueError(f"{prefix}: op must be send, forward, edit, delete, or react")
        if set(action) != required[op]:
            missing, extra = required[op] - set(action), set(action) - required[op]
            raise ValueError(f"{prefix}: incorrect fields; missing={sorted(missing)}, extra={sorted(extra, key=str)}")
        for field, value in action.items():
            _string(value, f"{prefix}.{field}", MAX_TEXT_LENGTH if field == "text" else 256, field == "text")
        if action["id"] in action_ids:
            raise ValueError(f"{prefix}: duplicate action id {action['id']!r}")
        action_ids.add(action["id"])
        mid = action["message_id"]
        if op in ("send", "forward"):
            if mid in created:
                raise ValueError(f"{prefix}: message_id {mid!r} already exists")
            if op == "forward" and action["source_id"] not in live:
                raise ValueError(f"{prefix}: forward source {action['source_id']!r} must exist and be live")
            created.add(mid)
            live.add(mid)
        else:
            if mid not in live:
                raise ValueError(f"{prefix}: message {mid!r} must exist and be live")
            if op == "delete":
                live.remove(mid)


def _version(version: str) -> str:
    if version not in VERSIONS:
        raise ValueError("version must be '1', '2', or '3'")
    return version


def _parser(parser: Parser) -> None:
    if not callable(parser) and parser not in ("buggy", "fixed"):
        raise ValueError("parser must be 'buggy', 'fixed', or a callable accepting a database path")


def _ground_truth(trace: list[dict]) -> list[dict]:
    """Action-only specification; it never opens a database or invokes a parser."""
    state = {}
    for event in trace:
        mid, op = event["message_id"], event["op"]
        if op in ("send", "forward"):
            source = event.get("source_id")
            state[mid] = {
                "message_id": mid, "sender": event["sender"], "recipient": event["recipient"],
                "text": event["text"] if op == "send" else state[source]["text"],
                "source_id": source, "edited": False, "deleted": False, "reactions": [],
            }
        elif op == "edit":
            state[mid]["text"] = event["text"]
            state[mid]["edited"] = True
        elif op == "delete":
            state[mid]["deleted"] = True
            state[mid]["text"] = None
        else:
            state[mid]["reactions"].append({"sender": event["sender"], "text": event["text"]})
    for message in state.values():
        message["reactions"].sort(key=lambda x: (x["sender"], x["text"]))
    return [state[mid] for mid in sorted(state)]


_LAYOUTS = {
    "1": ("messages", "id", "sender", "recipient", "body", "forwarded_from", "source_author", "edited", "deleted", "reactions", "mid", "actor", "emoji"),
    "2": ("messages", "message_key", "actor", "peer", "payload", "origin_key", "origin_actor", "edit_version", "tombstone", "reaction_events", "message_key", "actor", "reaction"),
}


def replay_to_database(trace: list[dict], version: str, output_path: Path) -> str:
    """Create a fresh reference-app database. Refuse to replace existing files."""
    validate_trace(trace)
    version = _version(version)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids accidentally destroying a caller's evidence file.
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE reference_app (schema_version TEXT NOT NULL, scope TEXT NOT NULL)")
            db.execute("INSERT INTO reference_app VALUES (?, ?)", (version, "synthetic reference messaging app"))
            if version in _LAYOUTS:
                table, key, sender, peer, body, source, origin, edit, deleted, react, rmid, actor, emoji = _LAYOUTS[version]
                db.execute(f"CREATE TABLE {table} ({key} TEXT PRIMARY KEY, {sender} TEXT NOT NULL, {peer} TEXT NOT NULL, {body} TEXT, {source} TEXT, {origin} TEXT, {edit} INTEGER NOT NULL, {deleted} INTEGER NOT NULL)")
                db.execute(f"CREATE TABLE {react} ({rmid} TEXT NOT NULL, {actor} TEXT NOT NULL, {emoji} TEXT NOT NULL)")
                for event in trace:
                    op, mid = event["op"], event["message_id"]
                    if op in ("send", "forward"):
                        if op == "send":
                            content, source_id, original = event["text"], None, None
                        else:
                            content, previous_sender, previous_origin = db.execute(f"SELECT {body}, {sender}, {origin} FROM {table} WHERE {key}=?", (event["source_id"],)).fetchone()  # nosec B608 - identifiers from the fixed _LAYOUTS table
                            source_id, original = event["source_id"], previous_origin or previous_sender
                        db.execute(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, 0, 0)", (mid, event["sender"], event["recipient"], content, source_id, original))  # nosec B608 - identifiers from the fixed _LAYOUTS table
                    elif op == "edit":
                        db.execute(f"UPDATE {table} SET {body}=?, {edit}={edit}+1 WHERE {key}=?", (event["text"], mid))  # nosec B608 - identifiers from the fixed _LAYOUTS table
                    elif op == "delete":
                        db.execute(f"UPDATE {table} SET {body}=NULL, {deleted}=1 WHERE {key}=?", (mid,))  # nosec B608 - identifiers from the fixed _LAYOUTS table
                    else:
                        db.execute(f"INSERT INTO {react} VALUES (?, ?, ?)", (mid, event["sender"], event["text"]))  # nosec B608 - identifiers from the fixed _LAYOUTS table
            else:
                db.execute("CREATE TABLE message_records (mid TEXT PRIMARY KEY, envelope TEXT NOT NULL, payload TEXT, state INTEGER NOT NULL)")
                db.execute("CREATE TABLE message_reactions (mid TEXT NOT NULL, actor TEXT NOT NULL, value TEXT NOT NULL)")
                for event in trace:
                    op, mid = event["op"], event["message_id"]
                    if op in ("send", "forward"):
                        env = {"actor": event["sender"], "peer": event["recipient"], "source": None, "origin": None}
                        if op == "forward":
                            source_env, payload = db.execute("SELECT envelope, payload FROM message_records WHERE mid=?", (event["source_id"],)).fetchone()
                            source_env = json.loads(source_env)
                            env.update(source=event["source_id"], origin=source_env["origin"] or source_env["actor"])
                        else:
                            payload = json.dumps({"text": event["text"]}, ensure_ascii=False)
                        db.execute("INSERT INTO message_records VALUES (?, ?, ?, 0)", (mid, json.dumps(env, ensure_ascii=False), payload))
                    elif op == "edit":
                        db.execute("UPDATE message_records SET payload=?, state=state|1 WHERE mid=?", (json.dumps({"text": event["text"]}, ensure_ascii=False), mid))
                    elif op == "delete":
                        db.execute("UPDATE message_records SET payload=NULL, state=state|2 WHERE mid=?", (mid,))
                    else:
                        db.execute("INSERT INTO message_reactions VALUES (?, ?, ?)", (mid, event["sender"], event["text"]))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return str(path.resolve())


def parse_database(database_path: Path, parser: str = "fixed") -> list[dict]:
    """Parse the reference application's three documented schemas read-only."""
    if parser not in ("fixed", "buggy"):
        raise ValueError("built-in parser must be 'fixed' or 'buggy'")
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        version = _version(db.execute("SELECT schema_version FROM reference_app").fetchone()[0])
        if version in _LAYOUTS:
            table, key, sender, peer, body, source, origin, edit, deleted, react, rmid, actor, emoji = _LAYOUTS[version]
            raw = db.execute(f"SELECT {key}, {sender}, {peer}, {body}, {source}, {origin}, {edit}, {deleted} FROM {table}").fetchall()  # nosec B608 - identifiers from the fixed _LAYOUTS table
            reactions = db.execute(f"SELECT {rmid}, {actor}, {emoji} FROM {react}").fetchall()  # nosec B608 - identifiers from the fixed _LAYOUTS table
        else:
            raw = []
            for mid, envelope, payload, flags in db.execute("SELECT mid, envelope, payload, state FROM message_records"):
                env = json.loads(envelope)
                raw.append((mid, env["actor"], env["peer"], json.loads(payload)["text"] if payload else None, env["source"], env["origin"], bool(flags & 1), bool(flags & 2)))
            reactions = db.execute("SELECT mid, actor, value FROM message_reactions").fetchall()
    by_message = {}
    for mid, actor, text in reactions:
        by_message.setdefault(mid, []).append({"sender": actor, "text": text})
    result = []
    for mid, sender, recipient, text, source_id, origin, edited, deleted in raw:
        # Seeded regression: the new-schema tombstone fallback misattributes a
        # forwarded, edited, deleted message to its original source's author.
        if parser == "buggy" and version in ("2", "3") and source_id and edited and deleted:
            sender = origin
        result.append({"message_id": mid, "sender": sender, "recipient": recipient, "text": text,
                       "source_id": source_id, "edited": bool(edited), "deleted": bool(deleted),
                       "reactions": sorted(by_message.get(mid, []), key=lambda x: (x["sender"], x["text"]))})
    return sorted(result, key=lambda item: item["message_id"])


def _canonical(rows) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("parser output must be a list of canonical message records")
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("each parser record must contain exactly the documented canonical fields")
        for name in ("message_id", "sender", "recipient"):
            _string(row[name], f"parser record.{name}")
        if row["message_id"] in seen:
            raise ValueError(f"duplicate parsed message_id {row['message_id']!r}")
        seen.add(row["message_id"])
        for name in ("edited", "deleted"):
            if type(row[name]) is not bool:
                raise ValueError(f"parser record.{name} must be a boolean")
        if row["text"] is not None:
            _string(row["text"], "parser record.text", MAX_TEXT_LENGTH, True)
        if row["source_id"] is not None:
            _string(row["source_id"], "parser record.source_id")
        if not isinstance(row["reactions"], list):
            raise ValueError("parser record.reactions must be a list")
        for reaction in row["reactions"]:
            if not isinstance(reaction, dict) or set(reaction) != {"sender", "text"}:
                raise ValueError("reactions require sender and text")
            _string(reaction["sender"], "reaction.sender")
            _string(reaction["text"], "reaction.text", MAX_TEXT_LENGTH, True)
        row = copy.deepcopy(row)
        row["reactions"].sort(key=lambda x: (x["sender"], x["text"]))
        result.append(row)
    return sorted(result, key=lambda x: x["message_id"])


def parse_fixed(database_path: Path) -> list[dict]:
    return parse_database(database_path, "fixed")


def parse_buggy(database_path: Path) -> list[dict]:
    return parse_database(database_path, "buggy")


def _signature(finding: dict) -> str:
    # Preserve the defect's semantic field/class and affected message. Values
    # may change as causal actions are removed; this is label preservation, not
    # an assertion that every differing byte remains identical to the baseline.
    return f"{finding['code']}:{finding['message_id']}"


def _compare(expected: list[dict], actual: list[dict]) -> list[dict]:
    left, right = {r["message_id"]: r for r in expected}, {r["message_id"]: r for r in actual}
    findings = []
    for mid in sorted(left.keys() | right.keys()):
        if mid not in left or mid not in right:
            findings.append({"code": "MISSING_MESSAGE" if mid not in right else "UNEXPECTED_MESSAGE", "message_id": mid, "expected": left.get(mid), "actual": right.get(mid)})
            continue
        for field in sorted(FIELDS - {"message_id"}):
            if left[mid][field] != right[mid][field]:
                findings.append({"code": f"FIELD_{field.upper()}", "message_id": mid, "expected": left[mid][field], "actual": right[mid][field]})
    return findings


def inspect_trace(trace: list[dict], version: str = "2", parser: Parser = "buggy", output_dir: Path | None = None) -> dict:
    validate_trace(trace)
    version = _version(version)
    _parser(parser)
    expected = _ground_truth(trace)
    temp = tempfile.TemporaryDirectory(prefix="witnessforge-") if output_dir is None else None
    directory = Path(temp.name) if temp else Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Each inspection gets its own preserved database when an output directory
    # is requested, so repeated inspections never overwrite earlier evidence.
    candidate = directory / f"evidence-v{version}.sqlite"
    suffix = 1
    while candidate.exists():
        candidate = directory / f"evidence-v{version}-{suffix}.sqlite"
        suffix += 1
    try:
        replay_to_database(trace, version, candidate)
        base = {"status": "UNRESOLVED", "signature": None, "findings": [], "expected": expected, "actual": [],
                "executed_actions": len(trace), "version": version,
                "parser": parser if isinstance(parser, str) else getattr(parser, "__name__", "custom"),
                "scope": "synthetic SQLite reference app", "database_path": None}
        if output_dir is not None:
            base["database_path"] = str(candidate.resolve())
        try:
            before_hash = hashlib.sha256(candidate.read_bytes()).digest() if callable(parser) else None
            actual = _canonical(parser(candidate) if callable(parser) else parse_database(candidate, parser))
            if before_hash is not None and hashlib.sha256(candidate.read_bytes()).digest() != before_hash:
                raise ValueError("parser modified the evidence database; output cannot be trusted")
        except Exception as exc:
            base["error"] = f"{type(exc).__name__}: {exc}"
            return base
        findings = _compare(expected, actual)
        base.update(status="FAIL" if findings else "PASS", findings=findings, actual=actual,
                    signature=_signature(findings[0]) if findings else None)
        return base
    finally:
        if temp:
            temp.cleanup()


def _dependencies(trace: list[dict]) -> dict[str, set[str]]:
    creators, deps = {}, {}
    for action in trace:
        mid, op, aid = action["message_id"], action["op"], action["id"]
        deps[aid] = set()
        if op == "forward":
            deps[aid].add(creators[action["source_id"]])
        elif op not in ("send", "forward"):
            deps[aid].add(creators[mid])
        if op in ("send", "forward"):
            creators[mid] = aid
    return deps


def minimize_trace(trace: list[dict], version: str = "2", parser: Parser = "buggy", repeats: int = 2, max_evaluations: int = 200) -> dict:
    """Find a dependency-closed 1-minimal witness, not a globally shortest trace.

    The evaluation budget counts individual parser executions, including repeat
    checks. Every accepted reduction reproduces the same code AND message ID.
    Unknown or unstable candidates are never accepted as reproductions.
    """
    validate_trace(trace)
    version = _version(version)
    _parser(parser)
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be an integer between 1 and 10")
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 10_000:
        raise ValueError("max_evaluations must be an integer between 1 and 10000")
    original, current = copy.deepcopy(trace), copy.deepcopy(trace)
    deps, evaluations, history = _dependencies(trace), 0, []
    budget_exhausted = False

    def remove_closed(events: list[dict], removed: set[str]) -> list[dict]:
        dropped = set(removed)
        for action in events:
            if deps[action["id"]] & dropped:
                dropped.add(action["id"])
        return [action for action in events if action["id"] not in dropped]

    def oracle(events: list[dict], phase: str) -> dict:
        nonlocal evaluations, budget_exhausted
        if evaluations + repeats > max_evaluations:
            budget_exhausted = True
            result = {"status": "UNRESOLVED", "signature": None, "findings": [], "expected": [], "actual": [],
                      "executed_actions": 0, "error": "Insufficient evaluation budget for all required repeat checks"}
            history.append({"phase": phase, "count": len(events), "status": "UNRESOLVED", "signature": None, "evaluations": evaluations, "reason": "budget"})
            return result
        results = []
        for _ in range(repeats):
            results.append(inspect_trace(events, version, parser))
            evaluations += 1
        result = results[0]
        fingerprints = {json.dumps({k: item.get(k) for k in ("status", "signature", "findings", "actual", "error")}, sort_keys=True, ensure_ascii=False) for item in results}
        if len(fingerprints) != 1:
            result = copy.deepcopy(result)
            result.update(status="UNRESOLVED", signature=None, error="Repeated parser executions produced inconsistent results")
        history.append({"phase": phase, "count": len(events), "status": result["status"], "signature": result["signature"], "evaluations": evaluations})
        return result

    initial = oracle(current, "baseline")
    target = initial["signature"] if initial["status"] == "FAIL" else None
    final, one_minimal = initial, False
    if target is not None:
        partitions = 2
        while current and not budget_exhausted:
            width = max(1, math.ceil(len(current) / partitions))
            reduced = False
            for start in range(0, len(current), width):
                candidate = remove_closed(current, {event["id"] for event in current[start:start + width]})
                result = oracle(candidate, "partition")
                accepted = result["status"] == "FAIL" and result["signature"] == target
                history[-1]["accepted"] = accepted
                if accepted:
                    current, final = candidate, result
                    partitions = max(2, partitions - 1)
                    reduced = True
                    break
                if budget_exhausted:
                    break
            if reduced:
                continue
            if partitions >= len(current):
                break
            partitions = min(len(current), partitions * 2)
        # Independently check every dependency-closed single-action removal.
        # A sweep with any UNRESOLVED result cannot certify 1-minimality.
        while not budget_exhausted:
            unresolved, reduced = False, False
            for event in current:
                candidate = remove_closed(current, {event["id"]})
                result = oracle(candidate, "single-action verification")
                accepted = result["status"] == "FAIL" and result["signature"] == target
                history[-1]["accepted"] = accepted
                if accepted:
                    current, final, reduced = candidate, result, True
                    break
                unresolved |= result["status"] == "UNRESOLVED"
                if budget_exhausted:
                    break
            if reduced:
                continue
            one_minimal = not unresolved and not budget_exhausted
            break
    return {"status": initial["status"], "original_trace": original, "minimized_trace": copy.deepcopy(current),
            "signature": target, "original_count": len(original), "minimized_count": len(current),
            "evaluations": evaluations, "repeats": repeats, "max_evaluations": max_evaluations,
            "one_minimal": one_minimal, "budget_exhausted": budget_exhausted, "history": history,
            "initial_inspection": initial, "final_inspection": final,
            "minimality_scope": "single-action removal plus dependent-action closure; not globally shortest",
            "scope": "synthetic SQLite reference app"}


def repair_scenarios() -> list[tuple[str, list[dict]]]:
    """Held-out specification examples constructed independently of DEFAULT_TRACE."""
    first = {"id": "s", "op": "send", "message_id": "seed", "sender": "Ada", "recipient": "Bo", "text": "Budget \u20b9 50\nReviewed"}
    forward = {"id": "f", "op": "forward", "message_id": "relay", "source_id": "seed", "sender": "Bo", "recipient": "Cy"}
    edit = {"id": "e", "op": "edit", "message_id": "relay", "text": "Changed \u2713"}
    delete = {"id": "d", "op": "delete", "message_id": "relay"}
    return [
        ("empty", []),
        ("unicode-send", [first]),
        ("ordinary-tombstone", [first, {"id": "e", "op": "edit", "message_id": "seed", "text": ""}, {"id": "d", "op": "delete", "message_id": "seed"}]),
        ("unedited-forward-tombstone", [first, forward, delete]),
        ("live-edited-forward", [first, forward, edit]),
        ("edited-forward-tombstone", [first, forward, edit, delete]),
        ("source-edited-before-forward", [first, {"id": "se", "op": "edit", "message_id": "seed", "text": "Revised before forwarding"}, forward, edit, delete]),
        ("reactions-preserved-in-tombstone", [first, forward, {"id": "r", "op": "react", "message_id": "relay", "sender": "Cy", "text": "\U0001f44d"}, edit, delete]),
        ("multi-hop-forward", [first, forward, {"id": "f2", "op": "forward", "message_id": "relay-two", "source_id": "relay", "sender": "Cy", "recipient": "Dee"}, {"id": "e2", "op": "edit", "message_id": "relay-two", "text": "Third revision"}, {"id": "d2", "op": "delete", "message_id": "relay-two"}]),
    ]


def validate_repair(parser: Parser = "fixed") -> dict:
    """Check all canonical semantic fields on 27 held-out schema/scenario cases."""
    _parser(parser)
    results = []
    for version in VERSIONS:
        for name, trace in repair_scenarios():
            candidate = inspect_trace(trace, version, parser)
            baseline = inspect_trace(trace, version, "buggy")
            results.append({"scenario": name, "version": version, "status": candidate["status"],
                            "signature": candidate["signature"], "findings": candidate["findings"],
                            "error": candidate.get("error"), "buggy_status": baseline["status"],
                            "buggy_signature": baseline["signature"]})
    summary = {"total": len(results), "passed": sum(r["status"] == "PASS" for r in results),
               "failed": sum(r["status"] == "FAIL" for r in results),
               "unresolved": sum(r["status"] == "UNRESOLVED" for r in results),
               "buggy_failures": sum(r["buggy_status"] == "FAIL" for r in results)}
    return {"status": "PASS" if summary["passed"] == summary["total"] else "FAIL", "summary": summary,
            "results": results, "parser": parser if isinstance(parser, str) else getattr(parser, "__name__", "custom"),
            "scope": "held-out synthetic reference-app cases; not proof for arbitrary applications or traces"}
