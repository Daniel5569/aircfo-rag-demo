"""
Thin REST wrapper around the same tool functions the MCP server exposes,
so the React frontend (which can't easily speak stdio/MCP itself) has a
plain HTTP endpoint to call. Deployed as the backend on Vercel; Postgres
lives on Neon (set DATABASE_URL in the deployment env).
"""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from tools import answer_question, find_anomalies, monthly_flux_analysis  # noqa: E402

app = FastAPI(title="airCFO RAG demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest):
    return answer_question(req.question)


@app.get("/api/flux/{month}")
def flux(month: str):
    return monthly_flux_analysis(month)


@app.get("/api/anomalies")
def anomalies():
    return find_anomalies()
