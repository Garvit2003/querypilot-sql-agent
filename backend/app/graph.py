"""The QueryPilot agent as a LangGraph state machine.

Flow (see docs/architecture.md for the diagram):

  input_guard ──blocked──────────────────────────────────────────────────────────► respond
      │
  classify_intent ──out_of_scope / destructive──► refuse ─────────────────────────► respond
      │           ──ambiguous─────────────────► clarify ──────────────────────────► respond
      │           ──schema_info───────────────► answer_schema ────────────────────► respond
      ▼
  load_schema ──generate / modify──────────────► generate_sql ◄──repair (≤2)──┐
      │                                               │                       │
      └──debug / optimize / explain──► diagnose ──────┤                       │
                                         (explain)    ▼                       │
                                        └──────► validate_sql ──invalid───────┘
                                                      │ ──still invalid──► fail ──► respond
                                                      ▼
                                              analyze_performance ► execute_query ► explain ► respond

Deterministic nodes (no LLM): input_guard, refuse, clarify, load_schema, diagnose, validate_sql,
analyze_performance, execute_query, fail, respond. LLM nodes: classify_intent, generate_sql,
explain, answer_schema.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, Optional, TypedDict

import sqlglot
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import analyzer, config, databases, db, guards, llm, prompts
from .validator import format_sql, validate_sql


class AgentState(TypedDict, total=False):
    # --- persisted across turns (conversation memory) ---
    messages: Annotated[list[dict], operator.add]   # appended each turn
    last_sql: Optional[str]                          # the query follow-ups build on

    # --- per turn (reset by the API on every new message) ---
    user_input: str
    intent: Optional[str]
    intent_reason: Optional[str]
    user_sql: Optional[str]
    schema_text: Optional[str]
    diagnostics: list[str]
    candidate_sql: Optional[str]
    attempts: int
    validation: Optional[dict]
    validation_history: list[dict]
    assumptions: list[str]
    notes: list[str]
    performance: list[dict]
    cost: Optional[dict]
    results: Optional[dict]
    execution_error: Optional[str]
    explanation: Optional[dict]
    response_type: Optional[str]      # sql | refusal | clarification | answer | error
    refusal_reason: Optional[str]
    response_text: Optional[str]
    final_sql: Optional[str]
    dialects: Optional[dict]
    steps: list[str]
    db_id: Optional[str]              # which database this turn runs against (None = sample)


TURN_RESET: dict[str, Any] = {
    "intent": None, "intent_reason": None, "user_sql": None, "schema_text": None, "diagnostics": [],
    "candidate_sql": None, "attempts": 0, "validation": None, "validation_history": [], "assumptions": [],
    "notes": [], "performance": [], "cost": None, "results": None, "execution_error": None,
    "explanation": None, "response_type": None, "refusal_reason": None, "response_text": None,
    "final_sql": None, "dialects": None, "steps": [],
}

SQL_INTENTS = {"generate", "modify", "optimize", "debug", "explain"}


# ----------------------------------- helpers -----------------------------------

def _db(state: AgentState):
    """File path of the database this conversation uses."""
    return databases.path_for(state.get("db_id"))


def _step(state: AgentState, name: str) -> list[str]:
    return list(state.get("steps") or []) + [name]


def _history(state: AgentState) -> str:
    turns = (state.get("messages") or [])[-config.HISTORY_TURNS_IN_PROMPT * 2:]
    if not turns:
        return "(none)"
    lines = []
    for m in turns:
        if m["role"] == "user":
            lines.append(f"User: {m['content']}")
        else:
            sql = f" [SQL: {m['sql']}]" if m.get("sql") else ""
            lines.append(f"Assistant: {m['content'][:300]}{sql}")
    return "\n".join(lines)


def _table_list(schema: db.Schema) -> str:
    return "; ".join(f"{t.name}({', '.join(c.name for c in t.columns)})" for t in schema.tables.values())


def _results_preview(results: Optional[dict], max_rows: int = 15) -> str:
    if not results or not results.get("columns"):
        return "(no results)"
    header = " | ".join(results["columns"])
    rows = [" | ".join(str(v) for v in r) for r in results["rows"][:max_rows]]
    total = results.get("total_rows", results["row_count"])
    note = f"\n(The query returns {total} rows in total; {min(max_rows, len(results['rows']))} are shown here.)"
    return header + "\n" + "\n".join(rows) + note


def _transpile(sql: str) -> dict:
    out = {}
    for target in ("postgres", "mysql"):
        try:
            out[target] = sqlglot.transpile(sql, read="sqlite", write=target, pretty=True)[0]
        except Exception:
            pass
    return out


# ------------------------------------ nodes ------------------------------------

def input_guard(state: AgentState) -> dict:
    g = guards.check_input(state["user_input"])
    update = {"steps": _step(state, "input_guard")}
    if g.blocked:
        update.update(response_type="refusal", refusal_reason=g.reason, response_text=g.message)
    return update


def classify_intent(state: AgentState) -> dict:
    schema = db.get_schema(_db(state))
    system = prompts.CLASSIFY_SYSTEM.format(
        injection_rule=prompts.INJECTION_RULE, table_list=_table_list(schema),
        last_sql=state.get("last_sql") or "(none)", history=_history(state))
    result: llm.IntentResult = llm.get_client().structured(
        llm.IntentResult, system, prompts.CLASSIFY_USER.format(message=state["user_input"]), fast=True)

    intent, user_sql = result.intent, (result.user_sql or "").strip() or None
    update: dict = {"steps": _step(state, "classify_intent"), "intent_reason": result.reason}

    # Deterministic corrections to the model's routing
    if intent == "modify" and not state.get("last_sql"):
        intent = "generate"
    if intent in ("optimize", "debug", "explain") and not user_sql:
        if state.get("last_sql"):
            user_sql = state["last_sql"]           # "explain that query" -> the previous one
        elif intent != "explain":
            intent = "ambiguous"
            update["response_text"] = "Please paste the SQL query you'd like me to look at."
    if intent == "ambiguous" and not update.get("response_text"):
        update["response_text"] = result.clarification_question or \
            "Could you say a bit more about what you'd like to find in the database?"

    update.update(intent=intent, user_sql=user_sql)
    return update


def refuse(state: AgentState) -> dict:
    reason = state.get("refusal_reason") or ("destructive" if state.get("intent") == "destructive" else "out_of_scope")
    return {"steps": _step(state, "refuse"), "response_type": "refusal", "refusal_reason": reason,
            "response_text": guards.REFUSAL_MESSAGES[reason]}


def clarify(state: AgentState) -> dict:
    return {"steps": _step(state, "clarify"), "response_type": "clarification"}


def answer_schema(state: AgentState) -> dict:
    system = prompts.SCHEMA_INFO_SYSTEM.format(injection_rule=prompts.INJECTION_RULE, schema=db.schema_for_prompt(db.get_schema(_db(state))))
    result: llm.SchemaAnswer = llm.get_client().structured(
        llm.SchemaAnswer, system, prompts.SCHEMA_INFO_USER.format(message=state["user_input"]), fast=True)
    return {"steps": _step(state, "answer_schema"), "response_type": "answer", "response_text": result.answer}


def load_schema(state: AgentState) -> dict:
    return {"steps": _step(state, "load_schema"), "schema_text": db.schema_for_prompt(db.get_schema(_db(state)))}


def diagnose(state: AgentState) -> dict:
    """Run the deterministic checks on SQL the user supplied, before asking the LLM anything."""
    sql = state.get("user_sql") or ""
    v = validate_sql(sql, db_path=_db(state))
    update: dict = {"steps": _step(state, "diagnose")}
    if v.destructive:
        update.update(intent="destructive", refusal_reason="destructive")
        return update
    diagnostics = list(v.errors) + list(v.warnings)
    if v.valid:
        diagnostics += [n["message"] for n in analyzer.analyze(sql, db_path=_db(state))]
    update["diagnostics"] = diagnostics or ["No problems detected by automatic checks."]
    intent = state["intent"]
    if intent == "explain":
        if v.valid:
            update["candidate_sql"] = sql
        else:
            update["intent"] = "debug"       # can't explain broken SQL; fix it first
    return update


def generate_sql(state: AgentState) -> dict:
    intent, attempts = state["intent"], state.get("attempts", 0)
    system = prompts.GENERATE_SYSTEM.format(
        today=date.today().isoformat(), injection_rule=prompts.INJECTION_RULE,
        schema=state.get("schema_text") or db.schema_for_prompt(db.get_schema(_db(state))), history=_history(state))
    msg = state["user_input"]
    last_validation = state.get("validation")
    if attempts > 0 and last_validation and not last_validation["valid"]:
        task = prompts.TASK_REPAIR.format(failed_sql=state.get("candidate_sql") or "",
                                          errors="\n".join(last_validation["errors"]), message=msg)
    elif intent == "modify":
        task = prompts.TASK_MODIFY.format(last_sql=state["last_sql"], message=msg)
    elif intent == "debug":
        task = prompts.TASK_DEBUG.format(diagnostics="\n".join(state.get("diagnostics") or []),
                                         user_sql=state["user_sql"], message=msg)
    elif intent == "optimize":
        task = prompts.TASK_OPTIMIZE.format(diagnostics="\n".join(state.get("diagnostics") or []),
                                            user_sql=state["user_sql"], message=msg)
    else:
        task = prompts.TASK_GENERATE.format(message=msg)

    draft: llm.SQLDraft = llm.get_client().structured(llm.SQLDraft, system, task, fast=False)
    update: dict = {"steps": _step(state, "generate_sql"), "candidate_sql": (draft.sql or "").strip()}
    if attempts == 0 or draft.assumptions:
        update["assumptions"] = draft.assumptions
    if attempts == 0 or draft.notes:
        update["notes"] = draft.notes
    if not update["candidate_sql"]:
        update.update(response_type="clarification",
                      response_text=draft.clarification_question or
                      "I can't answer that from this database. Could you rephrase it in terms of the available tables?")
    return update


def validate_node(state: AgentState) -> dict:
    v = validate_sql(state["candidate_sql"], db_path=_db(state))
    history = list(state.get("validation_history") or []) + [
        {"attempt": state.get("attempts", 0) + 1, "sql": state["candidate_sql"], **v.to_dict()}]
    update = {"steps": _step(state, "validate_sql"), "validation": v.to_dict(),
              "validation_history": history, "attempts": state.get("attempts", 0) + 1}
    if v.valid:
        update["final_sql"] = v.formatted_sql
    if v.destructive:
        update["refusal_reason"] = "destructive"
    return update


def analyze_performance(state: AgentState) -> dict:
    sql = state["final_sql"]
    return {"steps": _step(state, "analyze_performance"), "performance": analyzer.analyze(sql, db_path=_db(state)),
            "cost": analyzer.estimate_cost(sql, db_path=_db(state))}


def execute_query(state: AgentState) -> dict:
    update: dict = {"steps": _step(state, "execute_query")}
    try:
        update["results"] = db.run_readonly_query(state["final_sql"], db_path=_db(state))
    except Exception as e:  # timeout or runtime error: still return the SQL and explanation
        update["execution_error"] = str(e)
    return update


def explain(state: AgentState) -> dict:
    intent = state["intent"]
    mode_context = {
        "debug": "The user's SQL was broken and this is the corrected version. Problems found: "
                 + "; ".join(state.get("notes") or state.get("diagnostics") or []),
        "optimize": "This is an optimized rewrite of the user's SQL. Changes made: " + "; ".join(state.get("notes") or []),
        "modify": "This query modifies the user's previous query according to their follow-up.",
    }.get(intent, "This query answers the user's question.")
    system = prompts.EXPLAIN_SYSTEM.format(injection_rule=prompts.INJECTION_RULE)
    user = prompts.EXPLAIN_USER.format(mode_context=mode_context, sql=state["final_sql"],
                                       results_preview=_results_preview(state.get("results")),
                                       message=state["user_input"])
    try:
        e: llm.Explanation = llm.get_client().structured(llm.Explanation, system, user, fast=True)
        explanation = e.model_dump()
    except Exception:
        explanation = {"summary": "Here is the query.", "steps": [], "result_headline": ""}
    return {"steps": _step(state, "explain"), "explanation": explanation, "response_type": "sql"}


def fail(state: AgentState) -> dict:
    errors = (state.get("validation") or {}).get("errors") or ["unknown error"]
    return {"steps": _step(state, "fail"), "response_type": "error",
            "response_text": "I couldn't produce a query that passes validation after "
                             f"{state.get('attempts', 0)} attempts. Last problem: {errors[0]} "
                             "Try rephrasing, or name the tables or columns you mean."}


def respond(state: AgentState) -> dict:
    """Assemble the turn's reply and update conversation memory."""
    rtype = state.get("response_type") or "error"
    update: dict = {"steps": _step(state, "respond")}
    if rtype == "sql":
        e = state.get("explanation") or {}
        text = e.get("result_headline") or e.get("summary") or "Here is your query."
        update["response_text"] = text
        update["last_sql"] = state["final_sql"]
        update["dialects"] = _transpile(state["final_sql"])
    else:
        text = state.get("response_text") or "Something went wrong."
    update["messages"] = [
        {"role": "user", "content": state["user_input"]},
        {"role": "assistant", "content": text, "sql": state.get("final_sql") if rtype == "sql" else None,
         "type": rtype},
    ]
    return update


