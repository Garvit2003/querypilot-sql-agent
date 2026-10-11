"""User-supplied databases ("bring your own database").

A reviewer can upload either
  - a .sql script (CREATE TABLE statements, optionally INSERTs), which is executed into a brand-new,
    isolated SQLite file, or
  - a SQLite database file (.db / .sqlite).

Each upload gets an id. Conversations pass that id, and every part of the agent (schema, validator,
analyzer, execution) then works against that database. The agent still only ever reads it: queries run
on a read-only connection exactly as for the sample database.

Uploads live on local disk and are cleaned up after a day; on a free host they also disappear on restart.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, db

UPLOAD_DIR = config.DB_PATH.parent / "uploads"
MAX_SQL_BYTES = 2_000_000          # 2 MB of SQL text
MAX_SQLITE_BYTES = 20_000_000      # 20 MB database file
IMPORT_TIMEOUT_SECONDS = 15
MAX_AGE_SECONDS = 24 * 3600
MAX_UPLOADS = 100
ID_RE = re.compile(r"^db_[a-f0-9]{16}$")
SQLITE_HEADER = b"SQLite format 3\x00"


class UploadError(ValueError):
    """A problem with the uploaded database that the user can fix; the message is shown to them."""


@dataclass
class DatabaseInfo:
    id: str
    name: str
    kind: str          # "sample" | "sql" | "sqlite"
    tables: int
    created_at: float


SAMPLE = "sample"


def path_for(db_id: str | None) -> Path:
    """Resolve a database id to a file. None or 'sample' means the built-in sample database."""
    if not db_id or db_id == SAMPLE:
        return config.DB_PATH
    if not ID_RE.match(db_id):
        raise UploadError("Unknown database id.")
    path = UPLOAD_DIR / f"{db_id}.db"
    if not path.exists():
        raise UploadError("That database is no longer available (uploads expire after a day or when the "
                          "server restarts). Please upload it again.")
    return path


def info(db_id: str | None) -> DatabaseInfo:
    if not db_id or db_id == SAMPLE:
        return DatabaseInfo(SAMPLE, "Sample company database", SAMPLE, len(db.get_schema().tables), 0)
    path_for(db_id)
    meta = UPLOAD_DIR / f"{db_id}.json"
    return DatabaseInfo(**json.loads(meta.read_text()))


def _new_id() -> str:
    return "db_" + secrets.token_hex(8)


def _cleanup() -> None:
    """Delete expired uploads and keep the total bounded."""
    if not UPLOAD_DIR.exists():
        return
    files = sorted(UPLOAD_DIR.glob("db_*.db"), key=lambda p: p.stat().st_mtime)
    now = time.time()
    for i, f in enumerate(files):
        if now - f.stat().st_mtime > MAX_AGE_SECONDS or len(files) - i > MAX_UPLOADS:
            f.unlink(missing_ok=True)
            f.with_suffix(".json").unlink(missing_ok=True)


def _deny_attach(action, *_args):
    # Block ATTACH/DETACH so an uploaded script can't reach other files on the server.
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _finalise(db_id: str, path: Path, name: str, kind: str) -> DatabaseInfo:
    """Check the new database is usable, then record its metadata."""
    try:
        conn = db.get_readonly_connection(path)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise UploadError("The database file appears to be corrupted.")
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        path.unlink(missing_ok=True)
        raise UploadError(f"That file isn't a readable SQLite database ({e}).") from e
    if tables == 0:
        path.unlink(missing_ok=True)
        raise UploadError("No tables were found. Include CREATE TABLE statements (or upload a database "
                          "file that contains tables).")
    meta = DatabaseInfo(db_id, (name or "Uploaded database")[:80], kind, tables, time.time())
    path.with_suffix(".json").write_text(json.dumps(asdict(meta)))
    db.get_schema(path)  # warm the schema cache and surface any introspection problem now
    return meta


def create_from_sql(script: str, name: str = "") -> DatabaseInfo:
    """Execute a user's SQL script into a fresh, isolated SQLite file."""
    if not script or not script.strip():
        raise UploadError("The SQL file is empty.")
    if len(script.encode()) > MAX_SQL_BYTES:
        raise UploadError(f"The SQL file is too large (limit {MAX_SQL_BYTES // 1_000_000} MB).")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    db_id = _new_id()
    path = UPLOAD_DIR / f"{db_id}.db"
    conn = sqlite3.connect(path)
    deadline = time.monotonic() + IMPORT_TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    conn.set_authorizer(_deny_attach)
    try:
        conn.executescript(script)
        conn.commit()
    except sqlite3.Error as e:
        conn.close()
        path.unlink(missing_ok=True)
        msg = str(e)
        if "interrupted" in msg.lower():
            msg = f"the script took longer than {IMPORT_TIMEOUT_SECONDS}s to run"
        raise UploadError(f"The SQL script couldn't be loaded into SQLite: {msg}. "
                          "Scripts written for MySQL or PostgreSQL may need small changes for SQLite.") from e
    conn.close()
    return _finalise(db_id, path, name, "sql")


def create_from_sqlite(data: bytes, name: str = "") -> DatabaseInfo:
    """Store an uploaded SQLite database file."""
    if len(data) > MAX_SQLITE_BYTES:
        raise UploadError(f"The database file is too large (limit {MAX_SQLITE_BYTES // 1_000_000} MB).")
    if not data.startswith(SQLITE_HEADER):
        raise UploadError("That file isn't a SQLite database. Upload a .db/.sqlite file, or a .sql script.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    db_id = _new_id()
    path = UPLOAD_DIR / f"{db_id}.db"
    path.write_bytes(data)
    return _finalise(db_id, path, name, "sqlite")


def example_questions(schema: db.Schema) -> list[str]:
    """Simple starter questions for an uploaded schema, built from its own tables."""
    tables = sorted(schema.tables.values(), key=lambda t: -t.row_count)
    qs = []
    if tables:
        qs.append(f"Show 10 rows from {tables[0].name}")
        qs.append(f"How many rows are in {tables[0].name}?")
    for t in tables:
        if t.foreign_keys:
            qs.append(f"Join {t.name} with {t.foreign_keys[0].ref_table} and show 10 rows")
            break
    for t in tables:
        cat = next((c for c in t.columns if c.sample_values), None)
        if cat:
            qs.append(f"Count {t.name} by {cat.name}")
            break
    qs.append("What tables and columns are in this database?")
    return qs[:5]
