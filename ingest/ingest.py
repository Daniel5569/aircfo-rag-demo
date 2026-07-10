"""
Ingestion pipeline: reads the synthetic dataset in data/, turns each record into
a citable text chunk, embeds it, and loads it into the vector store.

Run: python ingest/ingest.py
(set DATABASE_URL to target Postgres/Neon instead of the local SQLite dev store)

Embeddings are TF-IDF (scikit-learn), not sentence-transformers. That's a
deliberate downgrade from real semantic embeddings: torch + transformers need
several hundred MB of RAM just to import, which reliably crashed the free
512MB Render instance this demo is deployed on. TF-IDF has no torch dependency
and is nearly instant to fit/load, at the cost of matching on vocabulary
overlap rather than true semantic similarity - fine for this corpus, where
questions and records share a lot of literal terms (SaaS, invoice, vendor
names). ingest/vector_store.py and mcp_server/tools.py don't need to know
which embedder produced the vectors, only their dimensionality.
"""
import csv
import pickle
import sys
from pathlib import Path

import numpy as np
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer

sys.path.insert(0, str(Path(__file__).parent))
from vector_store import get_vector_store  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / "data"
VECTORIZER_PATH = Path(__file__).parent / "tfidf_vectorizer.pkl"


def load_transactions_chunks():
    chunks = []
    with open(DATA_DIR / "transactions.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            text = (
                f"Transaction {row['transaction_id']} on {row['date']}: vendor '{row['vendor']}', "
                f"category '{row['category']}', amount {row['amount']} USD. {row['description']}"
            )
            chunks.append(("transactions.csv", row["transaction_id"], text))
    return chunks


def load_pnl_chunks():
    chunks = []
    with open(DATA_DIR / "monthly_pnl.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            text = (
                f"Monthly P&L for {row['month']}: revenue ${row['revenue']}, total opex ${row['total_opex']} "
                f"(of which SaaS subscriptions ${row['saas_subscriptions_opex']}), net income ${row['net_income']}."
            )
            chunks.append(("monthly_pnl.csv", row["month"], text))
    return chunks


def load_invoice_chunks():
    chunks = []
    with open(DATA_DIR / "invoices.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            paid = row["paid_date"] or "NOT PAID"
            text = (
                f"Invoice {row['invoice_id']} from vendor '{row['vendor']}': amount ${row['amount']}, "
                f"issued {row['issue_date']}, due {row['due_date']}, paid date {paid}, status: {row['status']}."
            )
            chunks.append(("invoices.csv", row["invoice_id"], text))
    return chunks


def load_contract_chunks():
    chunks = []
    contracts_dir = DATA_DIR / "contracts"
    for pdf_path in sorted(contracts_dir.glob("*.pdf")):
        reader = PdfReader(str(pdf_path))
        for page_num, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = " ".join(text.split())
            if text:
                chunks.append((pdf_path.name, f"{pdf_path.name} p.{page_num}", text))
    return chunks


def main():
    all_chunks = (
        load_transactions_chunks()
        + load_pnl_chunks()
        + load_invoice_chunks()
        + load_contract_chunks()
    )
    print(f"prepared {len(all_chunks)} chunks, fitting TF-IDF vectorizer...")

    texts = [c[2] for c in all_chunks]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=2000)
    embeddings = vectorizer.fit_transform(texts).toarray()

    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)
    print(f"saved fitted vectorizer to {VECTORIZER_PATH} (vocab size {len(vectorizer.vocabulary_)})")

    store = get_vector_store()
    store.reset()
    rows = [
        (source, ref, text, np.asarray(vec, dtype=np.float32))
        for (source, ref, text), vec in zip(all_chunks, embeddings)
    ]
    store.add_chunks(rows)
    print(f"ingested {len(rows)} chunks into {type(store).__name__}")


if __name__ == "__main__":
    main()
