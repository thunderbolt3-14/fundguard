"""
Phase 3 prep: examine the risk model's actual score distribution on the test
set, to calibrate risk bands from real data rather than an arbitrary cutoff.
"""

import pandas as pd
import joblib
from sklearn.model_selection import GroupShuffleSplit

df = pd.read_csv("./data/synthetic_mandates.csv")
saved = joblib.load("./model/risk_model.joblib")
model, scaler, features = saved["model"], saved["scaler"], saved["features"]

groups = df["customer_id"]
splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
_, test_idx = next(splitter.split(df[features], df["label_failed"], groups))
test_df = df.iloc[test_idx].copy()

X_test_scaled = scaler.transform(test_df[features])
test_df["risk_score"] = model.predict_proba(X_test_scaled)[:, 1]

print("=== RISK SCORE DISTRIBUTION (test set) ===")
print(test_df["risk_score"].describe(percentiles=[.5, .7, .8, .9, .95]))

print("\n=== ACTUAL FAILURE RATE BY SCORE DECILE ===")
test_df["decile"] = pd.qcut(test_df["risk_score"], 10, labels=False, duplicates="drop")
print(test_df.groupby("decile").agg(
    avg_score=("risk_score", "mean"),
    actual_failure_rate=("label_failed", "mean"),
    count=("label_failed", "size")
))