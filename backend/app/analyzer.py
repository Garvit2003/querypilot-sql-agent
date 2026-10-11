"""Deterministic performance analysis of a validated SELECT.

Uses SQLite's own query plan plus the syntax tree, so every suggestion points at something
real in this query and this database. The LLM is not asked to guess at performance.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from . import db
from .validator import DIALECT, _alias_map, _schema_mapping

LARGE_TABLE_ROWS = 100


def _filter_columns(select: exp.Select, aliases: dict[str, str]) -> dict[str, list[str]]:
    """Columns used in WHERE comparisons and JOIN conditions, grouped by real table name."""
    found: dict[str, list[str]] = {}
    exprs = []
    if select.args.get("where"):
        exprs.append(select.args["where"])
    exprs += [j.args["on"] for j in select.args.get("joins") or [] if j.args.get("on") is not None]
    for e in exprs:
        for col in e.find_all(exp.Column):
            like = col.find_ancestor(exp.Like)
            if like is not None and isinstance(like.expression, exp.Literal) and str(like.expression.this).startswith("%"):
                continue  # an index can't help a leading-wildcard LIKE, so don't recommend one
            table = aliases.get((col.table or "").lower())
            if table and col.name not in found.setdefault(table, []):
                found[table].append(col.name)
    return found


def estimate_cost(sql: str, schema: db.Schema | None = None) -> dict:
    """Rough cost estimate from SQLite's query plan: rows read by full scans vs index lookups."""
    schema = schema or db.get_schema()
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
        aliases = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    except Exception:
        aliases = {}
    plan = db.query_plan(sql)
    rows, scans, lookups = 0, [], []
    for step in plan:
        m = re.match(r"(SCAN|SEARCH) (\w+)", step)
        if not m:
            continue
        t = schema.table(aliases.get(m.group(2).lower(), m.group(2)))
        if not t:
            continue
        if m.group(1) == "SCAN" and "COVERING INDEX" not in step:
            rows += t.row_count
            scans.append(t.name)
        else:
            rows += max(1, t.row_count // 20)  # index lookup: assume a small slice of the table
            lookups.append(t.name)
    level = "Low" if rows < 1_000 else "Medium" if rows < 10_000 else "High"
    return {"level": level, "estimated_rows_read": rows, "full_scans": scans, "index_lookups": lookups,
            "plan": plan}


def analyze(sql: str, schema: db.Schema | None = None) -> list[dict]:
    """Return a list of {kind, message, suggestion} performance notes. Never raises."""
    schema = schema or db.get_schema()
    notes: list[dict] = []
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT)
        qualified = qualify(tree.copy(), schema=_schema_mapping(schema), dialect=DIALECT, identify=False)
    except Exception:
        return notes

    # SELECT *
    if any(isinstance(s, exp.Star) for sel in tree.find_all(exp.Select) for s in sel.expressions):
        notes.append({"kind": "readability",
                      "message": "SELECT * returns every column. Listing only the columns you need is faster "
                                 "to transfer and won't break if the table gains columns.",
                      "suggestion": None})

    plan = db.query_plan(sql)
    scanned = set()
    for step in plan:
        m = re.match(r"SCAN (\w+)", step)
        if m and "USING" not in step:
            scanned.add(m.group(1).lower())

    for select in qualified.find_all(exp.Select):
        aliases = _alias_map(select)
        # Plan lines name aliases; map them back to real tables
        scanned_tables = {aliases.get(s, s) for s in scanned}
        for table, cols in _filter_columns(select, aliases).items():
            t = schema.table(table)
            if not t or t.row_count < LARGE_TABLE_ROWS or table not in scanned_tables:
                continue
            leading = {ix[0].lower() for ix in t.indexed_columns}
            real_name = {c.name.lower(): c.name for c in t.columns}  # restore original casing
            missing = [real_name.get(c.lower(), c) for c in cols if c.lower() not in leading]
            if missing:
                col_list = ", ".join(missing[:2])
                idx_name = f"idx_{t.name.lower()}_{'_'.join(c.lower() for c in missing[:2])}"
                notes.append({"kind": "index",
                              "message": f"{t.name} ({t.row_count:,} rows) is fully scanned to filter on "
                                         f"{col_list}, {'which has' if len(missing) == 1 else 'which have'} "
                                         "no index. On this demo data the gain is small; on a production-size "
                                         "table it avoids reading every row.",
                              "suggestion": f"CREATE INDEX {idx_name} ON {t.name}({col_list});"})

    # Functions wrapped around filtered columns stop SQLite using an index on them.
    # Checked on the original tree so messages show the SQL as the user wrote it.
    for select in tree.find_all(exp.Select):
        where = select.args.get("where")
        if where is not None:
            for func in where.find_all(exp.Func):
                if isinstance(func, (exp.Connector, exp.Not, exp.Binary, exp.Paren)):
                    continue
                if func.find_ancestor(exp.Subquery) not in (None, select.find_ancestor(exp.Subquery)):
                    continue
                cols = [c.name for c in func.find_all(exp.Column)]
                if cols and not isinstance(func, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
                    notes.append({"kind": "sargability",
                                  "message": f"The filter wraps {cols[0]} in a function ({func.sql(DIALECT)}), "
                                             "which prevents an index on that column from being used. "
                                             "Compare the raw column against a range instead where possible.",
                                  "suggestion": None})
                    break
            for like in where.find_all(exp.Like):
                pattern = like.expression
                if isinstance(pattern, exp.Literal) and str(pattern.this).startswith("%"):
                    notes.append({"kind": "sargability",
                                  "message": "A LIKE pattern starting with % can't use an index and scans "
                                             "every row.",
                                  "suggestion": None})

    for select in qualified.find_all(exp.Select):
        # Joined tables that contribute nothing (only appear in their own join condition)
        for join in select.args.get("joins") or []:
            if not isinstance(join.this, exp.Table):
                continue
            alias = join.this.alias_or_name.lower()
            on = join.args.get("on")
            on_cols = set(id(c) for c in on.find_all(exp.Column)) if on is not None else set()
            used_elsewhere = any(
                (c.table or "").lower() == alias and id(c) not in on_cols for c in select.find_all(exp.Column)
            )
            if not used_elsewhere and not any(isinstance(s, exp.Star) for s in select.expressions):
                real = schema.table(join.this.name)
                if real is not None:
                    join.this.set("this", exp.to_identifier(real.name))
                side = (join.args.get("side") or "").upper()
                if side == "LEFT":
                    msg = (f"The LEFT JOIN to {join.this.name} isn't used in the output or filters and "
                           "can be removed without changing the result.")
                else:
                    msg = (f"The join to {join.this.name} isn't used in the output or filters. It only "
                           "matters if it's meant to exclude rows without a match; otherwise remove it.")
                notes.append({"kind": "unnecessary_join", "message": msg, "suggestion": None})

    # De-duplicate while keeping order
    seen, unique = set(), []
    for n in notes:
        key = (n["kind"], n["message"])
        if key not in seen:
            seen.add(key)
            unique.append(n)
    return unique