# ------------------------------------ routing ------------------------------------

def route_after_guard(state: AgentState) -> str:
    return "respond" if state.get("response_type") == "refusal" else "classify_intent"


def route_after_classify(state: AgentState) -> str:
    intent = state["intent"]
    if intent in ("out_of_scope", "destructive"):
        return "refuse"
    if intent == "ambiguous":
        return "clarify"
    if intent == "schema_info":
        return "answer_schema"
    return "load_schema"


def route_after_schema(state: AgentState) -> str:
    return "generate_sql" if state["intent"] in ("generate", "modify") else "diagnose"


def route_after_diagnose(state: AgentState) -> str:
    if state["intent"] == "destructive":
        return "refuse"
    if state["intent"] == "explain" and state.get("candidate_sql"):
        return "validate_sql"
    return "generate_sql"


def route_after_generate(state: AgentState) -> str:
    return "respond" if state.get("response_type") == "clarification" else "validate_sql"


def route_after_validate(state: AgentState) -> str:
    v = state["validation"]
    if v["valid"]:
        return "analyze_performance"
    if v.get("destructive"):
        return "refuse"
    if state["attempts"] < config.MAX_SQL_ATTEMPTS and state["intent"] != "explain":
        return "generate_sql"          # self-correction loop: errors are fed back to the model
    return "fail"


