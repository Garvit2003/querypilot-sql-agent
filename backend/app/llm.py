"""LLM access behind a tiny interface, plus the structured output schemas.

The graph only ever calls `client.structured(model_cls, system, user, fast=...)`. That keeps the
provider swappable (one setting) and lets tests inject a fake client with no network calls.
"""

from __future__ import annotations

from typing import Literal, Optional, Protocol, TypeVar

from pydantic import BaseModel, Field

from . import config

T = TypeVar("T", bound=BaseModel)


# ----------------------------- structured outputs -----------------------------

class IntentResult(BaseModel):
    intent: Literal["generate", "modify", "optimize", "debug", "explain", "schema_info",
                    "destructive", "ambiguous", "out_of_scope"]
    user_sql: Optional[str] = Field(default=None, description="SQL pasted by the user, copied exactly, if any")
    clarification_question: Optional[str] = Field(
        default=None, description="Only for intent=ambiguous: one short question to ask the user")
    reason: str = Field(description="One short sentence explaining the classification")


class SQLDraft(BaseModel):
    sql: str = Field(description="A single SQLite SELECT query, or empty if the request can't be answered")
    assumptions: list[str] = Field(default_factory=list, description="Interpretation choices made, one per item")
    notes: list[str] = Field(default_factory=list,
                             description="For debug: problems found. For optimize: changes made. Else empty")
    clarification_question: Optional[str] = Field(
        default=None, description="If the schema can't answer the request: what's missing and what you can offer")


class Explanation(BaseModel):
    summary: str
    steps: list[str] = Field(default_factory=list)
    result_headline: str = ""


class SchemaAnswer(BaseModel):
    answer: str


# ----------------------------- client interface -----------------------------

class LLMClient(Protocol):
    def structured(self, model_cls: type[T], system: str, user: str, fast: bool = False) -> T: ...


class LangChainClient:
    """Production client built on LangChain chat models (Anthropic by default)."""

    def __init__(self):
        self._models = {}

    def _model(self, fast: bool):
        key = "fast" if fast else "main"
        if key not in self._models:
            name = config.LLM_FAST_MODEL if fast else config.LLM_MODEL
            if config.LLM_PROVIDER == "anthropic":
                from langchain_anthropic import ChatAnthropic
                # No temperature: current Claude models reject it. Consistency comes from structured
                # output schemas, strict prompts and the deterministic validator instead.
                self._models[key] = ChatAnthropic(model=name, max_tokens=1500,
                                                  timeout=config.LLM_TIMEOUT_SECONDS, max_retries=2)
            else:
                from langchain.chat_models import init_chat_model  # optional dependency for other providers
                self._models[key] = init_chat_model(name, model_provider=config.LLM_PROVIDER)
        return self._models[key]

    def structured(self, model_cls, system, user, fast=False):
        kwargs = {"method": "json_schema"} if config.LLM_PROVIDER == "anthropic" else {}
        runnable = self._model(fast).with_structured_output(model_cls, **kwargs)
        return runnable.invoke([("system", system), ("human", user)])


_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LangChainClient()
    return _client


def set_client(client: LLMClient) -> None:
    """Used by tests to inject a fake client."""
    global _client
    _client = client
