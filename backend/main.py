from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
import pandas as pd
import json

from backend.database import get_db
from backend.orchestrator import process_batch
from sqlalchemy import func

app = FastAPI(title="FundGuard API")


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}

@app.get("/model-info")
def model_info():
    with open("./model/model_metrics.json") as f:
        return json.load(f)

@app.get("/events")
def get_events(limit: int = Query(50, description="Max rows to return"), db: Session = Depends(get_db)):
    """
    Full audit trail: cycle_events joined with mandate, customer, and any
    generated message. Ordered newest first.
    """
    from backend.models import CycleEvent, Mandate, Customer, Message

    rows = (
        db.query(CycleEvent, Mandate, Message)
        .join(Mandate, CycleEvent.mandate_id == Mandate.mandate_id)
        .outerjoin(Message, Message.event_id == CycleEvent.event_id)
        .order_by(CycleEvent.event_id.desc())
        .limit(limit)
        .all()
    )

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
    """
    Cumulative totals across everything ever persisted - not just the
    last batch run. This is what the dashboard's hero metric and summary
    panels pull from on page load / refresh.
    """
    from backend.models import CycleEvent, Mandate, Customer

    total_events = db.query(func.count(CycleEvent.event_id)).scalar()
    total_customers = db.query(func.count(Customer.customer_id)).scalar()
    total_failures = db.query(func.count(CycleEvent.event_id)).filter(CycleEvent.actual_outcome_failed == True).scalar()
    total_compliance_blocks = db.query(func.count(CycleEvent.event_id)).filter(CycleEvent.blocked_reason.isnot(None)).scalar()
    total_razorpay = db.query(func.count(Mandate.mandate_id)).filter(Mandate.razorpay_subscription_id.isnot(None)).scalar()

    # Defensible "recovered value" figure: sum of expected value across
    # retries the ROI model actually decided were worth scheduling -
    # explicitly an estimate, not a guaranteed/actual recovery number.
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

@app.post("/run-batch")
def run_batch(
    limit: int = Query(50, description="Number of mandate-cycle rows to process"),
    generate_messages: bool = Query(False, description="Whether to call the LLM for each flagged mandate"),
    tone: str = Query("english", description="'english' or 'hinglish'"),
    create_razorpay: bool = Query(False, description="Whether to create real Razorpay test-mode subscriptions"),
    db: Session = Depends(get_db),
):
    df = pd.read_csv("./data/synthetic_mandates.csv").head(limit)
    summary = process_batch(db, df, generate_messages=generate_messages, tone=tone, create_razorpay=create_razorpay)
    return summary