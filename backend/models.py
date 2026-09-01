"""
SQLAlchemy ORM models, mirroring backend/db_schema.sql exactly.
"""

from sqlalchemy import Column, Integer, String, Numeric, SmallInteger, Boolean, Text, TIMESTAMP, ForeignKey
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Customer(Base):
    __tablename__ = "customers"
    customer_id = Column(String(20), primary_key=True)
    baseline_balance = Column(Numeric(12, 2), nullable=False)
    balance_volatility = Column(Numeric(6, 4), nullable=False)
    salary_day = Column(SmallInteger, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Mandate(Base):
    __tablename__ = "mandates"
    mandate_id = Column(Integer, primary_key=True)
    customer_id = Column(String(20), ForeignKey("customers.customer_id"), nullable=False)
    mandate_name = Column(String(100), nullable=False)
    mandate_amount = Column(Numeric(12, 2), nullable=False)
    debit_day = Column(SmallInteger, nullable=False)
    razorpay_subscription_id = Column(String(50))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class CycleEvent(Base):
    __tablename__ = "cycle_events"
    event_id = Column(Integer, primary_key=True)
    mandate_id = Column(Integer, ForeignKey("mandates.mandate_id"), nullable=False)
    cycle_number = Column(SmallInteger, nullable=False)
    risk_score = Column(Numeric(6, 4), nullable=False)
    risk_band = Column(String(20), nullable=False)
    predictive_action = Column(String(50), nullable=False)
    reason_code = Column(Text)
    actual_outcome_failed = Column(Boolean, nullable=False)
    triage_category = Column(String(50))
    retry_scheduled = Column(Boolean)
    retry_expected_value = Column(Numeric(10, 2))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    message_id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("cycle_events.event_id"), nullable=False)
    action_type = Column(String(50), nullable=False)
    tone = Column(String(20), nullable=False)
    message_text = Column(Text, nullable=False)
    generated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())