# ------------------------------------ build ------------------------------------

def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    for name, fn in [
        ("input_guard", input_guard), ("classify_intent", classify_intent), ("refuse", refuse),
        ("clarify", clarify), ("answer_schema", answer_schema), ("load_schema", load_schema),
        ("diagnose", diagnose), ("generate_sql", generate_sql), ("validate_sql", validate_node),
        ("analyze_performance", analyze_performance), ("execute_query", execute_query),
        ("explain", explain), ("fail", fail), ("respond", respond),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", route_after_guard, ["respond", "classify_intent"])
    g.add_conditional_edges("classify_intent", route_after_classify,
                            ["refuse", "clarify", "answer_schema", "load_schema"])
    g.add_conditional_edges("load_schema", route_after_schema, ["generate_sql", "diagnose"])
    g.add_conditional_edges("diagnose", route_after_diagnose, ["refuse", "validate_sql", "generate_sql"])
    g.add_conditional_edges("generate_sql", route_after_generate, ["respond", "validate_sql"])
    g.add_conditional_edges("validate_sql", route_after_validate,
                            ["analyze_performance", "refuse", "generate_sql", "fail"])
    g.add_edge("analyze_performance", "execute_query")
    g.add_edge("execute_query", "explain")
    g.add_edge("explain", "respond")
    for n in ("refuse", "clarify", "answer_schema", "fail"):
        g.add_edge(n, "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_turn(session_id: str, message: str, db_id: str | None = None) -> dict:
    """Run one user message through the graph for a given conversation."""
    state = get_graph().invoke({**TURN_RESET, "user_input": message, "db_id": db_id},
                               config={"configurable": {"thread_id": session_id}})
    return to_response(state)


def to_response(state: dict) -> dict:
    return {
        "type": state.get("response_type"),
        "message": state.get("response_text"),
        "intent": state.get("intent"),
        "sql": state.get("final_sql") if state.get("response_type") == "sql" else None,
        "explanation": state.get("explanation"),
        "assumptions": state.get("assumptions") or [],
        "notes": state.get("notes") or [],
        "validation": state.get("validation"),
        "attempts": state.get("attempts", 0),
        "validation_history": state.get("validation_history") or [],
        "performance": state.get("performance") or [],
        "cost": state.get("cost"),
        "results": state.get("results"),
        "execution_error": state.get("execution_error"),
        "dialects": state.get("dialects") or {},
        "refusal_reason": state.get("refusal_reason"),
        "steps": state.get("steps") or [],
    }
