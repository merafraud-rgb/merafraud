"""
MeraFraud - Model Training
------------------------------
Trains a RandomForest classifier on the synthetic transaction dataset and
persists the model, feature order, and evaluation metrics.

Why RandomForest for the MVP:
- Handles mixed numeric/binary features without scaling
- Gives feature_importances_ out of the box -> feeds the "why was this
  flagged" explanation in the API response, which matters a lot for a
  fraud-review UI (analysts need a reason, not just a number)
- Fast to train/retrain as new labeled data comes in from pilot merchants
"""

import json
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    roc_auc_score, precision_recall_curve, average_precision_score,
    classification_report, confusion_matrix
)

DATA_PATH = "/home/claude/merafraud/data/transactions.csv"
MODEL_PATH = "/home/claude/merafraud/model/merafraud_model.pkl"
META_PATH = "/home/claude/merafraud/model/model_meta.json"

FEATURE_COLUMNS = [
    "account_age_days", "customer_ltv", "transaction_amount", "amount_ratio_to_avg",
    "hour_of_day", "num_tx_last_24h", "num_failed_payments_7d",
    "login_attempts_before_purchase", "time_since_last_tx_min",
    "billing_shipping_mismatch", "ip_billing_country_mismatch", "new_device",
    "new_payment_method", "free_email_domain", "num_items_in_cart", "express_shipping",
]


def main():
    df = pd.read_csv(DATA_PATH)
    X = df[FEATURE_COLUMNS]
    y = df["is_fraud"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=5,
        class_weight="balanced_subsample",  # fraud is rare -> reweight
        n_jobs=-1,
        random_state=42,
    )
    model.fit(X_train, y_train)

    probs = model.predict_proba(X_test)[:, 1]
    preds = (probs >= 0.5).astype(int)

    auc = roc_auc_score(y_test, probs)
    ap = average_precision_score(y_test, probs)
    report = classification_report(y_test, preds, output_dict=True)
    cm = confusion_matrix(y_test, preds).tolist()

    print(f"ROC-AUC: {auc:.4f}")
    print(f"Average Precision (PR-AUC): {ap:.4f}")
    print(classification_report(y_test, preds))
    print("Confusion matrix:", cm)

    importances = dict(zip(FEATURE_COLUMNS, model.feature_importances_.tolist()))
    importances = dict(sorted(importances.items(), key=lambda kv: -kv[1]))

    joblib.dump(model, MODEL_PATH)

    meta = {
        "feature_columns": FEATURE_COLUMNS,
        "metrics": {
            "roc_auc": auc,
            "average_precision": ap,
            "confusion_matrix": cm,
            "classification_report": report,
        },
        "feature_importances": importances,
        "training_rows": len(df),
        "fraud_rate": float(y.mean()),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nModel saved to {MODEL_PATH}")
    print(f"Metadata saved to {META_PATH}")
    print("\nTop feature importances:")
    for feat, imp in list(importances.items())[:8]:
        print(f"  {feat:35s} {imp:.4f}")


if __name__ == "__main__":
    main()
