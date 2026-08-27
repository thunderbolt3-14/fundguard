"""
Phase 4: Reactive triage and recovery-ROI layer, for mandates that fail
despite Phase 3's predictive intervention (or that were never flagged).
Deterministic throughout - triage rules and ROI math, no ML/LLM. Uses real
NPCI retry constraints (1 original + 3 retries max, staggered 24h/72h/168h
windows) as hard limits, not internal assumptions.
"""

from dataclasses import dataclass
from enum import Enum
import pandas as pd
import joblib


class TriageCategory(Enum):
    RETRY_WORTHY_MARGINAL = "retry_worthy_marginal"
    RETRY_WORTHY_SEVERE = "retry_worthy_severe"
    SILENT_CHURN = "silent_churn"
    CARD_LIFECYCLE = "card_lifecycle"  # not applicable to pure UPI Autopay - kept for extensibility


# NPCI hard compliance cap (2025 rule change): 1 original execution + 3 retries.
NPCI_MAX_RETRIES = 3
# Recommended staggered retry windows, in hours after the original failure -
# NOT same-day/rapid-fire, both to respect NPCI rate limits and because a
# same-day retry rarely helps (the underlying balance shortfall hasn't changed).
RETRY_WINDOWS_HOURS = [24, 72, 168]

# Rough per-attempt costs. Gateway/processing cost is small and roughly flat;
# friction cost models eroding customer goodwill with each additional attempt
# (each unsuccessful retry is itself a mildly annoying notification).
RETRY_PROCESSING_COST_INR = 2.0
FRICTION_COST_PER_ATTEMPT_INR = 5.0

SILENT_CHURN_THRESHOLD = 0.5  # recent_failure_rate >= this -> treat as disengaging, not cash-short
MARGINAL_SHORTFALL_RATIO = 0.5  # true_balance / mandate_amount >= this -> "marginal" not "severe"


def classify_failure(row: pd.Series) -> TriageCategory:
    if row["recent_failure_rate"] >= SILENT_CHURN_THRESHOLD:
        return TriageCategory.SILENT_CHURN

    shortfall_ratio = row["true_balance_at_debit"] / row["mandate_amount"]
    if shortfall_ratio >= MARGINAL_SHORTFALL_RATIO:
        return TriageCategory.RETRY_WORTHY_MARGINAL
    else:
        return TriageCategory.RETRY_WORTHY_SEVERE


def estimate_retry_success_probability(category: TriageCategory, days_since_salary_at_debit: float,
                                        retry_offset_days: int) -> float:
    """
    Heuristic, not ML - deliberately kept simple and explainable. Base rate by
    triage category, boosted if the retry lands close to a fresh salary cycle
    (the whole thesis of this project: timing matters more than persistence).
    """
    base_rates = {
        TriageCategory.RETRY_WORTHY_MARGINAL: 0.60,
        TriageCategory.RETRY_WORTHY_SEVERE: 0.35,
        TriageCategory.SILENT_CHURN: 0.10,
        TriageCategory.CARD_LIFECYCLE: 0.0,  # not applicable - never scheduled
    }
    prob = base_rates[category]

    # Days since salary at the moment of retry (cyclical, ~28-day month)
    days_since_salary_at_retry = (days_since_salary_at_debit + retry_offset_days) % 28
    if days_since_salary_at_retry <= 5:
        prob += 0.15  # retry lands in the "flush" post-salary window

    return min(max(prob, 0.02), 0.95)


@dataclass
class RetryDecision:
    should_retry: bool
    category: TriageCategory
    attempt_number: int
    scheduled_offset_hours: int | None
    expected_value_inr: float
    reasoning: str


def decide_retry(row: pd.Series, attempt_number: int = 1) -> RetryDecision:
    category = classify_failure(row)

    if attempt_number > NPCI_MAX_RETRIES:
        return RetryDecision(False, category, attempt_number, None, 0.0,
                              "BLOCKED: NPCI hard cap of 3 retries reached - mandate marked failed for this cycle")

    if category == TriageCategory.CARD_LIFECYCLE:
        return RetryDecision(False, category, attempt_number, None, 0.0,
                              "Not applicable - no card lifecycle event possible in pure UPI Autopay flow")

    if category == TriageCategory.SILENT_CHURN:
        # Deliberate categorical override, not an ROI input: retrying a
        # customer who's likely disengaging risks real goodwill/reputational
        # cost that a small per-attempt INR fee doesn't capture. Route to
        # soft win-back messaging instead of a payment retry, unconditionally.
        return RetryDecision(False, category, attempt_number, None, 0.0,
                              "SILENT_CHURN: retrying risks alienating a disengaging customer - "
                              "routed to soft win-back messaging instead of a payment retry")

    retry_offset_days = RETRY_WINDOWS_HOURS[attempt_number - 1] / 24
    p_success = estimate_retry_success_probability(category, row["days_since_salary_at_debit"], retry_offset_days)

    expected_recovery = p_success * row["mandate_amount"]
    cost = RETRY_PROCESSING_COST_INR + (FRICTION_COST_PER_ATTEMPT_INR * attempt_number)
    net_value = expected_recovery - cost

    if net_value <= 0:
        return RetryDecision(False, category, attempt_number, None, net_value,
                              f"ROI negative (expected recovery {expected_recovery:.2f} INR vs cost {cost:.2f} INR) - "
                              f"not worth retrying despite NPCI allowing it; switch to soft win-back messaging")

    return RetryDecision(True, category, attempt_number, RETRY_WINDOWS_HOURS[attempt_number - 1], net_value,
                          f"Retry scheduled: {p_success:.0%} estimated success probability, "
                          f"expected net value {net_value:.2f} INR")

# ============================================================
# DEMO: run triage over all failed mandates in the test batch
# ============================================================
if __name__ == "__main__":
    df = pd.read_csv("./data/synthetic_mandates.csv")
    saved = joblib.load("./model/risk_model.joblib")
    features = saved["features"]

    from sklearn.model_selection import GroupShuffleSplit
    groups = df["customer_id"]
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    _, test_idx = next(splitter.split(df[features], df["label_failed"], groups))
    batch = df.iloc[test_idx].copy()

    failed = batch[batch["label_failed"] == 1].copy()
    print(f"Total failed mandates in test batch: {len(failed)}")

    decisions = failed.apply(lambda row: decide_retry(row, attempt_number=1), axis=1)
    failed["triage_category"] = [d.category.value for d in decisions]
    failed["should_retry"] = [d.should_retry for d in decisions]
    failed["expected_value"] = [d.expected_value_inr for d in decisions]

    print("\n=== TRIAGE CATEGORY DISTRIBUTION ===")
    print(failed["triage_category"].value_counts())

    print("\n=== RETRY DECISION (first attempt) ===")
    print(failed["should_retry"].value_counts())

    print("\n=== SAMPLE DECISIONS ===")
    for i, (_, row) in enumerate(failed.head(6).iterrows()):
        d = decide_retry(row, attempt_number=1)
        print(f"{row['customer_id']} | {d.category.value} | retry={d.should_retry}")
        print(f"  {d.reasoning}")