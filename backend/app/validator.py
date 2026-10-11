"""Deterministic SQL validation. No LLM is involved in deciding whether SQL is safe or valid.

Checks, in order (each one is a named item the UI shows as a checklist):
  1. Parses           - sqlglot can parse it as SQLite SQL
  2. Single statement - exactly one statement (blocks "SELECT 1; DROP TABLE x")
  3. Read-only        - the statement is a SELECT (or UNION/INTERSECT/EXCEPT of SELECTs) and
                        contains no write/DDL/admin node anywhere in its syntax tree
  4. Tables exist     - every referenced table is in the schema (CTE names excluded)
  5. Columns exist    - every column resolves to exactly one table in scope
  6. Joins valid      - join conditions compare real columns; non-foreign-key joins and
                        joins with no condition are flagged
  7. Compiles         - SQLite itself compiles the query (EXPLAIN), catching anything left
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from . import db

DIALECT = "sqlite"

READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Any of these anywhere in the tree makes the query non-read-only.
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Drop", "Alter", "Create", "TruncateTable", "Merge",
        "Command", "Pragma", "Attach", "Detach", "Transaction", "Commit", "Rollback", "Set",
        "Use", "Grant", "Revoke", "Copy", "Analyze", "LoadData",
    ) if hasattr(exp, name)
)
DESTRUCTIVE_KEYWORDS = ("DELETE", "UPDATE", "INSERT", "DROP", "ALTER", "TRUNCATE")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationResult:
    valid: bool
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    formatted_sql: str = ""
    destructive: bool = False

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "destructive": self.destructive,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": [c.__dict__ for c in self.checks],
        }


def _schema_mapping(schema: db.Schema) -> dict:
    return {t.name: {c.name: c.type for c in t.columns} for t in schema.tables.values()}


def format_sql(sql: str) -> str:
    """Pretty-print SQL for readability; falls back to the input if it can't be parsed."""
    try:
        return sqlglot.transpile(sql, read=DIALECT, write=DIALECT, pretty=True)[0]
    except Exception:
        return sql.strip()


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _alias_map(select: exp.Select) -> dict[str, str]:
    """Map alias (or bare name) -> real table name for tables directly in this SELECT's FROM/JOINs."""
    mapping = {}
    sources = []
    from_clause = select.args.get("from_") or select.args.get("from")  # key name differs across sqlglot versions
    if from_clause is not None:
        sources.append(from_clause.this)
    sources += [j.this for j in select.args.get("joins") or []]
    for src in sources:
        if isinstance(src, exp.Table):
            mapping[src.alias_or_name.lower()] = src.name.lower()
    return mapping


