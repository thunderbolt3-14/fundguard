"""
Phase 7: Razorpay test-mode integration.
Proves genuine API connectivity by creating real Plan/Customer/Subscription
objects in Razorpay's test-mode sandbox. This does NOT replace our own
risk model / rules / triage - those remain the actual decision-making
intelligence. This layer proves the payment-platform side is real, not mocked.
"""

import os
from dotenv import load_dotenv
import razorpay

load_dotenv()

client = razorpay.Client(auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]))


def create_plan(amount_inr: float, plan_name: str = "FundGuard Subscription") -> dict:
    plan = client.plan.create({
        "period": "monthly",
        "interval": 1,
        "item": {"name": plan_name, "amount": int(amount_inr * 100), "currency": "INR"},
    })
    return plan


def create_customer(name: str, email: str, contact: str) -> dict:
    customer = client.customer.create({"name": name, "email": email, "contact": contact})
    return customer


def create_subscription(plan_id: str, customer_id: str, total_count: int = 6) -> dict:
    subscription = client.subscription.create({
        "plan_id": plan_id, "customer_notify": 0, "total_count": total_count, "customer_id": customer_id,
    })
    return subscription


def create_real_razorpay_records_for_customer(customer_id: str, mandate_amount: float) -> dict:
    """
    Called from the orchestrator for a specific flagged customer/mandate.
    Uses dummy contact info since this is test mode - no real customer data
    involved, no real notification sent (customer_notify=0).
    """
    plan = create_plan(amount_inr=mandate_amount, plan_name=f"FundGuard Plan - {customer_id}")
    customer = create_customer(
        name=f"FundGuard Test - {customer_id}",
        email=f"{customer_id.lower()}@fundguard-demo.com",
        contact="9999999999",
    )
    subscription = create_subscription(plan_id=plan["id"], customer_id=customer["id"])
    return {
        "razorpay_plan_id": plan["id"],
        "razorpay_customer_id": customer["id"],
        "razorpay_subscription_id": subscription["id"],
        "razorpay_status": subscription["status"],
    }


if __name__ == "__main__":
    result = create_real_razorpay_records_for_customer("C00000", 299)
    print(result)