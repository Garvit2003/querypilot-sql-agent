"""Database access: read-only connections and schema introspection.

The schema the agent sees is read from the live database, not hard-coded. Swap in a
different SQLite file and the agent, the validator and the prompts all follow it.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config

# Business meaning the raw schema can't express. This is a tiny "semantic layer":
# it is what lets the model map "California" to State = 'California', or know that
# revenue should exclude cancelled orders.
SCHEMA_NOTES = {
    "Departments": "One row per department.",
    "Employees": "ManagerID points to another employee (self-join); department heads have ManagerID NULL. "
                 "HireDate is 'YYYY-MM-DD' text.",
    "Customers": "State holds the full US state name (e.g. 'California', not 'CA'). "
                 "SignupDate is 'YYYY-MM-DD' text.",
    "Products": "IsActive is 1 for products currently sold, 0 for discontinued.",
    "Orders": "EmployeeID is the sales rep who handled the order; NULL means a self-serve online order. "
              "OrderDate is 'YYYY-MM-DD' text. TotalAmount is the order value after discounts. "
              "For revenue, exclude Status 'Cancelled' and 'Returned' unless the user asks otherwise.",
    "OrderItems": "Line items of an order. Line value = Quantity * UnitPrice * (1 - Discount). "
                  "Discount is a fraction (0.1 = 10%).",
}

LOW_CARDINALITY_LIMIT = 12  # show distinct values for text columns with this many or fewer values


@dataclass
class Column:
    name: str
    type: str
    primary_key: bool
    not_null: bool
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey]
    indexed_columns: list[tuple[str, ...]]
    row_count: int

    def column_names(self) -> set[str]:
        return {c.name.lower() for c in self.columns}


@dataclass
class Schema:
    tables: dict[str, Table]  # keyed by lower-case table name

    def table(self, name: str) -> Table | None:
        return self.tables.get(name.lower())

    def has_fk_between(self, t1: str, c1: str, t2: str, c2: str) -> bool:
        """True if a declared foreign key links t1.c1 and t2.c2, in either direction."""
        for a, ca, b, cb in ((t1, c1, t2, c2), (t2, c2, t1, c1)):
            ta = self.table(a)
            if not ta:
                continue
            for fk in ta.foreign_keys:
                if (fk.column.lower() == ca.lower() and fk.ref_table.lower() == b.lower()
                        and fk.ref_column.lower() == cb.lower()):
                    return True
        return False

    def to_dict(self) -> dict:
        return {
            t.name: {
                "columns": [{"name": c.name, "type": c.type, "primary_key": c.primary_key,
                             "sample_values": c.sample_values} for c in t.columns],
                "foreign_keys": [{"column": f.column, "references": f"{f.ref_table}.{f.ref_column}"}
                                 for f in t.foreign_keys],
                "row_count": t.row_count,
                "notes": SCHEMA_NOTES.get(t.name, ""),
            }
            for t in self.tables.values()
        }


def ensure_database() -> None:
    if not config.DB_PATH.exists():
        from .seed import build_database
        build_database(config.DB_PATH)


def get_readonly_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """A connection that cannot write, enforced by SQLite itself, not by our code.

    mode=ro opens the file read-only at the OS level; PRAGMA query_only blocks any
    write statement even if one slipped past every earlier check.
    """
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


@lru_cache(maxsize=1)
def get_schema() -> Schema:
    ensure_database()
    conn = get_readonly_connection()
    try:
        tables: dict[str, Table] = {}
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
        for name in names:
            cols = []
            for _, cname, ctype, notnull, _, pk in conn.execute(f'PRAGMA table_info("{name}")'):
                cols.append(Column(cname, ctype or "TEXT", bool(pk), bool(notnull)))
            fks = [ForeignKey(row[3], row[2], row[4]) for row in conn.execute(f'PRAGMA foreign_key_list("{name}")')]
            indexed = []
            for idx in conn.execute(f'PRAGMA index_list("{name}")'):
                idx_cols = tuple(r[2] for r in conn.execute(f'PRAGMA index_info("{idx[1]}")'))
                if idx_cols:
                    indexed.append(idx_cols)
            indexed += [(c.name,) for c in cols if c.primary_key]
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for c in cols:
                if c.type.upper() == "TEXT" and not c.primary_key:
                    distinct = conn.execute(
                        f'SELECT COUNT(DISTINCT "{c.name}") FROM "{name}"').fetchone()[0]
                    if 0 < distinct <= LOW_CARDINALITY_LIMIT:
                        c.sample_values = [r[0] for r in conn.execute(
                            f'SELECT DISTINCT "{c.name}" FROM "{name}" ORDER BY 1')]
            tables[name.lower()] = Table(name, cols, fks, indexed, row_count)
        return Schema(tables)
    finally:
        conn.close()


def schema_for_prompt(schema: Schema | None = None) -> str:
    """Render the schema as compact text for the LLM: columns, keys, allowed values, notes."""
    schema = schema or get_schema()
    lines = []
    for t in schema.tables.values():
        lines.append(f"TABLE {t.name} ({t.row_count} rows)")
        for c in t.columns:
            extra = " PRIMARY KEY" if c.primary_key else ""
            if c.sample_values:
                extra += "  -- values: " + ", ".join(repr(v) for v in c.sample_values)
            lines.append(f"  {c.name} {c.type}{extra}")
        for fk in t.foreign_keys:
            lines.append(f"  FOREIGN KEY {fk.column} -> {fk.ref_table}.{fk.ref_column}")
        if t.name in SCHEMA_NOTES:
            lines.append(f"  NOTE: {SCHEMA_NOTES[t.name]}")
        lines.append("")
    return "\n".join(lines).strip()


class QueryTimeout(Exception):
    pass


def run_readonly_query(sql: str, limit: int | None = None, timeout: float | None = None) -> dict:
    """Execute a validated SELECT on a read-only connection with a row cap and a time limit."""
    limit = limit or config.RESULT_ROW_LIMIT
    timeout = timeout or config.QUERY_TIMEOUT_SECONDS
    conn = get_readonly_connection()
    deadline = time.monotonic() + timeout
    # SQLite calls this every N virtual-machine steps; returning non-zero aborts the query.
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        started = time.monotonic()
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeout(f"Query took longer than {timeout:.0f}s and was stopped.") from e
        raise
    finally:
        conn.close()


_explain_lock = threading.Lock()


def explain_check(sql: str) -> str | None:
    """Ask SQLite to compile (not run) the query. Returns an error message, or None if it compiles."""
    conn = get_readonly_connection()
    try:
        conn.execute(f"EXPLAIN {sql}")
        return None
    except sqlite3.Error as e:
        return str(e)
    finally:
        conn.close()


def query_plan(sql: str) -> list[str]:
    """SQLite's query plan, used to spot full-table scans for performance hints."""
    conn = get_readonly_connection()
    try:
        return [row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
