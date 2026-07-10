"""
Core tool logic, shared by the MCP server (mcp_server/server.py) and the
HTTP chat API (mcp_server/api.py). Kept separate from both transports so the
same functions back the MCP tool-call path and the plain REST path.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

# Model is already cached locally after the first ingest run; skip the
# Hugging Face Hub network round-trip on every cold start (it was adding
# 20-30s of latency per process start, depending on network conditions).
os.environ.setdefault("HF_HUB_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).parent.parent / "ingest"))
from vector_store import get_vector_store  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"

_model = None
_store = None


def _embedder():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _vector_store():
    global _store
    if _store is None:
        _store = get_vector_store()
    return _store


def search_financial_records(question: str, top_k: int = 6) -> list[dict]:
    """Semantic search over transactions/invoices/P&L/contracts. Returns row-level
    citations only, no synthesis; the raw building block query_financials/answer_question
    are built on top of."""
    vec = _embedder().encode(question)
    hits = _vector_store().search(vec, top_k=top_k)
    return [
        {"source": h.source, "ref": h.ref, "text": h.text, "relevance": round(h.score, 3)}
        for h in hits
    ]


def query_financials(question: str, top_k: int = 6) -> dict:
    """Kept as a standalone MCP tool for pure semantic lookup (e.g. 'what does our
    contract with Notion say about renewal'). For questions that need computed
    analysis (deltas, anomalies) use answer_question, which lets Claude pick the
    right tool instead of relying on vector similarity alone."""
    citations = search_financial_records(question, top_k=top_k)
    answer = "Here's what the data shows:\n" + "\n".join(
        f"- [{c['source']}/{c['ref']}] {c['text']}" for c in citations
    )
    return {"answer": answer, "citations": citations}


# --- Agentic entry point: Claude gets all 3 tools and decides which to call. ---
# This is what the /api/chat endpoint uses, and what "Collegalo a Claude via MCP"
# means in practice: the model orchestrates search_financial_records /
# monthly_flux_analysis / find_anomalies itself instead of us hand-routing questions.

_CLAUDE_TOOLS = [
    {
        "name": "search_financial_records",
        "description": "Semantic search over transactions, invoices, monthly P&L, and vendor contracts. Good for open-ended lookups.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "monthly_flux_analysis",
        "description": "Computes month-over-month operating expense deltas by category for a given month (format 'March 2026'), with the specific transactions that drove each delta. Use this for any 'why did X change/jump/drop in month Y' question.",
        "input_schema": {
            "type": "object",
            "properties": {"month": {"type": "string"}},
            "required": ["month"],
        },
    },
    {
        "name": "find_anomalies",
        "description": "Scans invoices for duplicate payments and overdue unpaid invoices.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_TOOL_DISPATCH = {
    "search_financial_records": lambda i: search_financial_records(i["question"]),
    "monthly_flux_analysis": lambda i: monthly_flux_analysis(i["month"]),
    "find_anomalies": lambda i: find_anomalies(),
}


def answer_question(question: str, max_turns: int = 4) -> dict:
    """Main chat entry point. With ANTHROPIC_API_KEY set, runs a Claude tool-use
    loop over all 3 tools and returns a cited answer. Without a key, falls back
    to plain semantic search (degraded: no cross-tool reasoning)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        result = query_financials(question)
        result["answer"] = "[degraded mode, set ANTHROPIC_API_KEY for tool-use reasoning]\n" + result["answer"]
        return result

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    system = (
        "You are a startup CFO assistant. Use the available tools to answer the "
        "question, then write a final answer that cites the source for every claim "
        "using [source/ref] tags exactly as they appear in the tool results."
    )
    messages = [{"role": "user", "content": question}]
    all_citations = []

    for _ in range(max_turns):
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=800,
            system=system,
            tools=_CLAUDE_TOOLS,
            messages=messages,
        )
        if resp.stop_reason != "tool_use":
            final_text = "".join(b.text for b in resp.content if b.type == "text")
            return {"answer": final_text, "citations": all_citations}

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            result = _TOOL_DISPATCH[block.name](block.input)
            _collect_citations(block.name, result, all_citations)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=str),
            })
        messages.append({"role": "user", "content": tool_results})

    return {"answer": "Reached max tool-use turns without a final answer.", "citations": all_citations}


