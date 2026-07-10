"""
Storage abstraction for the RAG index: one interface, two backends.

- SQLiteVectorStore: zero-infra local dev/demo backend (cosine similarity in Python).
- PostgresVectorStore: pgvector backend, used when DATABASE_URL is set (Neon in prod).

The MCP server tools only ever talk to VectorStore, never to a specific backend,
so swapping from local dev to the Neon deploy is a one-line change (set DATABASE_URL).
"""
import json
import os
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

DB_PATH = Path(__file__).parent / "aircfo_demo.sqlite3"


@dataclass
class Chunk:
    id: int
    source: str          # e.g. "transactions.csv", "invoices.csv", "notion_msa.pdf"
    ref: str             # e.g. "TXN-1005" or "INV-2003" or "notion_msa.pdf p.1" - used for citations
    text: str
    score: float = 0.0


class VectorStore:
    def reset(self) -> None: ...
    def add_chunks(self, rows: list[tuple[str, str, str, np.ndarray]]) -> None: ...
    def search(self, query_vec: np.ndarray, top_k: int = 8) -> list[Chunk]: ...
    def all_chunks(self, source: Optional[str] = None) -> list[Chunk]: ...


class SQLiteVectorStore(VectorStore):
    def __init__(self, path: Path = DB_PATH):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                ref TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL
            )
        """)
        self.conn.commit()

    def reset(self) -> None:
        self.conn.execute("DELETE FROM chunks")
        self.conn.commit()

    def add_chunks(self, rows: list[tuple[str, str, str, np.ndarray]]) -> None:
        payload = [
            (source, ref, text, struct.pack(f"{len(vec)}f", *vec.tolist()))
            for source, ref, text, vec in rows
        ]
        self.conn.executemany(
            "INSERT INTO chunks (source, ref, text, embedding) VALUES (?, ?, ?, ?)",
            payload,
        )
        self.conn.commit()

    def search(self, query_vec: np.ndarray, top_k: int = 8) -> list[Chunk]:
        cur = self.conn.execute("SELECT id, source, ref, text, embedding FROM chunks")
        rows = cur.fetchall()
        q = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        scored = []
        for cid, source, ref, text, blob in rows:
            vec = np.array(struct.unpack(f"{len(blob) // 4}f", blob))
            vec = vec / (np.linalg.norm(vec) + 1e-8)
            score = float(np.dot(q, vec))
            scored.append(Chunk(id=cid, source=source, ref=ref, text=text, score=score))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_k]

    def all_chunks(self, source: Optional[str] = None) -> list[Chunk]:
        if source:
            cur = self.conn.execute("SELECT id, source, ref, text FROM chunks WHERE source = ?", (source,))
        else:
            cur = self.conn.execute("SELECT id, source, ref, text FROM chunks")
        return [Chunk(id=r[0], source=r[1], ref=r[2], text=r[3]) for r in cur.fetchall()]


class PostgresVectorStore(VectorStore):
    """pgvector-backed store. Requires DATABASE_URL, e.g. a Neon connection string."""

    def __init__(self, database_url: str, dim: int = 384):
        import psycopg2
        from pgvector.psycopg2 import register_vector

        self.dim = dim
        self.conn = psycopg2.connect(database_url)
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.commit()
        register_vector(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    id SERIAL PRIMARY KEY,
                    source TEXT NOT NULL,
                    ref TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding vector({dim})
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                ON chunks USING ivfflat (embedding vector_cosine_ops)
            """)
        self.conn.commit()

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE chunks RESTART IDENTITY")
        self.conn.commit()

    def add_chunks(self, rows: list[tuple[str, str, str, np.ndarray]]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (source, ref, text, embedding) VALUES (%s, %s, %s, %s)",
                [(s, r, t, v.tolist()) for s, r, t, v in rows],
            )
        self.conn.commit()

    def search(self, query_vec: np.ndarray, top_k: int = 8) -> list[Chunk]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, ref, text, 1 - (embedding <=> %s) AS score
                FROM chunks ORDER BY embedding <=> %s LIMIT %s
                """,
                (query_vec.tolist(), query_vec.tolist(), top_k),
            )
            return [Chunk(id=r[0], source=r[1], ref=r[2], text=r[3], score=float(r[4])) for r in cur.fetchall()]

    def all_chunks(self, source: Optional[str] = None) -> list[Chunk]:
        with self.conn.cursor() as cur:
            if source:
                cur.execute("SELECT id, source, ref, text FROM chunks WHERE source = %s", (source,))
            else:
                cur.execute("SELECT id, source, ref, text FROM chunks")
            return [Chunk(id=r[0], source=r[1], ref=r[2], text=r[3]) for r in cur.fetchall()]


def get_vector_store() -> VectorStore:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return PostgresVectorStore(database_url)
    return SQLiteVectorStore()
