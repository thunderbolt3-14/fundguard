"""
Phase 6b/7b: Core orchestration logic - ingests a batch of mandate-cycle rows,
runs the full risk -> rules -> triage -> (optional) messaging -> (optional)
Razorpay integration pipeline, and persists every decision to Postgres as
the audit trail.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import joblib
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.models import Customer, Mandate, CycleEvent, Message
from rules.rule_engine import decide_action, compute_reason_code
from rules.reactive_triage import decide_retry
from messaging.generate_message import generate_message
from razorpay_integration.create_test_subscription import create_real_razorpay_records_for_customer

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
        try:
            db.flush()
        except IntegrityError:
            # Concurrent request created this customer between our check and
            # our insert (e.g. two overlapping batch runs, such as a double
            # click on the frontend's "run batch" button in Phase 8). Roll
            # back this failed insert and fetch the row the other request
            # just created, instead of crashing the whole request.
            db.rollback()
            customer = db.get(Customer, row["customer_id"])
    return customer


def get_or_create_mandate(db: Session, row: pd.Series, customer: Customer, create_razorpay: bool = False) -> Mandate:
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
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            mandate = (
                db.query(Mandate)
                .filter_by(customer_id=customer.customer_id, mandate_amount=float(row["mandate_amount"]))
                .first()
            )
            return mandate  # skip Razorpay creation this time - the other concurrent request handles it

        if create_razorpay:
            try:
                rzp_result = create_real_razorpay_records_for_customer(
                    customer.customer_id, float(row["mandate_amount"])
                )
                mandate.razorpay_subscription_id = rzp_result["razorpay_subscription_id"]
                db.flush()
            except Exception as e:
                # Razorpay API failures shouldn't crash the whole batch - the
                # core risk/rules/triage pipeline is independent of this
                # integration and should keep running even if Razorpay is
                # unavailable, rate-limited, or a duplicate-customer error
                # occurs (e.g. re-running a batch that overlaps prior test runs).
                print(f"Razorpay integration failed for {customer.customer_id}: {e}")

    return mandate


def process_batch(db: Session, df: pd.DataFrame, generate_messages: bool = False, tone: str = "english",
                   create_razorpay: bool = False) -> dict:
    """
    Runs every row in df through the full pipeline and persists results.
    generate_messages=False skips the (slow, quota-consuming) LLM call.
    create_razorpay=True creates real Razorpay test-mode Plan/Customer/
    Subscription objects for mandates seen for the first time - kept
    optional since it involves real network calls and should typically
    only be used for small demo-sized batches, not bulk synthetic runs.
    """
    X_scaled = SCALER.transform(df[FEATURES])
    df = df.copy()
    df["risk_score"] = MODEL.predict_proba(X_scaled)[:, 1]

    summary = {"total": len(df), "actions": {}, "failures": 0, "retries_scheduled": 0,
               "messages_generated": 0, "razorpay_subscriptions_created": 0}

    for _, row in df.iterrows():
        customer = get_or_create_customer(db, row)
        mandate = get_or_create_mandate(db, row, customer, create_razorpay=create_razorpay)
        if create_razorpay and mandate.razorpay_subscription_id:
            summary["razorpay_subscriptions_created"] += 1

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