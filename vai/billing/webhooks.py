from __future__ import annotations

import json
from decimal import Decimal

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail

from .models import Subscription, BillingTransaction
from .services import stripe as stripe_svc

WEBHOOK_SECRET = getattr(settings, "STRIPE_WEBHOOK_SECRET", None)

def _cents_to_decimal(cents) -> Decimal:
    return (Decimal(str(cents or 0)) / Decimal("100"))

def _extract_pi_and_charge_from_invoice(inv) -> tuple[str | None, str | None]:
    pi_id = inv.get("payment_intent")
    if isinstance(pi_id, dict):
        pi_id = pi_id.get("id")
    charge_id = inv.get("charge")
    if not charge_id and pi_id:
        try:
            pi = stripe.PaymentIntent.retrieve(pi_id, expand=["charges.data"])
            charges = (pi.get("charges") or {}).get("data") or []
            charge_id = charges[0].get("id") if charges else None
        except Exception:
            charge_id = None
    return pi_id, charge_id

def _kind_from_billing_reason(billing_reason: str | None) -> str:
    mapping = {
        "subscription_cycle": "renewal",
        "subscription_create": "purchase",
        "subscription_update": "upgrade",
    }
    return mapping.get(str(billing_reason or ""), "purchase")

def _tx_defaults_from_invoice(inv, local_sub: Subscription | None, status: str, *, overwrite_amount_with_paid=False):
    amount_due = _cents_to_decimal(inv.get("amount_due"))
    amount_paid = _cents_to_decimal(inv.get("amount_paid"))
    currency = inv.get("currency") or "usd"
    pi_id, ch_id = _extract_pi_and_charge_from_invoice(inv)
    kind = _kind_from_billing_reason(inv.get("billing_reason"))

    return dict(
        user=(local_sub.user if local_sub else None),
        subscription=local_sub,
        plan_content_type=(local_sub.plan_content_type if local_sub else None),
        plan_object_id=(local_sub.plan_object_id if local_sub else None),
        plan_name=(str(local_sub.plan) if local_sub else ""),
        kind=kind,
        status=status,
        amount=(amount_paid if overwrite_amount_with_paid else amount_due),
        currency=currency,
        stripe_invoice_id=inv.get("id"),
        stripe_invoice_url=inv.get("hosted_invoice_url"),
        stripe_invoice_pdf=inv.get("invoice_pdf"),
        stripe_payment_intent_id=pi_id or None,
        stripe_charge_id=ch_id or None,
        description=(inv.get("number") or f"{kind.title()} invoice"),
        meta={"billing_reason": inv.get("billing_reason")},
    )

def _local_sub_for_invoice(inv):
    sub_id = inv.get("subscription")
    if not sub_id:
        return None
    try:
        return Subscription.objects.get(stripe_subscription_id=sub_id)
    except Subscription.DoesNotExist:
        try:
            return stripe_svc.upsert_local_subscription_from_stripe(sub_id)
        except Exception:
            return None

def _record_refund_negative_row(refund_obj):

    try:
        charge_id = refund_obj.get("charge")
        amount = _cents_to_decimal(refund_obj.get("amount"))
        currency = (refund_obj.get("currency") or "usd").lower()
        reason = refund_obj.get("reason") or ""
        refund_id = refund_obj.get("id")

        original = BillingTransaction.objects.filter(stripe_charge_id=charge_id).first()
        user = original.user if original else None
        subscription = original.subscription if original else None
        plan_ct = original.plan_content_type if original else None
        plan_obj_id = original.plan_object_id if original else None
        plan_name = original.plan_name if original else ""

        BillingTransaction.objects.create(
            user=user,
            subscription=subscription,
            plan_content_type=plan_ct,
            plan_object_id=plan_obj_id,
            plan_name=plan_name,
            kind=original.kind if original else "purchase",
            status="succeeded",
            amount=(amount * Decimal("-1")),  # negative refund
            currency=currency,
            description=f"Refund: {reason or 'requested'}",
            meta={
                "refund_id": refund_id,
                "original_billing_transaction_id": (original.id if original else None),
                "original_charge_id": charge_id,
            },
        )
    except Exception:
        pass

