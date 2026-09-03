"""
Phase 6b/7b/6c: Core orchestration logic - ingests a batch of mandate-cycle rows,
runs the full risk -> rules -> compliance check -> triage -> (optional)
messaging -> (optional) Razorpay integration pipeline, and persists every
decision to Postgres as the audit trail.
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
            mandate_name="Subscription",
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
            return mandate

        if create_razorpay:
            try:
                rzp_result = create_real_razorpay_records_for_customer(
                    customer.customer_id, float(row["mandate_amount"])
                )
                mandate.razorpay_subscription_id = rzp_result["razorpay_subscription_id"]
                db.flush()
            except Exception as e:
                print(f"Razorpay integration failed for {customer.customer_id}: {e}")

    return mandate


def check_compliance(db: Session, customer_id: str, mandate_id: int, cycle_number: int) -> tuple[bool, str | None]:
    """
    Real, DB-backed compliance check (not an in-memory tracker, since that
    can't survive across separate API requests). Two hard stopping rules
    from RBI/NPCI research:
      1. Max one intervention per mandate per cycle
      2. One active pre-debit notice per customer at a time (across all
         their mandates, within the same cycle)
    """
    existing_for_mandate = (
        db.query(CycleEvent)
        .filter(CycleEvent.mandate_id == mandate_id, CycleEvent.cycle_number == cycle_number,
                CycleEvent.predictive_action != "no_action", CycleEvent.blocked_reason.is_(None))
        .first()
    )
    if existing_for_mandate:
        return True, "BLOCKED: max one intervention per mandate per cycle already used"

    existing_for_customer = (
        db.query(CycleEvent)
        .join(Mandate, CycleEvent.mandate_id == Mandate.mandate_id)
        .filter(Mandate.customer_id == customer_id, CycleEvent.cycle_number == cycle_number,
                CycleEvent.predictive_action != "no_action", CycleEvent.blocked_reason.is_(None))
        .first()
    )
    if existing_for_customer:
        return True, "BLOCKED: customer already has an active pre-debit notice this cycle"

    return False, None


def process_batch(db: Session, df: pd.DataFrame, generate_messages: bool = False, tone: str = "english",
                   create_razorpay: bool = False) -> dict:
    X_scaled = SCALER.transform(df[FEATURES])
    df = df.copy()
    df["risk_score"] = MODEL.predict_proba(X_scaled)[:, 1]

    summary = {"total": len(df), "actions": {}, "failures": 0, "retries_scheduled": 0,
               "messages_generated": 0, "razorpay_subscriptions_created": 0, "compliance_blocks": 0}

    for _, row in df.iterrows():
        customer = get_or_create_customer(db, row)
        mandate = get_or_create_mandate(db, row, customer, create_razorpay=create_razorpay)
        if create_razorpay and mandate.razorpay_subscription_id:
            summary["razorpay_subscriptions_created"] += 1

        band, action = decide_action(row["risk_score"], row["mandate_amount"])
        cycle_number = int(row["cycle"])

        blocked_reason = None
        if action.value != "no_action":
            is_blocked, reason = check_compliance(db, customer.customer_id, mandate.mandate_id, cycle_number)
            if is_blocked:
                blocked_reason = reason
                summary["compliance_blocks"] += 1
                action_for_record = "no_action"  # downgraded due to compliance block
            else:
                action_for_record = action.value
        else:
            action_for_record = action.value

        reason_code = compute_reason_code(row, MODEL, SCALER, FEATURES) if (action.value != "no_action" and not blocked_reason) else None

        event = CycleEvent(
            mandate_id=mandate.mandate_id,
            cycle_number=cycle_number,
            risk_score=float(row["risk_score"]),
            risk_band=band.value,
            predictive_action=action_for_record,
            reason_code=reason_code,
            blocked_reason=blocked_reason,
            actual_outcome_failed=bool(row["label_failed"]),
        )

        summary["actions"][action_for_record] = summary["actions"].get(action_for_record, 0) + 1

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

        MAX_MESSAGES_PER_BATCH = 2  # stays safely under Gemini free tier's 5-requests-per-minute limit
        if generate_messages and action_for_record != "no_action" and summary["messages_generated"] < MAX_MESSAGES_PER_BATCH:
            try:
                msg_text = generate_message(
                    action=action_for_record, mandate_name=mandate.mandate_name,
                    amount=float(mandate.mandate_amount), debit_date="upcoming cycle", tone=tone,
                )
                db.add(Message(event_id=event.event_id, action_type=action_for_record, tone=tone, message_text=msg_text))
                summary["messages_generated"] += 1
            except Exception as e:
                # Gemini free-tier rate limits (5 req/min) or transient API
                # errors shouldn't crash the whole batch - the risk/rules/
                # triage pipeline is independent of the messaging layer and
                # should keep running even if message generation fails.
                print(f"Message generation failed for event {event.event_id}: {e}")

    db.commit()
    return summary