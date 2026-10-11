"""Runtime settings, read from environment variables so nothing secret lives in code."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Database
DB_PATH = Path(os.getenv("QP_DB_PATH", BASE_DIR.parent / "data" / "querypilot.db"))
SCHEMA_SQL_PATH = BASE_DIR / "schema.sql"

# LLM
LLM_PROVIDER = os.getenv("QP_LLM_PROVIDER", "anthropic")
# Stronger model writes SQL; faster model classifies intent and writes explanations.
LLM_MODEL = os.getenv("QP_LLM_MODEL", "claude-sonnet-5-5")
LLM_FAST_MODEL = os.getenv("QP_LLM_FAST_MODEL", "claude-haiku-5-5")
LLM_TIMEOUT_SECONDS = float(os.getenv("QP_LLM_TIMEOUT", "45"))

# Agent behaviour
MAX_SQL_ATTEMPTS = int(os.getenv("QP_MAX_SQL_ATTEMPTS", "3"))  # first try + 2 repairs
RESULT_ROW_LIMIT = int(os.getenv("QP_RESULT_ROW_LIMIT", "200"))
QUERY_TIMEOUT_SECONDS = float(os.getenv("QP_QUERY_TIMEOUT", "5"))
HISTORY_TURNS_IN_PROMPT = int(os.getenv("QP_HISTORY_TURNS", "6"))
MAX_INPUT_CHARS = int(os.getenv("QP_MAX_INPUT_CHARS", "4000"))

# Abuse protection for the public deployment
RATE_LIMIT_REQUESTS = int(os.getenv("QP_RATE_LIMIT_REQUESTS", "120"))  # generous: reviewers may share one office IP
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("QP_RATE_LIMIT_WINDOW", "600"))

# Frontend build served by FastAPI in production
FRONTEND_DIST = Path(os.getenv("QP_FRONTEND_DIST", BASE_DIR.parent.parent / "frontend" / "dist"))
