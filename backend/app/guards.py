"""Cheap, deterministic checks on the raw user message, run before any LLM call.

These don't replace the LLM's scope classification; they are a backstop that holds even
if a cleverly worded message fools the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config

# A pasted write/DDL statement. Matches SQL syntax, not ordinary English like "remove duplicates".
DESTRUCTIVE_SQL = re.compile(
    r"\b(DELETE\s+FROM|UPDATE\s+[\w\"`\[\]]+\s+SET|INSERT\s+(OR\s+\w+\s+)?INTO|REPLACE\s+INTO|"
    r"DROP\s+(TABLE|DATABASE|SCHEMA|INDEX|VIEW|TRIGGER)|ALTER\s+TABLE|TRUNCATE(\s+TABLE)?\s+[\w\"`\[\]]+|"
    r"CREATE\s+(TABLE|VIEW|TRIGGER)|ATTACH\s+DATABASE|DETACH\s+DATABASE|GRANT\s+|REVOKE\s+)",
    re.IGNORECASE,
)

# Common prompt-injection phrasings. Kept specific to avoid flagging normal questions.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier|your)\s+(instructions|rules|prompts?)",
    r"disregard\s+(all\s+|the\s+)?(previous|prior|above|your)\s+(instructions|rules)",
    r"(reveal|show|print|repeat|output)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions)",
    r"you\s+are\s+now\s+(a|an|in)\b",
    r"\b(developer|jailbreak|dan)\s+mode\b",
    r"pretend\s+(you\s+are|to\s+be)\s+(not|no\s+longer)",
    r"new\s+instructions\s*:",
]
INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class GuardResult:
    blocked: bool
    reason: str = ""          # "too_long" | "empty" | "destructive" | "prompt_injection"
    message: str = ""


REFUSAL_MESSAGES = {
    "out_of_scope": ("I'm designed to assist only with SQL and database-related tasks. "
                     "Please ask a question related to the provided database schema."),
    "destructive": ("I can only help with read-only queries, so I can't create SQL that deletes, updates, "
                    "inserts, drops, alters or truncates data. I can help you write a SELECT query instead, "
                    "for example to preview exactly which rows such a change would affect."),
    "prompt_injection": ("I can't change my instructions. I'm here to help with SQL and questions about "
                         "the provided database. What would you like to look up?"),
    "too_long": f"That message is too long. Please keep it under {config.MAX_INPUT_CHARS} characters.",
    "empty": "Please type a question about the database, or paste a SQL query.",
}


def check_input(text: str) -> GuardResult:
    text = text or ""
    if not text.strip():
        return GuardResult(True, "empty", REFUSAL_MESSAGES["empty"])
    if len(text) > config.MAX_INPUT_CHARS:
        return GuardResult(True, "too_long", REFUSAL_MESSAGES["too_long"])
    if INJECTION_RE.search(text):
        return GuardResult(True, "prompt_injection", REFUSAL_MESSAGES["prompt_injection"])
    if DESTRUCTIVE_SQL.search(text):
        return GuardResult(True, "destructive", REFUSAL_MESSAGES["destructive"])
    return GuardResult(False)
