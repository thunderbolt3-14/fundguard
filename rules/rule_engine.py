"""
Phase 3: Deterministic rule engine - risk score -> bounded action.
Everything in this file is intentionally NOT machine-learned: risk bands,
action mapping, cost gating, and compliance stopping rules are all fixed,
auditable logic. This is deliberate - these are known constraints (NPCI/RBI
rules, business policy), not probabilistic judgment calls, so a rule engine
is the right tool, not an LLM or a second model.
"""

from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import joblib


# ============================================================
# RISK BANDS - calibrated from actual failure-rate-by-decile
# analysis on the held-out test set (see calibrate_thresholds.py).
# Baseline population failure rate is ~18%; bands are set relative
# to that, not arbitrary round numbers.
# ============================================================
class RiskBand(Enum):
    LOW = "low"           # < 0.48  -> ~10-16% actual failure rate (near/below baseline)
    MEDIUM = "medium"     # 0.48-0.58 -> ~19-25% (above baseline)
    HIGH = "high"         # 0.58-0.71 -> ~26-33% (meaningfully elevated)
    CRITICAL = "critical" # >= 0.71 -> ~39% (>2x baseline, top 5% of scores)


def get_risk_band(risk_score: float) -> RiskBand:
    if risk_score >= 0.71:
        return RiskBand.CRITICAL
    elif risk_score >= 0.58:
        return RiskBand.HIGH
    elif risk_score >= 0.48:
        return RiskBand.MEDIUM
    else:
        return RiskBand.LOW


# ============================================================
# BOUNDED ACTION SET - the only things the system is allowed to do.
# Ordered roughly by increasing customer friction / intervention cost.
# ============================================================
class Action(Enum):
    NO_ACTION = "no_action"
    STANDARD_NUDGE = "standard_nudge"
    DATE_SHIFT_OFFER = "date_shift_offer"
    PAYMENT_FALLBACK_SUGGESTION = "payment_fallback_suggestion"
    FALLBACK_AND_SHIFT = "fallback_and_shift"


# Mandate value above which the extra friction of a stronger action
# (date-shift / fallback, both require active customer response) is
# judged worth it. Derived from the real amount distribution: the 75th
# percentile of real transaction amounts was ~1200 INR; we use a similar
# order-of-magnitude cutoff here as a defensible "worth actively saving" line.
HIGH_VALUE_MANDATE_THRESHOLD = 300.0  # INR


def decide_action(risk_score: float, mandate_amount: float) -> tuple[RiskBand, Action]:
    """
    Cost-aware action escalation: action severity depends on BOTH risk band
    and mandate value, not risk score alone. A low-value mandate at CRITICAL
    risk still only gets a nudge - the friction of a stronger action isn't
    worth spending on a mandate that isn't worth much even if saved.
    """
    band = get_risk_band(risk_score)
    is_high_value = mandate_amount >= HIGH_VALUE_MANDATE_THRESHOLD

    if band == RiskBand.LOW:
        action = Action.NO_ACTION
    elif band == RiskBand.MEDIUM:
        action = Action.STANDARD_NUDGE
    elif band == RiskBand.HIGH:
        action = Action.DATE_SHIFT_OFFER if is_high_value else Action.STANDARD_NUDGE
    else:  # CRITICAL
        action = Action.FALLBACK_AND_SHIFT if is_high_value else Action.DATE_SHIFT_OFFER

    return band, action


# ============================================================
# DECLINE-REASON LOOKUP - static reference table, deterministic.
# Not used for the predictive (pre-debit) path, but shared here since
# it's part of the same "known, finite taxonomy = rules, not ML" family.
# Used by the reactive triage layer (Phase 4) once a debit has actually
# failed and a real gateway/bank decline code is available.
# ============================================================
DECLINE_REASON_LOOKUP = {
    "INSUFFICIENT_BALANCE": "Customer's account balance was below the mandate amount at debit time.",
    "MANDATE_LIMIT_EXCEEDED": "Debit amount or frequency exceeded the mandate's registered limits.",
    "INCORRECT_BENEFICIARY": "Beneficiary/UPI handle details on the mandate are stale or invalid.",
    "MANDATE_REVOKED": "Customer revoked the mandate directly with their bank/UPI app.",
    "BANK_DOWNTIME": "Issuing bank or UPI infrastructure was unavailable at debit time.",
    "CARD_EXPIRED": "Underlying card (for card-based mandates) has expired or been reissued.",
}


