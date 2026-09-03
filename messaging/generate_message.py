"""
Phase 5: LLM messaging layer - generates customer-facing text for each
bounded action type decided by rules/rule_engine.py and rules/reactive_triage.py.
This is deliberately the ONLY place in the system that calls an LLM - every
upstream decision (risk score, action, retry timing, triage category) is
already made by deterministic rules/ML before this layer runs. The LLM's job
is purely to phrase the message well, not to decide anything.
"""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODEL = "gemini-3.6-flash"

# System-level constraints the LLM must never violate, regardless of what
# it's asked to write. This is the "constrained output" guardrail - the
# model can choose words, but not make commitments the business hasn't
# authorized (e.g. it must never promise a refund, guarantee an outcome,
# or imply a threat/penalty that isn't real).
SYSTEM_INSTRUCTION = """You are writing short customer notification messages for
FundGuard, a payment-recovery assistant for Indian subscription businesses.

Hard rules, never break these:
- Never promise a refund, discount, or guarantee of any kind
- Never threaten the customer (no "you will be penalized", no legal language)
- Never fabricate specific dates, amounts, or account details beyond what is given to you
- Keep messages under 400 characters (app notification / WhatsApp length, not strict SMS)
- Be warm and helpful, never robotic or accusatory
- Do not use exclamation marks more than once per message
- Output ONLY the final message text itself. Do not include headers, labels,
  notes about your approach, or any text other than the message the customer
  will actually see.
"""

ACTION_PROMPTS = {
    "standard_nudge": (
        "Write a friendly reminder that the customer's {mandate_name} payment "
        "of INR {amount} is coming up on {debit_date}, and their balance might "
        "be low around that time. Gently suggest checking their balance or "
        "adding funds before then."
    ),
    "date_shift_offer": (
        "Write a message offering to shift the customer's {mandate_name} "
        "payment date from {debit_date} to a few days later, since their "
        "balance is often lower around the current debit date. Ask them to "
        "confirm if they'd like the shift."
    ),
    "payment_fallback_suggestion": (
        "Write a message suggesting the customer add a backup payment method "
        "for their {mandate_name} subscription (INR {amount}/cycle), in case "
        "their primary UPI mandate doesn't go through this cycle."
    ),
    "fallback_and_shift": (
        "Write a message combining two suggestions: shifting the {mandate_name} "
        "payment date from {debit_date} to a few days later, AND adding a "
        "backup payment method, since there's a meaningful chance the INR "
        "{amount} payment may not go through as currently scheduled."
    ),
    "silent_churn_winback": (
        "Write a soft, no-pressure message to a customer whose {mandate_name} "
        "payment has failed multiple times recently. Don't push for another "
        "payment attempt. Instead, gently ask if they'd like to pause the "
        "subscription instead of it failing repeatedly, or if there's anything "
        "wrong they'd like to flag."
    ),
    "retry_notice": (
        "Write a message letting the customer know their {mandate_name} "
        "payment of INR {amount} didn't go through, and it will be "
        "automatically retried on {debit_date}. Keep it low-key, not alarming."
    ),
}


def generate_message(action: str, mandate_name: str, amount: float, debit_date: str,
                      tone: str = "english") -> str:
    """
    tone: "english" or "hinglish" - Hinglish support per the buildathon's own
    example direction ("Hinglish voice recovery").
    """
    if action not in ACTION_PROMPTS:
        raise ValueError(f"Unknown action type: {action}")

    prompt = ACTION_PROMPTS[action].format(
        mandate_name=mandate_name, amount=f"{amount:.0f}", debit_date=debit_date
    )

    if tone == "hinglish":
        prompt += (
            "\n\nWrite this in casual Hinglish (Hindi-English code-mixed, as "
            "commonly used in Indian customer messaging - e.g. 'Aapka payment "
            "abhi due hai' style), not pure Hindi or pure English."
        )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            max_output_tokens=1024,
            temperature=0.7,
            http_options=genai.types.HttpOptions(retry_options=genai.types.HttpRetryOptions(attempts=1)),
        ),
    )
    return response.text.strip()


if __name__ == "__main__":
    test_cases = [
        ("standard_nudge", "Netflix", 499, "3rd Sept", "english"),
        ("date_shift_offer", "Spotify Premium", 119, "5th Sept", "english"),
        ("silent_churn_winback", "Gym Membership", 1200, "1st Sept", "english"),
        ("standard_nudge", "Netflix", 499, "3rd Sept", "hinglish"),
        ("fallback_and_shift", "Amazon Prime", 299, "8th Sept", "hinglish"),
    ]

    for action, name, amt, date, tone in test_cases:
        print(f"\n--- {action} ({tone}) ---")
        msg = generate_message(action, name, amt, date, tone)
        print(msg)
        print(f"[{len(msg)} chars]")