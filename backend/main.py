from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd

from backend.database import get_db
from backend.orchestrator import process_batch

app = FastAPI(title="FundGuard API")


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post("/run-batch")
def run_batch(
    limit: int = Query(50, description="Number of mandate-cycle rows to process"),
    generate_messages: bool = Query(False, description="Whether to call the LLM for each flagged mandate"),
    tone: str = Query("english", description="'english' or 'hinglish'"),
    db: Session = Depends(get_db),
):
    """
    Runs a batch of synthetic mandate-cycle rows through the full pipeline:
    risk scoring -> rule engine -> reactive triage -> optional LLM messaging.
    Every decision is persisted to Postgres as it happens.
    """
    df = pd.read_csv("./data/synthetic_mandates.csv").head(limit)
    summary = process_batch(db, df, generate_messages=generate_messages, tone=tone)
    return summary