def validate_sql(sql: str, schema: db.Schema | None = None, db_path=None) -> ValidationResult:
    schema = schema or db.get_schema(db_path)
    result = ValidationResult(valid=False)
    sql = (sql or "").strip().rstrip(";").strip()

    def fail(name: str, detail: str) -> ValidationResult:
        result.checks.append(Check(name, False, detail))
        result.errors.append(detail)
        result.formatted_sql = format_sql(sql)
        return result

    if not sql:
        return fail("Parses", "No SQL was provided.")

    # 1. Parse
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as e:
        upper = sql.upper()
        result.destructive = any(upper.lstrip().startswith(k) for k in DESTRUCTIVE_KEYWORDS)
        return fail("Parses", f"SQL syntax error: {str(e).splitlines()[0]}")
    result.checks.append(Check("Parses", True))

    # 2. Single statement
    if len(statements) != 1:
        result.destructive = any(isinstance(s, FORBIDDEN_NODES) or s.find(*FORBIDDEN_NODES) for s in statements)
        return fail("Single statement", f"Expected one SQL statement, found {len(statements)}. "
                                        "Multiple statements are not allowed.")
    tree = statements[0]
    result.checks.append(Check("Single statement", True))

    # 3. Read-only
    forbidden = tree if isinstance(tree, FORBIDDEN_NODES) else tree.find(*FORBIDDEN_NODES)
    if forbidden is not None:
        result.destructive = True
        kind = type(forbidden).__name__.upper()
        return fail("Read-only", f"Only read-only SELECT queries are allowed; this contains a {kind} operation.")
    if not isinstance(tree, READ_ONLY_ROOTS):
        return fail("Read-only", "This is not a valid SELECT query.")
    result.checks.append(Check("Read-only", True))

    # 4. Tables exist
    ctes = _cte_names(tree)
    unknown = sorted({t.name for t in tree.find_all(exp.Table)
                      if t.name and t.name.lower() not in ctes and not schema.table(t.name)})
    if unknown:
        available = ", ".join(t.name for t in schema.tables.values())
        return fail("Tables exist", f"Unknown table(s): {', '.join(unknown)}. Available tables: {available}.")
    result.checks.append(Check("Tables exist", True))

    # 5. Columns exist (sqlglot resolves every column against the schema, through aliases, CTEs, subqueries)
    try:
        qualified = qualify(tree.copy(), schema=_schema_mapping(schema), dialect=DIALECT,
                            validate_qualify_columns=True, identify=False)
    except OptimizeError as e:
        msg = str(e).split(" Line:")[0]
        col = msg.split("'")[1] if "'" in msg else ""
        if col:  # sqlglot lower-cases identifiers; show the column as the query wrote it
            m = re.search(rf"\b({re.escape(col)})\b", sql, re.IGNORECASE)
            col = m.group(1) if m else col
        owners = [t.name for t in schema.tables.values() if col and col.lower() in t.column_names()]
        tables_in_query = {t.name.lower() for t in tree.find_all(exp.Table)}
        in_scope = [o for o in owners if o.lower() in tables_in_query]
        if len(in_scope) > 1:
            detail = (f"Column '{col}' is ambiguous: it exists in {', '.join(in_scope)}. "
                      f"Prefix it with a table name or alias.")
        elif owners:
            detail = f"Column '{col}' is not in the tables used by this query; it exists in {', '.join(owners)}."
        else:
            detail = f"Unknown column '{col}': no table in the schema has it." if col else msg
        return fail("Columns exist", detail)
    result.checks.append(Check("Columns exist", True))

    # 6. Joins valid
    join_warnings = []
    for select in qualified.find_all(exp.Select):
        aliases = _alias_map(select)
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            using = join.args.get("using")
            kind = (join.args.get("kind") or "").upper()
            if isinstance(on, exp.Boolean) and on.this is True:
                on = None  # sqlglot fills a missing condition with ON TRUE
            if on is None and not using:
                if kind != "CROSS" and isinstance(join.this, exp.Table):
                    join_warnings.append(f"Join to {join.this.name} has no join condition, which produces "
                                         "every combination of rows (a cartesian product).")
                continue
            for eq in (on.find_all(exp.EQ) if on is not None else []):
                left, right = eq.this, eq.expression
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                lt, rt = aliases.get(left.table.lower()), aliases.get(right.table.lower())
                if not lt or not rt or not schema.table(lt) or not schema.table(rt):
                    continue  # one side is a CTE or subquery; nothing to check against declared keys
                if not schema.has_fk_between(lt, left.name, rt, right.name):
                    lt_name, rt_name = schema.table(lt).name, schema.table(rt).name
                    join_warnings.append(
                        f"Join condition {lt_name}.{left.name} = {rt_name}.{right.name} does not follow a declared "
                        "foreign key; check that this relationship is intended.")
    result.warnings.extend(join_warnings)
    result.checks.append(Check("Joins valid", True,
                               "; ".join(join_warnings) if join_warnings else "All joins follow declared keys."))

    # 7. Compiles in SQLite
    err = db.explain_check(sql, db_path)
    if err:
        return fail("Compiles", f"SQLite rejected the query: {err}")
    result.checks.append(Check("Compiles", True))

    result.valid = True
    result.formatted_sql = format_sql(sql)
    return result
