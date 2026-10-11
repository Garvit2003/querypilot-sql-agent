"""Regenerate docs/prompts.md and docs/schema.md from the code, so docs never drift.

Usage (from backend/):  python -m scripts.gen_docs
"""

from pathlib import Path

from app import db, prompts

DOCS = Path(__file__).resolve().parents[2] / "docs"

PROMPTS = [
    ("CLASSIFY_SYSTEM", "Intent classification — system (fast model)"), ("CLASSIFY_USER", "Intent classification — user"),
    ("GENERATE_SYSTEM", "SQL generation — system (strong model)"), ("TASK_GENERATE", "Task: generate"),
    ("TASK_MODIFY", "Task: modify a previous query (follow-ups)"), ("TASK_DEBUG", "Task: debug user SQL"),
    ("TASK_OPTIMIZE", "Task: optimize user SQL"), ("TASK_REPAIR", "Task: repair after failed validation"),
    ("EXPLAIN_SYSTEM", "Explanation — system (fast model)"), ("EXPLAIN_USER", "Explanation — user"),
    ("SCHEMA_INFO_SYSTEM", "Schema and SQL-concept questions — system (fast model)"),
    ("SCHEMA_INFO_USER", "Schema and SQL-concept questions — user"), ("INJECTION_RULE", "Shared injection rule"),
]

PRINCIPLES = """## Design principles

1. **Prompts ask; code enforces.** Every prompt asks for read-only SQL from the listed schema, but the validator and the read-only connection enforce it regardless of what the model returns.
2. **User text is data.** It is wrapped in `<user_message>` tags that the prompts declare untrusted (prompt-injection hygiene).
3. **Structured outputs.** Each call returns a typed object (`IntentResult`, `SQLDraft`, `Explanation`, `SchemaAnswer` in `llm.py`), so routing never depends on parsing prose.
4. **One generation prompt, five task blocks.** Generate, modify, debug, optimize and repair share the same rules and schema; only the task changes. This keeps behaviour consistent across modes.
5. **Assumptions are surfaced.** The SQL prompt must list every interpretation choice, which the UI shows to the user.
6. **Honest "can't answer".** If the schema lacks the data, the model returns no SQL and explains what's missing, rather than inventing a column.
7. **Rules added from evaluation findings:** month-phrase date handling ("after January 2024" = from 1 January), superlatives keeping ties ("which rep has the most…" returns all tied winners), and a `sql_concept` route so general SQL questions are answered rather than treated as debugging requests.

## Structured output schemas

| Schema | Fields |
| --- | --- |
| `IntentResult` | `intent`, `user_sql`, `clarification_question`, `reason` |
| `SQLDraft` | `sql`, `assumptions[]`, `notes[]`, `clarification_question` |
| `Explanation` | `summary`, `steps[]`, `result_headline` |
| `SchemaAnswer` | `answer` |
"""


def prompts_md() -> str:
    out = ["# Prompts", "", "Generated from [`backend/app/prompts.py`](../backend/app/prompts.py) by "
           "`python -m scripts.gen_docs`. `{placeholders}` are filled at runtime.", "", PRINCIPLES]
    for name, title in PROMPTS:
        out += [f"## {title}", "", f"`prompts.{name}`", "", "```text", getattr(prompts, name).strip(), "```", ""]
    out += ["## Schema text injected as `{schema}` (sample database)", "",
            "Rendered live from the database by `db.schema_for_prompt()`:", "", "```text", db.schema_for_prompt(), "```", ""]
    return "\n".join(out)


def schema_md() -> str:
    s = db.get_schema()
    lines = ["# Sample database schema", "",
             "SQLite. Definition: [`backend/app/schema.sql`](../backend/app/schema.sql). Data: "
             "[`backend/app/seed.py`](../backend/app/seed.py) (fixed seed, identical on every build). "
             "To test your own schema instead, upload it in the app (see the README).", "",
             "```mermaid", "erDiagram",
             "    Departments ||--o{ Employees : employs", "    Employees ||--o{ Employees : manages",
             "    Employees ||--o{ Orders : \"handles (sales rep)\"", "    Customers ||--o{ Orders : places",
             "    Orders ||--|{ OrderItems : contains", "    Products ||--o{ OrderItems : \"appears in\"", "```", ""]
    for t in s.tables.values():
        lines += [f"## {t.name} ({t.row_count:,} rows)", "", s.notes.get(t.name, ""), "",
                  "| Column | Type | Key | Values |", "| --- | --- | --- | --- |"]
        fks = {f.column: f"FK → {f.ref_table}.{f.ref_column}" for f in t.foreign_keys}
        for c in t.columns:
            key = "PK" if c.primary_key else fks.get(c.name, "")
            lines.append(f"| {c.name} | {c.type} | {key} | {', '.join(c.sample_values)} |")
        idx = [", ".join(i) for i in t.indexed_columns
               if not (len(i) == 1 and any(c.primary_key and c.name == i[0] for c in t.columns))]
        if idx:
            lines += ["", "Indexed: " + "; ".join(idx)]
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    (DOCS / "prompts.md").write_text(prompts_md())
    (DOCS / "schema.md").write_text(schema_md())
    print("Wrote docs/prompts.md and docs/schema.md")
