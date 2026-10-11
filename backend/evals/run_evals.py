"""Run the evaluation set against the real LLM and write a report.

Usage (from backend/):  ANTHROPIC_API_KEY=... python -m evals.run_evals [--workers 4] [--only gen-01,ctx-01]
Writes evals/results/latest.md and evals/results/latest.json.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app import config, db, graph
from evals.cases import CASES

RESULTS_DIR = Path(__file__).parent / "results"


def _norm(v):
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def results_match(agent: dict, gold: dict) -> tuple[bool, str]:
    """Every gold column must appear among the agent's columns with the same multiset of values."""
    a_rows, g_rows = agent["rows"], gold["rows"]
    a_total = agent.get("total_rows", len(a_rows))
    g_total = gold.get("total_rows", len(g_rows))
    if a_total != g_total:
        return False, f"row count {a_total} vs gold {g_total}"
    if agent.get("truncated") or gold.get("truncated"):
        return True, "row counts match (large result, values not compared)"
    a_cols = [sorted(map(_norm, col), key=repr) for col in zip(*a_rows)] if a_rows else []
    for gi, g_col in enumerate(zip(*g_rows) if g_rows else []):
        target = sorted(map(_norm, g_col), key=repr)
        if target not in a_cols:
            return False, f"gold column {gold['columns'][gi]!r} not found in agent results"
    return True, "results match"


def run_case(case: dict) -> dict:
    session = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
    started = time.monotonic()
    resp, error = None, None
    try:
        for turn in case["turns"]:
            resp = graph.run_turn(session, turn)
    except Exception as e:  # an exception is a failure, never a crash of the whole run
        error = f"{type(e).__name__}: {e}"
    latency = round(time.monotonic() - started, 1)

    checks = []
    if error:
        checks.append(("no exception", False, error))
    else:
        want = case["type"] if isinstance(case["type"], tuple) else (case["type"],)
        checks.append(("type", resp["type"] in want, f"got {resp['type']}, want {'/'.join(want)}"))
        if case.get("intent"):
            checks.append(("intent", resp["intent"] == case["intent"], f"got {resp['intent']}"))
        sql = resp.get("sql") or ""
        if case.get("no_sql"):
            checks.append(("no SQL returned", not sql, "returned SQL" if sql else "ok"))
        for s in case.get("sql_includes", []):
            checks.append((f"SQL includes {s!r}", s.lower() in sql.lower(), "ok" if s.lower() in sql.lower() else "missing"))
        for s in case.get("sql_excludes", []):
            ok = s.lower() not in sql.lower()
            checks.append((f"SQL excludes {s!r}", ok, "ok" if ok else "present"))
        if sql:
            checks.append(("validated", bool(resp["validation"] and resp["validation"]["valid"]), ""))
        if case.get("gold_sql"):
            if resp.get("results"):
                gold = db.run_readonly_query(case["gold_sql"])
                ok, why = results_match(resp["results"], gold)
                checks.append(("results match gold", ok, why))
            else:
                checks.append(("results match gold", False, "no results returned"))

    return {
        "id": case["id"], "category": case["category"], "turns": case["turns"],
        "passed": all(c[1] for c in checks), "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks],
        "latency_s": latency, "sql": (resp or {}).get("sql"), "response_type": (resp or {}).get("type"),
        "intent": (resp or {}).get("intent"), "attempts": (resp or {}).get("attempts"),
        "message": (resp or {}).get("message"),
    }


def write_report(results: list[dict]) -> str:
    RESULTS_DIR.mkdir(exist_ok=True)
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    passed = sum(r["passed"] for r in results)
    repaired = sum(1 for r in results if (r["attempts"] or 0) > 1)
    lat = sorted(r["latency_s"] for r in results)
    lines = [
        "# QueryPilot evaluation results", "",
        f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M')} · models: {config.LLM_MODEL} (SQL), {config.LLM_FAST_MODEL} (routing, explanations)", "",
        f"**{passed} / {len(results)} cases passed ({passed / len(results):.0%})** · "
        f"median latency {lat[len(lat) // 2]}s · {repaired} case(s) needed the self-correction loop", "",
        "| Category | Passed | Total |", "| --- | --- | --- |",
    ]
    for cat, rs in by_cat.items():
        lines.append(f"| {cat} | {sum(r['passed'] for r in rs)} | {len(rs)} |")
    lines += ["", "## Every case", "", "| ID | Result | Request | Latency | Notes |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        failed = [f"{c['name']}: {c['detail']}" for c in r["checks"] if not c["ok"]]
        req = " → ".join(r["turns"]).replace("|", "/")
        lines.append(f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {req} | {r['latency_s']}s | "
                     f"{'; '.join(failed).replace('|', '/') if failed else ''} |")
    report = "\n".join(lines) + "\n"
    (RESULTS_DIR / "latest.md").write_text(report)
    (RESULTS_DIR / "latest.json").write_text(json.dumps(results, indent=2, default=str))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    cases = [c for c in CASES if not args.only or c["id"] in args.only.split(",")]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_case, cases))
    print(write_report(results))


if __name__ == "__main__":
    main()
