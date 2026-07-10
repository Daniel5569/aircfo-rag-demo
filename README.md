# airCFO RAG demo — a financial context layer, exposed as an MCP server

A working proof of concept for the exact thing airCFO's founding engineer post describes:
a RAG layer over a startup's financial data, exposed as an MCP server, with an agentic
chat layer on top that cites its sources row by row.

Ask it "why did our SaaS spend jump in March?" and it doesn't just retrieve similar
text — it calls a `monthly_flux_analysis` tool, computes the actual month-over-month
delta, and cites the three new vendors and the Notion seat upgrade that caused it.

## Mental model

There are two ways to talk to this system, because they solve two different problems:

```
                        ┌─────────────────────────┐
                        │   Synthetic dataset      │
                        │   data/*.csv + *.pdf      │
                        └────────────┬─────────────┘
                                     │ ingest/ingest.py
                                     ▼
                        ┌─────────────────────────┐
                        │   VectorStore             │
                        │   (SQLite dev / pgvector   │
                        │    prod via DATABASE_URL) │
                        └────────────┬─────────────┘
                                     │
                    ┌────────────────┴─────────────────┐
                    ▼                                   ▼
      ┌───────────────────────┐          ┌───────────────────────────┐
      │ mcp_server/server.py    │          │ mcp_server/api.py           │
      │ real MCP protocol        │          │ REST wrapper for the        │
      │ (stdio / streamable-http)│          │ React frontend               │
      │ → Claude Desktop, Claude │          │ → runs its OWN Claude tool-  │
      │   Code, any MCP client   │          │   use loop over all 3 tools  │
      │   orchestrates the 3     │          │   (answer_question), since   │
      │   tools itself           │          │   a browser isn't an MCP     │
      └───────────────────────┘          │   client                     │
                                          └──────────────┬─────────────┘
                                                         ▼
                                          ┌───────────────────────────┐
                                          │ frontend/ (React + Vite)    │
                                          │ chat + cited-sources table  │
                                          └───────────────────────────┘
```

`mcp_server/tools.py` is the single source of truth for the 3 tools. Both
transports call the same functions — the MCP server exposes them for an MCP
client's model to orchestrate; the REST API runs its own orchestration loop
with the Anthropic SDK because the frontend is a plain browser, not an MCP
client.

## The 3 tools

- **`search_financial_records`** — semantic search (sentence-transformers
  embeddings) over transactions, invoices, monthly P&L, and vendor contract
  PDFs. Good for open-ended lookups ("what does our Notion contract say about
  renewal").
- **`monthly_flux_analysis(month)`** — deterministic computation, not a vector
  search: groups transactions by category for the given month vs. the prior
  month and returns the actual dollar delta, with the specific transactions
  that drove it. This is what answers "why did X change" questions correctly —
  semantic similarity alone gets you documents that *mention* the topic, not
  the arithmetic that explains it.
- **`find_anomalies`** — scans invoices for duplicate payments (same vendor +
  amount + issue date under different invoice numbers) and overdue unpaid
  invoices.

## Design decisions worth knowing about

- **Local dev uses SQLite, production targets pgvector on Neon.**
  `ingest/vector_store.py` defines one `VectorStore` interface with two
  implementations. Docker Desktop wasn't available in the environment this was
  built in, so local dev/demo runs on a zero-infra SQLite backend (cosine
  similarity in Python — fine at this data volume). Setting `DATABASE_URL`
  switches to the pgvector implementation with no code changes elsewhere.
  `docker-compose.yml` is included for local Postgres+pgvector once Docker is
  available.
- **The REST chat endpoint is agentic, not just RAG.** An earlier version of
  `query_financials` only ever did vector search, which meant "why did SaaS
  spend jump in March" retrieved the March P&L row (which shows *that* it
  jumped) but never the individual transactions explaining *why*. Fixed by
  giving Claude all 3 tools via the Anthropic tool-use API and letting it
  decide `monthly_flux_analysis("March 2026")` is the right call — same
  pattern a real MCP client (Claude Desktop, Claude Code) does automatically
  when you point it at `mcp_server/server.py`.
