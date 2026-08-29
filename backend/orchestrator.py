"""
Phase 6b: Core orchestration logic - ingests a batch of mandate-cycle rows,
runs the full risk -> rules -> triage -> (optional) messaging pipeline,
and persists every decision to Postgres as the audit trail.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import joblib
from sqlalchemy.orm import Session

from backend.models import Customer, Mandate, CycleEvent, Message
from rules.rule_engine import decide_action, compute_reason_code
from rules.reactive_triage import decide_retry
from messaging.generate_message import generate_message

_saved = joblib.load("./model/risk_model.joblib")
MODEL, SCALER, FEATURES = _saved["model"], _saved["scaler"], _saved["features"]


def get_or_create_customer(db: Session, row: pd.Series) -> Customer:
    customer = db.get(Customer, row["customer_id"])
    if customer is None:
        customer = Customer(
            customer_id=row["customer_id"],
            baseline_balance=float(row["baseline_balance"]),
            balance_volatility=float(row["balance_volatility"]),
            salary_day=int(row["salary_day"]),
        )
        db.add(customer)
        db.flush()
    return customer


def get_or_create_mandate(db: Session, row: pd.Series, customer: Customer) -> Mandate:
    mandate = (
        db.query(Mandate)
        .filter_by(customer_id=customer.customer_id, mandate_amount=float(row["mandate_amount"]))
        .first()
    )
    if mandate is None:
        mandate = Mandate(
            customer_id=customer.customer_id,
            mandate_name="Subscription",  # generic placeholder; real name would come from merchant data
            mandate_amount=float(row["mandate_amount"]),
            debit_day=int(row["debit_day"]),
        )
        db.add(mandate)
        db.flush()
    return mandate


def process_batch(db: Session, df: pd.DataFrame, generate_messages: bool = False, tone: str = "english") -> dict:
    """
    Runs every row in df through the full pipeline and persists results.
    generate_messages=False skips the (slow, quota-consuming) LLM call and
    only runs risk scoring + rules + triage - useful for large batch runs
    where you want fast, complete decisioning without messaging every case.
    """
    X_scaled = SCALER.transform(df[FEATURES])
    df = df.copy()
    df["risk_score"] = MODEL.predict_proba(X_scaled)[:, 1]

    summary = {"total": len(df), "actions": {}, "failures": 0, "retries_scheduled": 0, "messages_generated": 0}

    for _, row in df.iterrows():
        customer = get_or_create_customer(db, row)
        mandate = get_or_create_mandate(db, row, customer)

        band, action = decide_action(row["risk_score"], row["mandate_amount"])
        reason_code = compute_reason_code(row, MODEL, SCALER, FEATURES) if action.value != "no_action" else None

        event = CycleEvent(
            mandate_id=mandate.mandate_id,
            cycle_number=int(row["cycle"]),
            risk_score=float(row["risk_score"]),
            risk_band=band.value,
            predictive_action=action.value,
            reason_code=reason_code,
            actual_outcome_failed=bool(row["label_failed"]),
        )

        summary["actions"][action.value] = summary["actions"].get(action.value, 0) + 1

        if row["label_failed"]:
            summary["failures"] += 1
            retry_decision = decide_retry(row, attempt_number=1)
            event.triage_category = retry_decision.category.value
            event.retry_scheduled = retry_decision.should_retry
            event.retry_expected_value = retry_decision.expected_value_inr
            if retry_decision.should_retry:
                summary["retries_scheduled"] += 1

        db.add(event)
        db.flush()

        if generate_messages and action.value != "no_action":
            msg_text = generate_message(
                action=action.value, mandate_name=mandate.mandate_name,
                amount=float(mandate.mandate_amount), debit_date="upcoming cycle", tone=tone,
            )
            db.add(Message(event_id=event.event_id, action_type=action.value, tone=tone, message_text=msg_text))
            summary["messages_generated"] += 1

    db.commit()
    return summary