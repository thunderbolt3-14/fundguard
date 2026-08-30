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
    """Amount must be in paise (INR * 100) per Razorpay's API convention."""
    plan = client.plan.create({
        "period": "monthly",
        "interval": 1,
        "item": {
            "name": plan_name,
            "amount": int(amount_inr * 100),
            "currency": "INR",
        },
    })
    return plan


def create_customer(name: str, email: str, contact: str) -> dict:
    customer = client.customer.create({
        "name": name,
        "email": email,
        "contact": contact,
    })
    return customer


def create_subscription(plan_id: str, customer_id: str, total_count: int = 6) -> dict:
    subscription = client.subscription.create({
        "plan_id": plan_id,
        "customer_notify": 0,  # don't spam a real inbox in test mode
        "total_count": total_count,
        "customer_id": customer_id,
    })
    return subscription


if __name__ == "__main__":
    print("Creating test plan...")
    plan = create_plan(amount_inr=299, plan_name="FundGuard Test Plan - C00000")
    print(f"Plan created: {plan['id']}")

    print("\nCreating test customer...")
    customer = create_customer(name="Test Customer C00000", email="test.c00000@fundguard-demo.com", contact="9999999999")
    print(f"Customer created: {customer['id']}")

    print("\nCreating test subscription...")
    subscription = create_subscription(plan_id=plan["id"], customer_id=customer["id"])
    print(f"Subscription created: {subscription['id']}, status: {subscription['status']}")