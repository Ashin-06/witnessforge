"""Runnable trusted-plugin example for the reference SQLite app.

This adapter delegates to the bundled parser. Replace that call with your own
read-only extraction logic to evaluate an independently written parser.
"""

from pathlib import Path
from witnessforge.core import parse_fixed


def parse(database_path: Path) -> list[dict]:
    return parse_fixed(database_path)