def _collect_citations(tool_name: str, result: dict | list, sink: list[dict]) -> None:
    if tool_name == "search_financial_records":
        sink.extend(result)
    elif tool_name == "monthly_flux_analysis" and "biggest_opex_changes" in result:
        for change in result["biggest_opex_changes"]:
            for txn in change["citations"]:
                sink.append({"source": "transactions.csv", "ref": txn["transaction_id"], "text": txn["description"], "relevance": None})
    elif tool_name == "find_anomalies":
        for dup in result.get("duplicate_payment_suspects", []):
            for inv_id in dup["invoice_ids"]:
                sink.append({"source": "invoices.csv", "ref": inv_id, "text": dup["reason"], "relevance": None})
        for od in result.get("overdue_unpaid_invoices", []):
            sink.append({"source": "invoices.csv", "ref": od["invoice_id"], "text": f"Overdue, due {od['due_date']}", "relevance": None})


def monthly_flux_analysis(month: str) -> dict:
    """Explain month-over-month spend changes for a given month (e.g. 'March 2026'),
    citing the specific transactions that drove the biggest deltas."""
    pnl = pd.read_csv(DATA_DIR / "monthly_pnl.csv")
    months = pnl["month"].tolist()
    if month not in months:
        return {"error": f"Unknown month '{month}'. Known months: {months}"}

    idx = months.index(month)
    if idx == 0:
        return {"error": f"'{month}' is the first month in the dataset, no prior month to compare."}
    prev_month = months[idx - 1]

    txns = pd.read_csv(DATA_DIR / "transactions.csv")
    txns["month"] = pd.to_datetime(txns["date"]).dt.strftime("%B %Y")

    cur = txns[txns["month"] == month]
    prev = txns[txns["month"] == prev_month]

    # amounts are stored negative for outflows, so spend_delta = -(cur - prev):
    # positive spend_delta means the category cost MORE this month than last.
    cur_by_cat = cur.groupby("category")["amount"].sum()
    prev_by_cat = prev.groupby("category")["amount"].sum()
    spend_delta = (prev_by_cat.reindex(cur_by_cat.index, fill_value=0) - cur_by_cat).sort_values(ascending=False)

    flux = []
    for category, delta_amount in spend_delta.items():
        if abs(delta_amount) < 1:
            continue
        driving_txns = cur[cur["category"] == category][["transaction_id", "vendor", "amount", "description"]]
        flux.append({
            "category": category,
            "spend_delta_usd": round(float(delta_amount), 2),
            "note": "positive = spent more than prior month, negative = spent less",
            "citations": driving_txns.to_dict(orient="records"),
        })
    flux.sort(key=lambda f: f["spend_delta_usd"], reverse=True)

    return {
        "month": month,
        "compared_to": prev_month,
        "revenue_delta_usd": round(
            float(pnl.loc[pnl["month"] == month, "revenue"].iloc[0])
            - float(pnl.loc[pnl["month"] == prev_month, "revenue"].iloc[0]), 2
        ),
        "net_income_delta_usd": round(
            float(pnl.loc[pnl["month"] == month, "net_income"].iloc[0])
            - float(pnl.loc[pnl["month"] == prev_month, "net_income"].iloc[0]), 2
        ),
        "biggest_opex_changes": flux[:5],
    }


def find_anomalies() -> dict:
    """Scan invoices for likely duplicate payments and overdue unpaid invoices."""
    inv = pd.read_csv(DATA_DIR / "invoices.csv")

    duplicates = []
    grouped = inv.groupby(["vendor", "amount", "issue_date"])
    for (vendor, amount, issue_date), group in grouped:
        if len(group) > 1:
            duplicates.append({
                "vendor": vendor,
                "amount": float(amount),
                "issue_date": issue_date,
                "invoice_ids": group["invoice_id"].tolist(),
                "reason": "Same vendor, amount, and issue date billed on multiple invoice numbers.",
            })

    overdue = inv[inv["status"] == "overdue"][["invoice_id", "vendor", "amount", "due_date"]].to_dict(orient="records")

    return {
        "duplicate_payment_suspects": duplicates,
        "overdue_unpaid_invoices": overdue,
    }