@csrf_exempt
def stripe_webhook(request):
    import logging
    logger = logging.getLogger(__name__)

    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        if WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
        else:
            event = json.loads(payload.decode("utf-8"))
    except Exception:
        logger.exception("Stripe webhook signature/parse failed")
        return HttpResponse(status=400)

    etype = event.get("type")
    data = (event.get("data") or {}).get("object", {})

    try:
        logger.info("Stripe webhook event: type=%s", etype)
    except Exception:
        pass


    if etype == "checkout.session.completed":
        sess = data
        mode = sess.get("mode")
        payment_status = sess.get("payment_status")
        pi_id = sess.get("payment_intent")
        if isinstance(pi_id, dict):
            pi_id = pi_id.get("id")

        if mode == "subscription":
            inv_id = sess.get("invoice")
            local = None
            try:
                stripe_sub_id = sess.get("subscription")
                if stripe_sub_id:
                    local = stripe_svc.upsert_local_subscription_from_stripe(stripe_sub_id)
            except Exception:
                local = None

            if inv_id:
                try:
                    inv = stripe.Invoice.retrieve(inv_id, expand=["payment_intent", "lines.data"])
                    defaults = _tx_defaults_from_invoice(
                        inv,
                        local,
                        status=("succeeded" if inv.get("paid") else "initiated"),
                        overwrite_amount_with_paid=bool(inv.get("paid")),
                    )
                    defaults["meta"]["checkout_session_id"] = sess.get("id")
                    BillingTransaction.objects.update_or_create(
                        stripe_invoice_id=inv.get("id"),
                        defaults=defaults,
                    )
                except Exception:
                    pass
            else:
                try:
                    BillingTransaction.objects.get_or_create(
                        stripe_checkout_session_id=sess.get("id"),
                        defaults=dict(
                            user=(local.user if local else None),
                            subscription=local,
                            plan_content_type=(local.plan_content_type if local else None),
                            plan_object_id=(local.plan_object_id if local else None),
                            plan_name=(str(local.plan) if local else ""),
                            kind="purchase",
                            status=("succeeded" if payment_status == "paid" else "initiated"),
                            amount=_cents_to_decimal(sess.get("amount_total")),
                            currency=(sess.get("currency") or "usd"),
                            stripe_payment_intent_id=pi_id or None,
                            description="Subscription created via Checkout (no invoice)",
                        ),
                    )
                except Exception:
                    pass

        elif mode == "payment":
            meta = sess.get("metadata") or {}
            try:
                sub_pk = int(meta.get("subscription_pk"))
                component = str(meta.get("component"))
                minutes = int(meta.get("minutes"))
            except Exception:
                sub_pk = None; component = None; minutes = None

            if payment_status == "paid" and sub_pk and component and minutes:
                try:
                    sub = Subscription.objects.select_related("user").get(pk=sub_pk)
                except Subscription.DoesNotExist:
                    sub = None

                if sub:
                    try:
                        if not BillingTransaction.objects.filter(stripe_checkout_session_id=sess.get("id")).exists():
                            stripe_svc.apply_topup_credit(sub, component, minutes)
                            amt = _cents_to_decimal(sess.get("amount_total"))
                            charge_id = None
                            if pi_id:
                                try:
                                    pi = stripe.PaymentIntent.retrieve(pi_id, expand=["charges.data"])
                                    chg = (pi.get("charges") or {}).get("data") or []
                                    charge_id = chg[0].get("id") if chg else None
                                except Exception:
                                    pass

                            # Retrieve invoice information for top-up purchases
                            invoice_id = None
                            invoice_url = None
                            invoice_pdf = None
                            inv_id = sess.get("invoice")
                            if inv_id:
                                try:
                                    inv = stripe.Invoice.retrieve(inv_id, expand=["payment_intent", "lines.data"])
                                    invoice_id = inv.get("id")
                                    invoice_url = inv.get("hosted_invoice_url")
                                    invoice_pdf = inv.get("invoice_pdf")
                                except Exception:
                                    pass

                            BillingTransaction.objects.create(
                                user=sub.user,
                                subscription=sub,
                                plan_content_type=sub.plan_content_type,
                                plan_object_id=sub.plan_object_id,
                                plan_name=str(sub.plan),
                                kind="top_up",
                                status="succeeded",
                                amount=amt,
                                currency=sess.get("currency", getattr(sub.plan, "currency", "usd")),
                                stripe_payment_intent_id=pi_id or None,
                                stripe_charge_id=charge_id or None,
                                stripe_checkout_session_id=sess.get("id"),
                                stripe_invoice_id=invoice_id or None,
                                stripe_invoice_url=invoice_url or None,
                                stripe_invoice_pdf=invoice_pdf or None,
                                description=f"Top-up {component}: {minutes} minute(s)",
                            )
                    except Exception as e:
                        BillingTransaction.objects.get_or_create(
                            stripe_checkout_session_id=sess.get("id"),
                            defaults=dict(
                                user=sub.user if sub else None,
                                subscription=sub,
                                plan_content_type=sub.plan_content_type if sub else None,
                                plan_object_id=sub.plan_object_id if sub else None,
                                plan_name=str(sub.plan) if sub else "",
                                kind="top_up",
                                status="failed",
                                amount=Decimal("0.00"),
                                currency="usd",
                                stripe_payment_intent_id=pi_id or None,
                                failure_message=str(e),
                            ),
                        )

    elif etype == "checkout.session.async_payment_failed":
        sess = data
        if sess.get("mode") == "payment":
            meta = sess.get("metadata") or {}
            try:
                sub_pk = int(meta.get("subscription_pk"))
            except Exception:
                sub_pk = None
            try:
                sub = Subscription.objects.get(pk=sub_pk) if sub_pk else None
            except Subscription.DoesNotExist:
                sub = None
            BillingTransaction.objects.get_or_create(
                stripe_checkout_session_id=sess.get("id"),
                defaults=dict(
                    user=sub.user if sub else None,
                    subscription=sub,
                    plan_content_type=sub.plan_content_type if sub else None,
                    plan_object_id=sub.plan_object_id if sub else None,
                    plan_name=str(sub.plan) if sub else "",
                    kind="top_up",
                    status="failed",
                    amount=_cents_to_decimal(sess.get("amount_total")),
                    currency=(sess.get("currency") or "usd"),
                    description="Checkout async payment failed",
                ),
            )


    # elif etype == "invoice.finalized":
    #     inv = data
    #     local = _local_sub_for_invoice(inv)
    #     defaults = _tx_defaults_from_invoice(inv, local, status="initiated", overwrite_amount_with_paid=False)
    #     BillingTransaction.objects.update_or_create(
    #         stripe_invoice_id=inv.get("id"),
    #         defaults=defaults,
    #     )

    elif etype == "invoice.payment_succeeded":
        inv = data
        sub_id = inv.get("subscription")
        local = _local_sub_for_invoice(inv)

        if local:
            try:
                local.latest_invoice_id = inv.get("id")
                local.status = "active"
                ps, pe, _, _ = stripe_svc.normalize_subscription_with_periods(sub_id, local.stripe_subscription_item_id)
                changed = ["latest_invoice_id", "status"]
                if ps: local.current_period_start = ps; changed.append("current_period_start")
                if pe: local.current_period_end = pe; changed.append("current_period_end")
                local.save(update_fields=changed)
            except Exception:
                pass

        defaults = _tx_defaults_from_invoice(inv, local, status="succeeded", overwrite_amount_with_paid=True)
        tx = BillingTransaction.objects.filter(stripe_invoice_id=inv.get("id")).first()
        if tx:
            tx.status = "succeeded"
            tx.amount = defaults["amount"]
            tx.currency = defaults["currency"]
            tx.stripe_invoice_url = defaults["stripe_invoice_url"]
            tx.stripe_invoice_pdf = defaults["stripe_invoice_pdf"]
            if defaults.get("stripe_payment_intent_id") and not tx.stripe_payment_intent_id:
                tx.stripe_payment_intent_id = defaults["stripe_payment_intent_id"]
            if defaults.get("stripe_charge_id") and not tx.stripe_charge_id:
                tx.stripe_charge_id = defaults["stripe_charge_id"]
            tx.save(update_fields=[
                "status", "amount", "currency", "stripe_invoice_url", "stripe_invoice_pdf",
                "stripe_payment_intent_id", "stripe_charge_id"
            ])
        else:
            BillingTransaction.objects.create(**defaults)

    elif etype == "invoice.payment_failed":
        inv = data
        local = _local_sub_for_invoice(inv)
        if local:
            try:
                local.status = "past_due"
                local.save(update_fields=["status"])
            except Exception:
                pass

        pi_id = inv.get("payment_intent")
        failure_code = ""
        failure_message = ""
        if isinstance(pi_id, dict):
            pi_id = pi_id.get("id")
        if pi_id:
            try:
                pi = stripe.PaymentIntent.retrieve(pi_id)
                err = pi.get("last_payment_error") or {}
                failure_code = err.get("code") or ""
                failure_message = err.get("message") or (err.get("decline_code") or "")
            except Exception:
                pass

        defaults = _tx_defaults_from_invoice(inv, local, status="failed", overwrite_amount_with_paid=False)
        defaults["failure_code"] = failure_code
        defaults["failure_message"] = failure_message

        tx = BillingTransaction.objects.filter(stripe_invoice_id=inv.get("id")).first()
        if tx:
            tx.status = "failed"
            tx.failure_code = failure_code
            tx.failure_message = failure_message
            tx.stripe_invoice_url = defaults["stripe_invoice_url"]
            tx.stripe_invoice_pdf = defaults["stripe_invoice_pdf"]
            if defaults.get("stripe_payment_intent_id") and not tx.stripe_payment_intent_id:
                tx.stripe_payment_intent_id = defaults["stripe_payment_intent_id"]
            tx.save(update_fields=[
                "status", "failure_code", "failure_message", "stripe_invoice_url", "stripe_invoice_pdf",
                "stripe_payment_intent_id"
            ])
        else:
            BillingTransaction.objects.create(**defaults)

        try:
            if local:
                to_email = local.user.email
                subject = "Payment failed for your subscription"
                from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com")
                body = "We could not process your renewal payment. Please update your payment method to avoid interruption."
                send_mail(subject, body, from_email, [to_email], fail_silently=True)
        except Exception:
            pass

    elif etype in ("invoice.voided", "invoice.marked_uncollectible"):
        inv = data
        local = _local_sub_for_invoice(inv)
        defaults = _tx_defaults_from_invoice(inv, local, status="failed", overwrite_amount_with_paid=False)
        defaults["failure_code"] = ("voided" if etype.endswith("voided") else "uncollectible")
        BillingTransaction.objects.update_or_create(
            stripe_invoice_id=inv.get("id"),
            defaults=defaults,
        )


    elif etype == "payment_intent.payment_failed":
        pi = data
        inv_id = pi.get("invoice")
        if inv_id:
            try:
                inv = stripe.Invoice.retrieve(inv_id)
                err = pi.get("last_payment_error") or {}
                failure_code = err.get("code") or ""
                failure_message = err.get("message") or (err.get("decline_code") or "")
                local = _local_sub_for_invoice(inv)
                defaults = _tx_defaults_from_invoice(inv, local, status="failed", overwrite_amount_with_paid=False)
                defaults["failure_code"] = failure_code
                defaults["failure_message"] = failure_message
                BillingTransaction.objects.update_or_create(
                    stripe_invoice_id=inv.get("id"),
                    defaults=defaults,
                )
            except Exception:
                pass


    elif etype == "refund.created":
        _record_refund_negative_row(data)

    elif etype == "refund.updated":
        pass

    elif etype == "credit_note.created":
        cn = data
        try:
            invoice_id = cn.get("invoice")
            amount = _cents_to_decimal(cn.get("amount"))
            currency = (cn.get("currency") or "usd").lower()
            refund_id = cn.get("refund")  # may be None (credit to balance)
            inv = stripe.Invoice.retrieve(invoice_id) if invoice_id else None
            local = _local_sub_for_invoice(inv) if inv else None
            original = BillingTransaction.objects.filter(stripe_invoice_id=invoice_id).first() if invoice_id else None

            BillingTransaction.objects.create(
                user=(local.user if local else (original.user if original else None)),
                subscription=(local or (original.subscription if original else None)),
                plan_content_type=((local.plan_content_type if local else (original.plan_content_type if original else None))),
                plan_object_id=((local.plan_object_id if local else (original.plan_object_id if original else None))),
                plan_name=(str(local.plan) if local else (original.plan_name if original else "")),
                kind=(original.kind if original else _kind_from_billing_reason(inv.get("billing_reason") if inv else None)),
                status="succeeded",
                amount=(amount * Decimal("-1")),
                currency=currency,
                description=f"Credit note{' (refund)' if refund_id else ''}",
                meta={
                    "credit_note_id": cn.get("id"),
                    "refund_id": refund_id,
                    "invoice_id": invoice_id,
                    "number": cn.get("number"),
                },
            )
        except Exception:
            pass


    elif etype == "customer.subscription.updated":
        obj = data
        sub_id = obj.get("id")
        try:
            sub = Subscription.objects.get(stripe_subscription_id=sub_id)
            ps, pe, item_id, _ = stripe_svc.normalize_subscription_with_periods(sub_id, sub.stripe_subscription_item_id)
            changed = []
            new_status = obj.get("status", sub.status)
            if new_status != sub.status:
                sub.status = new_status; changed.append("status")
            new_cancel = obj.get("cancel_at_period_end", sub.cancel_at_period_end)
            if new_cancel != sub.cancel_at_period_end:
                sub.cancel_at_period_end = new_cancel; changed.append("cancel_at_period_end")
            if ps and sub.current_period_start != ps:
                sub.current_period_start = ps; changed.append("current_period_start")
            if pe and sub.current_period_end != pe:
                sub.current_period_end = pe; changed.append("current_period_end")
            if item_id and sub.stripe_subscription_item_id != item_id:
                sub.stripe_subscription_item_id = item_id; changed.append("stripe_subscription_item_id")
            if changed:
                sub.save(update_fields=changed)
                sub.initialize_or_rollover_usage_buckets()
        except Subscription.DoesNotExist:
            pass

    elif etype == "customer.subscription.deleted":
        obj = data
        sub_id = obj.get("id")
        try:
            sub = Subscription.objects.get(stripe_subscription_id=sub_id)
            sub.status = "canceled"
            sub.canceled_at = timezone.now()
            sub.ended_at = sub.ended_at or timezone.now()
            sub.save(update_fields=["status", "canceled_at", "ended_at"])
        except Subscription.DoesNotExist:
            pass

    return HttpResponse(status=200)