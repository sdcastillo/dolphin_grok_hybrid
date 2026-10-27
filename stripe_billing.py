"""Stripe Checkout for Dolphin access. Inactive until STRIPE_SECRET_KEY is set."""

from __future__ import annotations

import os


def configured() -> bool:
    return bool((os.getenv("STRIPE_SECRET_KEY") or "").strip())


def _stripe():
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    return stripe


def create_checkout_session(email: str | None = None) -> dict:
    stripe = _stripe()
    amount = int(os.getenv("STRIPE_AMOUNT_CENTS", "2900"))
    currency = os.getenv("STRIPE_CURRENCY", "usd")
    name = os.getenv("STRIPE_PRODUCT_NAME", "Dolphin AI Access (Tailscale)")
    success = os.getenv("STRIPE_SUCCESS_URL", "http://127.0.0.1:8000/onboarding?session_id={CHECKOUT_SESSION_ID}")
    cancel = os.getenv("STRIPE_CANCEL_URL", "http://127.0.0.1:8000/pay")
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=email or None,
        client_reference_id=email or None,
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount,
                    "product_data": {"name": name},
                },
            }
        ],
        success_url=success,
        cancel_url=cancel,
    )
    return {"id": session["id"], "url": session["url"]}


def construct_event(payload: bytes, signature: str):
    stripe = _stripe()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET") or ""
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    return stripe.Webhook.construct_event(payload, signature, secret)