# ============================================================
# COMPLIANCE / STOPPING RULES - stateful, enforced regardless of model output.
# Sourced from real constraints found during research:
#   - one active pre-debit notification per customer at a time
#   - mandatory 24-48h pre-debit notice window (RBI E-Mandate Framework, 2026)
#   - max one intervention per mandate per cycle (our own bounded-workflow policy)
# ============================================================
@dataclass
class ComplianceTracker:
    """Tracks per-customer/per-mandate state to enforce hard stopping rules.
    In production this would be backed by the audit-log DB table (Phase 6);
    here it's an in-memory simulation for the batch-scoring demo."""
    active_notice_customers: set = field(default_factory=set)
    intervention_count_this_cycle: dict = field(default_factory=dict)

    def can_intervene(self, customer_id: str, cycle: int) -> tuple[bool, str]:
        if customer_id in self.active_notice_customers:
            return False, "BLOCKED: customer already has an active pre-debit notice"
        if self.intervention_count_this_cycle.get((customer_id, cycle), 0) >= 1:
            return False, "BLOCKED: max one intervention per mandate per cycle already used"
        return True, "OK"

    def register_intervention(self, customer_id: str, cycle: int):
        self.active_notice_customers.add(customer_id)
        key = (customer_id, cycle)
        self.intervention_count_this_cycle[key] = self.intervention_count_this_cycle.get(key, 0) + 1

    def clear_notice(self, customer_id: str):
        """Call once the pre-debit window closes (24-48h elapsed)."""
        self.active_notice_customers.discard(customer_id)


# ============================================================
# DETERMINISTIC REASON CODES - explain *why* a mandate was flagged, using
# the model's own learned coefficients against this customer's actual
# standardized feature values. Pure arithmetic, no LLM.
# ============================================================
def compute_reason_code(feature_row: pd.Series, model, scaler, features: list[str], top_n: int = 2) -> str:
    row_df = feature_row[features].to_frame().T  # keep as DataFrame with column names, avoids sklearn warning
    scaled = scaler.transform(row_df)[0]
    contributions = scaled * model.coef_[0]
    ranked = sorted(zip(features, contributions), key=lambda x: -abs(x[1]))[:top_n]

    readable = {
        "balance_volatility": "high balance volatility",
        "amount_to_balance_ratio": "mandate amount is large relative to typical balance",
        "baseline_balance": "low typical account balance",
        "mandate_amount": "high mandate amount",
        "days_since_salary_at_debit": "debit falls late in the salary cycle",
        "recent_failure_rate": "recent history of failed debits",
    }

    parts = [f"{readable.get(f, f)} ({c:+.2f})" for f, c in ranked]
    return "Flagged primarily due to: " + "; ".join(parts)


# ============================================================
# DEMO: run the full Phase 3 pipeline over the test set batch
# ============================================================
if __name__ == "__main__":
    df = pd.read_csv("./data/synthetic_mandates.csv")
    saved = joblib.load("./model/risk_model.joblib")
    model, scaler, features = saved["model"], saved["scaler"], saved["features"]

    from sklearn.model_selection import GroupShuffleSplit
    groups = df["customer_id"]
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    _, test_idx = next(splitter.split(df[features], df["label_failed"], groups))
    batch = df.iloc[test_idx].copy().reset_index(drop=True)

    X_scaled = scaler.transform(batch[features])
    batch["risk_score"] = model.predict_proba(X_scaled)[:, 1]

    # Process in cycle order, clearing notices at each new cycle boundary -
    # the 24-48h pre-debit window closes well before the next month's cycle,
    # so "one active notice" should only block within the SAME cycle, not
    # carry over indefinitely (this was the bug in the first run).
    batch = batch.sort_values("cycle").reset_index(drop=True)
    tracker = ComplianceTracker()
    results = []
    current_cycle = None

    for _, row in batch.iterrows():
        if row["cycle"] != current_cycle:
            tracker.active_notice_customers.clear()
            current_cycle = row["cycle"]

        band, action = decide_action(row["risk_score"], row["mandate_amount"])

        if action == Action.NO_ACTION:
            results.append({"customer_id": row["customer_id"], "band": band.value,
                             "action": action.value, "blocked_reason": None, "reason_code": None})
            continue

        can_act, block_reason = tracker.can_intervene(row["customer_id"], row["cycle"])
        if not can_act:
            results.append({"customer_id": row["customer_id"], "band": band.value,
                             "action": "no_action", "blocked_reason": block_reason, "reason_code": None})
            continue

        reason_code = compute_reason_code(row, model, scaler, features)
        tracker.register_intervention(row["customer_id"], row["cycle"])
        results.append({"customer_id": row["customer_id"], "band": band.value,
                         "action": action.value, "blocked_reason": None, "reason_code": reason_code})

    results_df = pd.DataFrame(results)

    print("=== ACTION DISTRIBUTION ===")
    print(results_df["action"].value_counts())
    print("\n=== RISK BAND DISTRIBUTION ===")
    print(results_df["band"].value_counts())
    print("\n=== COMPLIANCE BLOCKS (customers who would have gotten a 2nd notice IN THE SAME CYCLE) ===")
    print(results_df["blocked_reason"].value_counts())
    print("\n=== SAMPLE DECISIONS WITH REASON CODES ===")
    sample = results_df[results_df["reason_code"].notna()].head(5)
    for _, r in sample.iterrows():
        print(f"{r['customer_id']} | {r['band']} -> {r['action']}")
        print(f"  {r['reason_code']}")