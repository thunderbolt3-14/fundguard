from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text, func
import pandas as pd
import json

from backend.database import get_db
from backend.orchestrator import process_batch
from backend.models import Customer, Mandate, CycleEvent, Message
from messaging.generate_message import generate_message

app = FastAPI(title="FundGuard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.post("/run-batch")
def run_batch(
    limit: int = Query(50, description="Number of mandate-cycle rows to process"),
    generate_messages: bool = Query(False, description="Whether to call the LLM for each flagged mandate"),
    tone: str = Query("english", description="'english' or 'hinglish'"),
    create_razorpay: bool = Query(False, description="Whether to create real Razorpay test-mode subscriptions"),
    db: Session = Depends(get_db),
):
    """
    Random-samples rows from the synthetic dataset rather than always
    starting from row 0 - repeated demo runs would otherwise keep hitting
    the same already-processed customers, which our own compliance rules
    correctly block, making every subsequent run look empty/boring.
    """
    full_df = pd.read_csv("./data/synthetic_mandates.csv")
    df = full_df.sample(n=min(limit, len(full_df)))
    summary = process_batch(db, df, generate_messages=generate_messages, tone=tone, create_razorpay=create_razorpay)
    return summary


@app.post("/preview-message")
def preview_message(
    action: str = Query(..., description="Action type, e.g. standard_nudge"),
    tone: str = Query("english", description="'english' or 'hinglish'"),
    mandate_name: str = Query("Netflix", description="Sample subscription name"),
    amount: float = Query(299, description="Sample mandate amount in INR"),
):
    """
    Generates a single message on demand, independent of the batch/database
    pipeline - lets a demo reliably show off tone/action variety (like
    Hinglish) without depending on a real batch happening to produce that
    exact case.
    """
    text_out = generate_message(
        action=action, mandate_name=mandate_name, amount=amount,
        debit_date="3rd of next month", tone=tone,
    )
    return {"message": text_out}


@app.post("/reset-data")
def reset_data(db: Session = Depends(get_db)):
    """
    Atomic TRUNCATE with CASCADE - handles foreign-key ordering automatically
    and safely, unlike sequential ORM .delete() calls which can race with a
    concurrent in-flight request. RESTART IDENTITY also resets auto-increment
    IDs for a genuinely clean demo state.
    """
    db.execute(text("TRUNCATE TABLE messages, cycle_events, mandates, customers RESTART IDENTITY CASCADE"))
    db.commit()
    return {"status": "reset complete"}


@app.get("/model-info")
def model_info():
    with open("./model/model_metrics.json") as f:
        return json.load(f)


@app.get("/events")
def get_events(
    limit: int = Query(50, description="Max rows to return"),
    risk_band: str | None = Query(None),
    failed_only: bool = Query(False),
    blocked_only: bool = Query(False),
    has_message: bool = Query(False),
    db: Session = Depends(get_db),
):
    query = (
        db.query(CycleEvent, Mandate, Message)
        .join(Mandate, CycleEvent.mandate_id == Mandate.mandate_id)
        .outerjoin(Message, Message.event_id == CycleEvent.event_id)
    )
    if risk_band:
        query = query.filter(CycleEvent.risk_band == risk_band)
    if failed_only:
        query = query.filter(CycleEvent.actual_outcome_failed == True)
    if blocked_only:
        query = query.filter(CycleEvent.blocked_reason.isnot(None))
    if has_message:
        query = query.filter(Message.message_id.isnot(None))

    rows = query.order_by(CycleEvent.event_id.desc()).limit(limit).all()

    results = []
    for event, mandate, message in rows:
        results.append({
            "event_id": event.event_id,
            "customer_id": mandate.customer_id,
            "mandate_amount": float(mandate.mandate_amount),
            "cycle_number": event.cycle_number,
            "risk_score": float(event.risk_score),
            "risk_band": event.risk_band,
            "predictive_action": event.predictive_action,
            "reason_code": event.reason_code,
            "blocked_reason": event.blocked_reason,
            "actual_outcome_failed": event.actual_outcome_failed,
            "triage_category": event.triage_category,
            "retry_scheduled": event.retry_scheduled,
            "retry_expected_value": float(event.retry_expected_value) if event.retry_expected_value else None,
            "razorpay_subscription_id": mandate.razorpay_subscription_id,
            "message_text": message.message_text if message else None,
            "message_tone": message.tone if message else None,
        })
    return results


@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total_events = db.query(func.count(CycleEvent.event_id)).scalar()
    total_customers = db.query(func.count(Customer.customer_id)).scalar()
    total_failures = db.query(func.count(CycleEvent.event_id)).filter(CycleEvent.actual_outcome_failed == True).scalar()
    total_compliance_blocks = db.query(func.count(CycleEvent.event_id)).filter(CycleEvent.blocked_reason.isnot(None)).scalar()
    total_razorpay = db.query(func.count(Mandate.mandate_id)).filter(Mandate.razorpay_subscription_id.isnot(None)).scalar()

    expected_value_protected = (
        db.query(func.coalesce(func.sum(CycleEvent.retry_expected_value), 0))
        .filter(CycleEvent.retry_scheduled == True)
        .scalar()
    )

    action_breakdown = dict(
        db.query(CycleEvent.predictive_action, func.count(CycleEvent.event_id))
        .group_by(CycleEvent.predictive_action)
        .all()
    )
    risk_band_breakdown = dict(
        db.query(CycleEvent.risk_band, func.count(CycleEvent.event_id))
        .group_by(CycleEvent.risk_band)
        .all()
    )

    return {
        "total_events": total_events,
        "total_customers": total_customers,
        "total_failures": total_failures,
        "total_compliance_blocks": total_compliance_blocks,
        "total_razorpay_subscriptions": total_razorpay,
        "expected_value_protected_inr": float(expected_value_protected),
        "action_breakdown": action_breakdown,
        "risk_band_breakdown": risk_band_breakdown,
    }