- **Embeddings are local (sentence-transformers), not an API call.** No
  external dependency or per-request cost for retrieval; only the final answer
  synthesis calls the Anthropic API.
- **Degraded mode is explicit, not silent.** Without `ANTHROPIC_API_KEY` set,
  `/api/chat` still returns cited results (raw semantic search) but prefixes
  the answer with `[degraded mode]` rather than pretending to reason across
  tools it never called.

## Running it locally

```bash
pip install -r requirements.txt

# 1. Generate the synthetic dataset (transactions, P&L, invoices, contracts)
python data/generate_dataset.py

# 2. Ingest + embed into the vector store (SQLite by default)
python ingest/ingest.py

# 3. Run the REST API the frontend talks to
export ANTHROPIC_API_KEY=sk-...   # optional, enables agentic tool-use answers
cd mcp_server && python -m uvicorn api:app --port 8000

# 4. Run the frontend
cd frontend && npm install && npm run dev
```

### As an MCP server (Claude Desktop / Claude Code)

```bash
python mcp_server/server.py          # stdio transport
python mcp_server/server.py --http   # streamable-http transport
```

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aircfo-financials": {
      "command": "python",
      "args": ["/absolute/path/to/mcp_server/server.py"]
    }
  }
}
```

### Postgres + pgvector (matches the production target)

```bash
docker compose up -d
export DATABASE_URL=postgresql://aircfo:aircfo_dev_password@localhost:5433/aircfo_demo
python ingest/ingest.py
```

## Deploying (Vercel + Render)

Backend runs on Render, not Vercel: Vercel's Python serverless functions cap
deployment size well below what `sentence-transformers` + `torch` need. Render
runs a normal long-lived Python process instead, no size limit problem.

The free demo deployment skips Postgres/Neon entirely and ships the
pre-built SQLite index (`ingest/aircfo_demo.sqlite3`) committed in the repo,
for a reason worth knowing: Render's free instance is 512MB RAM / 0.1 CPU,
and running `ingest.py` (downloads + runs the embedding model) at every cold
start blew past Render's ~5 minute port-binding timeout before the server
even came up. Regenerating and re-committing that file after changing the
dataset is a one-line `python ingest/ingest.py` run before pushing. A real
deployment with live data would use pgvector on Neon (the code already
supports it via `DATABASE_URL`, see `ingest/vector_store.py`) on an instance
with enough CPU/RAM to embed at request time.

1. **Render**: create a free account, "New +" → "Web Service", point it at
   this GitHub repo. Root Directory `mcp_server`, Build Command
   `pip install -r ../requirements.txt`, Start Command
   `uvicorn api:app --host 0.0.0.0 --port $PORT`, Instance Type Free. Add
   `ANTHROPIC_API_KEY` as an environment variable if you have one (optional —
   without it the API runs in "degraded mode", see above). Deploy. Copy the
   resulting `https://<name>.onrender.com` URL.
2. **Vercel**: create a free account, "Add New" → "Project", point it at this
   repo with Root Directory set to `frontend`. Add an environment variable
   `VITE_API_BASE` = the Render URL from step 1, then deploy.

Render's free tier spins down after inactivity, so the first request after a
while can take ~30s to wake up — worth knowing before sending a link to
someone who'll click it cold.

## What's not done yet

- No auth on the API — fine for a demo, not for anything with real financial
  data.
- `monthly_flux_analysis` compares exactly one month back; a real version
  would take an arbitrary date range.
- The synthetic dataset (`data/generate_dataset.py`) is deterministic (seeded)
  so the demo is reproducible; swap in a real QuickBooks/Xero export and the
  ingestion pipeline doesn't change.
