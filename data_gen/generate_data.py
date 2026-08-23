"""
Phase 1: Synthetic recurring-mandate dataset generator.
Grounded in real distributions pulled from shivamb/bank-customer-segmentation.
v2: narrowed balance range to subscription-relevant segment, increased volatility
to better match real-world UPI Autopay failure rates (8-15%, per NPCI data).
"""

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)

N_CUSTOMERS = 3000
N_CYCLES = 6

# --- Real distributions, narrowed to the subscription-relevant segment ---
# Full population was 0-100th pctile (up to 11.5L); subscription customers are
# more realistically drawn from the lower-to-middle segment, not high-net-worth
# outliers. Interpolated 10th/75th pctile anchors from the original quartile data.
BALANCE_PCTS = [10, 25, 50, 75]
BALANCE_VALS = [1888, 4721, 16792, 57657]

AMOUNT_PCTS = [10, 25, 50]
AMOUNT_VALS = [50, 161, 459]

CV_PCTS = [10, 25, 50, 75, 90, 100]
CV_VALS = [0.26, 0.64, 1.08, 1.36, 1.41, 2.41]

SALARY_DAY_WEIGHTS = np.array([3 if d <= 7 else (2 if d <= 14 else 1) for d in range(1, 29)])
SALARY_DAY_WEIGHTS = SALARY_DAY_WEIGHTS / SALARY_DAY_WEIGHTS.sum()


def sample_from_percentiles(pcts, vals, n):
    u = rng.uniform(pcts[0], pcts[-1], n)
    return np.interp(u, pcts, vals)


def generate_customers(n):
    baseline_balance = sample_from_percentiles(BALANCE_PCTS, BALANCE_VALS, n)
    baseline_balance = np.clip(baseline_balance, 500, None)
    cv = sample_from_percentiles(CV_PCTS, CV_VALS, n)
    salary_day = rng.choice(np.arange(1, 29), size=n, p=SALARY_DAY_WEIGHTS)
    mandate_amount = sample_from_percentiles(AMOUNT_PCTS, AMOUNT_VALS, n)
    debit_day = rng.integers(1, 29, size=n)

    return pd.DataFrame({
        "customer_id": [f"C{i:05d}" for i in range(n)],
        "baseline_balance": baseline_balance,
        "cv": cv,
        "salary_day": salary_day,
        "mandate_amount": mandate_amount,
        "debit_day": debit_day,
    })


def days_since_salary(debit_day, salary_day):
    return (debit_day - salary_day) % 28


def simulate_true_balance(baseline_balance, mandate_amount, cv, days_since_salary_val):
    """
    GROUND-TRUTH balance simulation - used ONLY to generate labels, never fed
    to the model as a feature.
    """
    depletion_factor = 1 - 0.90 * (days_since_salary_val / 28)
    noise = rng.normal(loc=1.0, scale=cv * 0.55, size=len(baseline_balance))
    noise = np.clip(noise, 0.02, None)
    smooth_balance = baseline_balance * depletion_factor * noise

    # Discrete "bad month" shock, sized relative to the mandate amount itself
    # (guarantees a real shortfall when triggered, unlike v2 which scaled off
    # baseline balance and rarely dipped below a much smaller mandate amount).
    shock_prob = 0.10
    shock_mask = rng.random(len(baseline_balance)) < shock_prob
    shocked_balance = mandate_amount * rng.uniform(0.3, 0.95, size=len(baseline_balance))

    return np.where(shock_mask, shocked_balance, smooth_balance)

def generate_dataset():
    customers = generate_customers(N_CUSTOMERS)
    rows = []
    failure_history = {cid: [] for cid in customers["customer_id"]}

    for cycle in range(1, N_CYCLES + 1):
        dsd = days_since_salary(customers["debit_day"].values, customers["salary_day"].values)
        true_balance = simulate_true_balance(
    customers["baseline_balance"].values, customers["mandate_amount"].values,
    customers["cv"].values, dsd
)

        safety_buffer = rng.normal(loc=1.05, scale=0.1, size=len(customers))
        failed = true_balance < (customers["mandate_amount"].values * safety_buffer)

        for i, cid in enumerate(customers["customer_id"]):
            recent_fails = failure_history[cid][-3:]
            recent_failure_rate = (sum(recent_fails) / len(recent_fails)) if recent_fails else 0.0

            rows.append({
                "customer_id": cid,
                "cycle": cycle,
                "mandate_amount": customers["mandate_amount"].values[i],
                "baseline_balance": customers["baseline_balance"].values[i],
                "days_since_salary_at_debit": dsd[i],
                "recent_failure_rate": recent_failure_rate,
                "amount_to_balance_ratio": customers["mandate_amount"].values[i] / customers["baseline_balance"].values[i],
                "true_balance_at_debit": true_balance[i],  # AUDIT ONLY - drop before training
                "label_failed": int(failed[i]),
            })

            failure_history[cid].append(int(failed[i]))

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_dataset()
    df.to_csv("./data/synthetic_mandates.csv", index=False)
    print(df.shape)
    print(df["label_failed"].value_counts(normalize=True))
    print(df.head(10))