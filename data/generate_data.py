"""
MeraFraud - Synthetic Training Data Generator
------------------------------------------------
Generates a realistic (synthetic) e-commerce transaction dataset that blends
payment-level signals (amount, payment method, velocity) with behavioral
signals (device, session, account history). Fraud is injected with realistic
correlations rather than pure randomness, so the downstream model learns
meaningful patterns instead of memorizing noise.

NOTE: This is SYNTHETIC data for demo/prototype purposes. Before production
use, MeraFraud should be retrained on real (anonymized, consented) merchant
transaction data.
"""

import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)

FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}


def generate_dataset(n_samples: int = 60_000, fraud_rate: float = 0.028) -> pd.DataFrame:
    n_fraud = int(n_samples * fraud_rate)
    n_legit = n_samples - n_fraud

    def base_block(n, fraud: bool):
        # Account & customer history -------------------------------------------------
        account_age_days = RNG.exponential(400, n) if not fraud else RNG.exponential(60, n)
        account_age_days = np.clip(account_age_days, 0, 3650)

        customer_ltv = RNG.gamma(2, 80, n) if not fraud else RNG.gamma(1, 20, n)

        # Transaction -----------------------------------------------------------------
        base_amount = RNG.lognormal(mean=3.6, sigma=0.9, size=n)
        transaction_amount = base_amount * (1.8 if fraud else 1.0)
        transaction_amount = np.clip(transaction_amount, 1, 20000)

        avg_hist_amount = np.clip(base_amount * RNG.normal(1.0, 0.3, n), 1, None)
        amount_ratio_to_avg = transaction_amount / avg_hist_amount

        hour_of_day = (
            RNG.choice(range(24), n, p=_night_skewed_probs()) if fraud
            else RNG.choice(range(24), n, p=_day_skewed_probs())
        )

        # Velocity / behavior -----------------------------------------------------------
        num_tx_last_24h = RNG.poisson(4 if fraud else 1.1, n)
        num_failed_payments_7d = RNG.poisson(1.8 if fraud else 0.15, n)
        login_attempts_before_purchase = RNG.poisson(3.5 if fraud else 1.2, n)
        time_since_last_tx_min = RNG.exponential(8 if fraud else 240, n)

        # Identity / device signals ------------------------------------------------------
        billing_shipping_mismatch = RNG.binomial(1, 0.55 if fraud else 0.04, n)
        ip_billing_country_mismatch = RNG.binomial(1, 0.5 if fraud else 0.03, n)
        new_device = RNG.binomial(1, 0.7 if fraud else 0.12, n)
        new_payment_method = RNG.binomial(1, 0.65 if fraud else 0.15, n)
        free_email_domain = RNG.binomial(1, 0.8 if fraud else 0.45, n)

        # Cart / order ------------------------------------------------------------------
        num_items_in_cart = RNG.poisson(1.5 if fraud else 2.8, n) + 1
        express_shipping = RNG.binomial(1, 0.6 if fraud else 0.2, n)

        return pd.DataFrame({
            "account_age_days": account_age_days,
            "customer_ltv": customer_ltv,
            "transaction_amount": transaction_amount,
            "amount_ratio_to_avg": amount_ratio_to_avg,
            "hour_of_day": hour_of_day,
            "num_tx_last_24h": num_tx_last_24h,
            "num_failed_payments_7d": num_failed_payments_7d,
            "login_attempts_before_purchase": login_attempts_before_purchase,
            "time_since_last_tx_min": time_since_last_tx_min,
            "billing_shipping_mismatch": billing_shipping_mismatch,
            "ip_billing_country_mismatch": ip_billing_country_mismatch,
            "new_device": new_device,
            "new_payment_method": new_payment_method,
            "free_email_domain": free_email_domain,
            "num_items_in_cart": num_items_in_cart,
            "express_shipping": express_shipping,
            "is_fraud": int(fraud),
        })

    df_legit = base_block(n_legit, fraud=False)
    df_fraud = base_block(n_fraud, fraud=True)
    df = pd.concat([df_legit, df_fraud], ignore_index=True)

    # small label noise to simulate real-world imperfect labeling
    flip_mask = RNG.random(len(df)) < 0.01
    df.loc[flip_mask, "is_fraud"] = 1 - df.loc[flip_mask, "is_fraud"]

    df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    return df


def _night_skewed_probs():
    hours = np.arange(24)
    weights = np.where((hours >= 0) & (hours <= 5), 4.0, 1.0)
    return weights / weights.sum()


def _day_skewed_probs():
    hours = np.arange(24)
    weights = np.where((hours >= 9) & (hours <= 22), 1.5, 0.5)
    return weights / weights.sum()


if __name__ == "__main__":
    df = generate_dataset()
    out_path = "/home/claude/merafraud/data/transactions.csv"
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df):,} rows to {out_path}")
    print(f"Fraud rate: {df['is_fraud'].mean():.3%}")
    print(df.describe().T)
