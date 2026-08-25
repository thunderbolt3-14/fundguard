"""
Phase 2: Risk model - logistic regression trained on the synthetic mandate dataset.
Split by customer_id (not by row) to prevent leakage across a customer's cycles.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    precision_score, recall_score, roc_auc_score,
    confusion_matrix, classification_report
)
import joblib

df = pd.read_csv("./data/synthetic_mandates.csv")

FEATURES = [
    "mandate_amount",
    "baseline_balance",
    "days_since_salary_at_debit",
    "recent_failure_rate",
    "amount_to_balance_ratio",
    "balance_volatility",
]
TARGET = "label_failed"

X = df[FEATURES]
y = df[TARGET]
groups = df["customer_id"]

splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
train_idx, test_idx = next(splitter.split(X, y, groups))

X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

train_customers = set(groups.iloc[train_idx])
test_customers = set(groups.iloc[test_idx])
overlap = train_customers & test_customers
print(f"Customer overlap between train/test: {len(overlap)} (should be 0)")

scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

model = LogisticRegression(class_weight="balanced", random_state=42, max_iter=1000)
model.fit(X_train_scaled, y_train)

y_pred = model.predict(X_test_scaled)
y_proba = model.predict_proba(X_test_scaled)[:, 1]

print("\n=== EVALUATION (held-out test set, unseen customers) ===")
print(f"Precision: {precision_score(y_test, y_pred):.3f}")
print(f"Recall:    {recall_score(y_test, y_pred):.3f}")
print(f"AUC:       {roc_auc_score(y_test, y_proba):.3f}")
print("\nConfusion matrix:")
print(confusion_matrix(y_test, y_pred))
print("\nFull report:")
print(classification_report(y_test, y_pred, target_names=["succeeded", "failed"]))

print("\n=== FEATURE COEFFICIENTS (standardized - larger magnitude = stronger effect) ===")
for feat, coef in sorted(zip(FEATURES, model.coef_[0]), key=lambda x: -abs(x[1])):
    print(f"  {feat}: {coef:+.3f}")

joblib.dump({"model": model, "scaler": scaler, "features": FEATURES}, "./model/risk_model.joblib")
print("\nModel saved to ./model/risk_model.joblib")