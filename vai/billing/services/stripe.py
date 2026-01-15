from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from typing import Optional
import os

from django.db.models import F
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

import stripe

from ..models import Subscription, PaymentMethod, BillingTransaction, SupportAgentPlan, OutboundCallingPlan
from ..utils import seconds_to_billable_minutes

STRIPE_API_KEY = getattr(settings, "STRIPE_API_KEY", os.environ.get("STRIPE_API_KEY"))
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY


def _ensure_stripe_initialized():
    if not STRIPE_API_KEY:
        raise RuntimeError("Stripe API key not configured")


def _ts_to_dt(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=dt_tz.utc)
    except Exception:
        return None


def _cents_to_decimal(cents) -> Decimal:
    return Decimal(str(cents or 0)) / Decimal("100")


def _decimal_to_cents(amount: Decimal | float | int) -> int:
    return int(Decimal(str(amount)) * 100)


def _invoice_links(invoice_obj) -> tuple[str | None, str | None]:
    if not invoice_obj:
        return None, None
    if isinstance(invoice_obj, str):
        try:
            inv = stripe.Invoice.retrieve(invoice_obj)
            return inv.get("hosted_invoice_url"), inv.get("invoice_pdf")
        except Exception:
            return None, None
    return invoice_obj.get("hosted_invoice_url"), invoice_obj.get("invoice_pdf")


def _customer_default_pm(customer_id: str) -> Optional[str]:
    try:
        cust = stripe.Customer.retrieve(customer_id)
        inv_settings = cust.get("invoice_settings") or {}
        return inv_settings.get("default_payment_method")
    except Exception:
        return None


def get_or_create_customer(user, test_clock_id: Optional[str] = None) -> str:
    _ensure_stripe_initialized()
    if getattr(user, "stripe_customer_id", None):
        return user.stripe_customer_id
    kwargs = {
        "email": user.email,
        "metadata": {
            "django_user_id": str(user.pk),
            "user_name": getattr(user, "user_name", "") or "",
        },
    }
    if test_clock_id:
        kwargs["test_clock"] = test_clock_id
    customer = stripe.Customer.create(**kwargs)
    user.stripe_customer_id = customer["id"]
    user.save(update_fields=["stripe_customer_id"])
    return user.stripe_customer_id

def invalidate_all_minutes(subscription):
    from datetime import timedelta
    now = timezone.now()
    subscription.initialize_or_rollover_usage_buckets()
    subscription.usage_buckets.active().update(unlimited=False, seconds_included=F("seconds_used"), period_end=now - timedelta(seconds=1))
    return True


def cancel_and_checkout_full_price_upgrade(user, subscription, new_plan, success_url: str, cancel_url: str):
    invalidate_all_minutes(subscription)
    cancel_subscription(subscription, at_period_end=False)
    session = create_checkout_session_for_subscription(user, new_plan, success_url, cancel_url)
    BillingTransaction.objects.create(
        user=user,
        subscription=subscription,
        plan_content_type=ContentType.objects.get_for_model(new_plan.__class__),
        plan_object_id=new_plan.pk,
        plan_name=new_plan.name,
        kind="upgrade",
        status="initiated",
        amount=Decimal("0.00"),
        currency=getattr(new_plan, "currency", "usd"),
        stripe_checkout_session_id=session["id"],
        description=f"Upgrade to {new_plan.name} via Checkout",
        meta={"full_price": True},
    )
    return session
def ensure_product_and_price(plan) -> tuple[str, str]:
    _ensure_stripe_initialized()
    components = list(getattr(plan, "components", lambda: {})().keys()) if hasattr(plan, "components") else []
    common_meta = {
        "django_model": plan.__class__.__name__,
        "django_plan_id": str(plan.pk),
        "is_trial": str(getattr(plan, "is_trial", False)),
        "components": str(components),
    }
    if not getattr(plan, "stripe_product_id", None):
        product = stripe.Product.create(
            name=plan.name,
            active=bool(getattr(plan, "is_active", True)),
            metadata=common_meta,
            idempotency_key=f"plan-prod-{plan.__class__.__name__}-{plan.pk}",
        )
        plan.stripe_product_id = product["id"]
        plan.save(update_fields=["stripe_product_id"])
    else:
        try:
            stripe.Product.modify(
                plan.stripe_product_id,
                name=plan.name,
                active=bool(getattr(plan, "is_active", True)),
                metadata=common_meta,
            )
        except Exception:
            product = stripe.Product.create(
                name=plan.name,
                active=bool(getattr(plan, "is_active", True)),
                metadata=common_meta,
                idempotency_key=f"plan-prod-{plan.pk}",
            )
            plan.stripe_product_id = product["id"]
            plan.save(update_fields=["stripe_product_id"])
    desired_unit_amount = _decimal_to_cents(Decimal(str(plan.price)))
    desired_currency = str(plan.currency).lower()
    desired_interval = plan.billing_interval
    need_new_price = False
    if not getattr(plan, "stripe_price_id", None):
        need_new_price = True
    else:
        try:
            price = stripe.Price.retrieve(plan.stripe_price_id)
            rec = price.get("recurring") or {}
            if (
                price.get("product") != plan.stripe_product_id
                or price.get("unit_amount") != desired_unit_amount
                or str(price.get("currency", "")).lower() != desired_currency
                or rec.get("interval") != desired_interval
            ):
                need_new_price = True
            else:
                stripe.Price.modify(plan.stripe_price_id, active=True, metadata=common_meta)
        except Exception:
            need_new_price = True
    if need_new_price:
        old_price_id = getattr(plan, "stripe_price_id", None)
        new_price = stripe.Price.create(
            product=plan.stripe_product_id,
            currency=desired_currency,
            unit_amount=desired_unit_amount,
            recurring={"interval": desired_interval},
            metadata=common_meta,
            idempotency_key=f"plan-price-{plan.pk}-{desired_unit_amount}-{desired_currency}-{desired_interval}",
        )
        plan.stripe_price_id = new_price["id"]
        plan.save(update_fields=["stripe_price_id"])
        if old_price_id and old_price_id != new_price["id"]:
            try:
                stripe.Price.modify(old_price_id, active=False)
            except Exception:
                pass
    return plan.stripe_product_id, plan.stripe_price_id


def create_subscription(
    user,
    plan,
    payment_method,
    *,
    trial_days: Optional[int] = None,
    cancel_at_period_end: bool = False,
) -> Subscription:
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    _, price_id = ensure_product_and_price(plan)
    default_pm_id = _customer_default_pm(customer_id)
    trial_days = trial_days if trial_days is not None else (getattr(plan, "trial_period_days", None) or None)
    params = {
        "customer": customer_id,
        "items": [{"price": price_id}],
        "collection_method": "charge_automatically",
        "cancel_at_period_end": bool(cancel_at_period_end),
        "payment_behavior": "default_incomplete",
        "default_payment_method": payment_method or default_pm_id,
        "expand": ["latest_invoice.payment_intent", "items.data", "latest_invoice.lines.data"],
        "payment_settings": {"save_default_payment_method": "on_subscription"},
    }
    if trial_days and int(trial_days) > 0:
        params["trial_period_days"] = int(trial_days)
    sub = stripe.Subscription.create(**params)
    inv_obj = sub.get("latest_invoice")
    latest_invoice_id = None
    hosted_url = None
    invoice_pdf = None
    amount_due = Decimal("0.00")
    currency = getattr(plan, "currency", "usd")
    pi_id = None
    pi_status = None
    if isinstance(inv_obj, dict):
        latest_invoice_id = inv_obj.get("id")
        hosted_url, invoice_pdf = _invoice_links(inv_obj)
        amount_due = _cents_to_decimal(inv_obj.get("amount_due"))
        currency = inv_obj.get("currency", currency)
        pi = inv_obj.get("payment_intent")
        if isinstance(pi, dict):
            pi_id = pi.get("id")
            pi_status = pi.get("status")
    if (default_pm_id or payment_method) and amount_due > 0 and pi_id and pi_status in {"requires_payment_method", "requires_confirmation"}:
        try:
            confirmed = stripe.PaymentIntent.confirm(
                pi_id,
                payment_method=(payment_method or default_pm_id),
                off_session=True,
            )
            pi_status = confirmed.get("status")
            if latest_invoice_id:
                inv_obj = stripe.Invoice.retrieve(latest_invoice_id, expand=["payment_intent", "lines.data"])
                hosted_url, invoice_pdf = _invoice_links(inv_obj)
        except Exception:
            pass
    period_start, period_end, item_id, _ = normalize_subscription_with_periods(sub)
    subscription = Subscription.objects.create(
        user=user,
        plan_content_type=ContentType.objects.get_for_model(plan.__class__),
        plan_object_id=plan.pk,
        stripe_subscription_id=sub["id"],
        stripe_subscription_item_id=item_id,
        status=sub.get("status"),
        started_at=timezone.now(),
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=sub.get("cancel_at_period_end", bool(cancel_at_period_end)),
        latest_invoice_id=latest_invoice_id,
        metadata={},
    )
    try:
        invoice_paid = bool(isinstance(inv_obj, dict) and inv_obj.get("paid"))
    except Exception:
        invoice_paid = False
    tx_status = "succeeded" if (invoice_paid or (pi_status == "succeeded") or (amount_due == 0)) else "initiated"
    BillingTransaction.objects.create(
        user=user,
        subscription=subscription,
        plan_content_type=ContentType.objects.get_for_model(plan.__class__),
        plan_object_id=plan.pk,
        plan_name=plan.name,
        kind="purchase",
        status=tx_status,
        amount=amount_due,
        currency=currency,
        stripe_invoice_id=latest_invoice_id,
        stripe_invoice_url=hosted_url if tx_status != "succeeded" else None,
        stripe_invoice_pdf=invoice_pdf,
        stripe_payment_intent_id=pi_id,
        description=f"Purchase of {plan.name}",
        meta={"billing_interval": plan.billing_interval},
    )
    subscription.initialize_or_rollover_usage_buckets()
    return subscription


def cancel_subscription(subscription: Subscription, *, at_period_end: bool = True) -> Subscription:
    _ensure_stripe_initialized()
    if not subscription.stripe_subscription_id:
        subscription.status = "canceled"
        subscription.cancel_at_period_end = at_period_end
        subscription.canceled_at = timezone.now()
        subscription.ended_at = subscription.ended_at or timezone.now()
        subscription.save(update_fields=["status", "cancel_at_period_end", "canceled_at", "ended_at"])
        return subscription
    sub = stripe.Subscription.modify(
        subscription.stripe_subscription_id,
        cancel_at_period_end=at_period_end,
    )
    if not at_period_end:
        sub = stripe.Subscription.delete(subscription.stripe_subscription_id)
    subscription.status = sub["status"]
    subscription.cancel_at_period_end = sub.get("cancel_at_period_end", at_period_end)
    if subscription.status == "canceled":
        subscription.canceled_at = timezone.now()
        subscription.ended_at = subscription.ended_at or timezone.now()
    subscription.save(update_fields=["status", "cancel_at_period_end", "canceled_at", "ended_at"])
    return subscription


def bill_overage_now(subscription: Subscription, description_prefix: str = "Overage"):
    _ensure_stripe_initialized()
    user = subscription.user
    customer_id = get_or_create_customer(user)
    total_cents = 0
    for bucket in subscription.usage_buckets.active():
        if bucket.unlimited:
            continue
        over_s = max(0, bucket.seconds_used - bucket.seconds_included)
        if over_s <= 0:
            continue
        minutes_to_bill = seconds_to_billable_minutes(over_s)
        unit_price = Decimal(bucket.extra_per_minute)
        amount_cents = _decimal_to_cents(Decimal(minutes_to_bill) * unit_price)
        if amount_cents <= 0:
            continue
        line_desc = f"{description_prefix}: {bucket.component} ({minutes_to_bill} minute{'s' if minutes_to_bill != 1 else ''})"
        stripe.InvoiceItem.create(
            customer=customer_id,
            amount=amount_cents,
            currency=subscription.plan.currency,
            description=line_desc,
            metadata={
                "subscription_id": str(subscription.pk),
                "component": bucket.component,
                "minutes_billed": str(minutes_to_bill),
            },
        )
        total_cents += amount_cents
    if total_cents > 0:
        invoice = stripe.Invoice.create(customer=customer_id, auto_advance=True)
        finalized = stripe.Invoice.finalize_invoice(invoice["id"])
        subscription.latest_invoice_id = finalized["id"]
        subscription.save(update_fields=["latest_invoice_id"])
    return total_cents


def create_setup_intent(user) -> dict:
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    si = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
        usage="off_session",
    )
    return {"client_secret": si["client_secret"], "id": si["id"]}


def list_customer_payment_methods(user):
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    pms = stripe.PaymentMethod.list(customer=customer_id, type="card")["data"]
    cust = stripe.Customer.retrieve(customer_id)
    default_pm_id = None
    inv = cust.get("invoice_settings") or {}
    if inv.get("default_payment_method"):
        default_pm_id = inv["default_payment_method"]
    seen_ids = set()
    for pm in pms:
        seen_ids.add(pm["id"])
        card = pm["card"]
        PaymentMethod.objects.update_or_create(
            stripe_payment_method_id=pm["id"],
            defaults=dict(
                user=user,
                brand=card["brand"],
                last4=card["last4"],
                exp_month=card["exp_month"],
                exp_year=card["exp_year"],
                is_default=(pm["id"] == default_pm_id),
            ),
        )
    PaymentMethod.objects.filter(user=user).exclude(stripe_payment_method_id__in=seen_ids).delete()
    return pms, default_pm_id


def set_default_payment_method(user, payment_method_id: str):
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": payment_method_id},
    )
    PaymentMethod.objects.filter(user=user, is_default=True).update(is_default=False)
    pm = stripe.PaymentMethod.retrieve(payment_method_id)
    card = pm["card"]
    PaymentMethod.objects.update_or_create(
        stripe_payment_method_id=payment_method_id,
        defaults=dict(
            user=user,
            brand=card["brand"],
            last4=card["last4"],
            exp_month=card["exp_month"],
            exp_year=card["exp_year"],
            is_default=True,
        ),
    )


def detach_payment_method(user, payment_method_id: str):
    _ensure_stripe_initialized()
    stripe.PaymentMethod.detach(payment_method_id)
    PaymentMethod.objects.filter(user=user, stripe_payment_method_id=payment_method_id).delete()


def swap_subscription_price(subscription: Subscription, new_plan) -> tuple[Subscription, dict]:
    _ensure_stripe_initialized()
    if not subscription.stripe_subscription_id:
        raise RuntimeError("Cannot swap price: missing stripe_subscription_id")
    _, new_price_id = ensure_product_and_price(new_plan)
    item_id = subscription.stripe_subscription_item_id
    if not item_id:
        sub = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=False,
            proration_behavior="create_prorations",
            items=[{"price": new_price_id}],
            expand=["items.data", "latest_invoice", "latest_invoice.lines.data"],
        )
    else:
        sub = stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=False,
            proration_behavior="create_prorations",
            items=[{"id": item_id, "price": new_price_id}],
            expand=["items.data", "latest_invoice", "latest_invoice.lines.data"],
        )
    period_start, period_end, item_id_after, _ = normalize_subscription_with_periods(sub, subscription.stripe_subscription_item_id)
    subscription.status = sub.get("status")
    subscription.current_period_start = period_start
    subscription.current_period_end = period_end
    subscription.cancel_at_period_end = sub.get("cancel_at_period_end", False)
    subscription.stripe_subscription_item_id = item_id_after or subscription.stripe_subscription_item_id
    subscription.save(update_fields=["status", "current_period_start", "current_period_end", "cancel_at_period_end", "stripe_subscription_item_id"])
    invoice_info = {}
    inv_obj = sub.get("latest_invoice")
    if isinstance(inv_obj, dict):
        hosted_url, invoice_pdf = _invoice_links(inv_obj)
        invoice_info = {
            "invoice_id": inv_obj.get("id"),
            "hosted_invoice_url": hosted_url,
            "invoice_pdf": invoice_pdf,
            "amount_due": (_cents_to_decimal(inv_obj.get("amount_due")) or Decimal("0.00")),
            "currency": inv_obj.get("currency"),
        }
    elif isinstance(inv_obj, str):
        try:
            inv = stripe.Invoice.retrieve(inv_obj)
            hosted_url, invoice_pdf = _invoice_links(inv)
            invoice_info = {
                "invoice_id": inv.get("id"),
                "hosted_invoice_url": hosted_url,
                "invoice_pdf": invoice_pdf,
                "amount_due": (_cents_to_decimal(inv.get("amount_due")) or Decimal("0.00")),
                "currency": inv.get("currency"),
            }
        except Exception:
            pass
    BillingTransaction.objects.create(
        user=subscription.user,
        subscription=subscription,
        plan_content_type=ContentType.objects.get_for_model(new_plan.__class__),
        plan_object_id=new_plan.pk,
        plan_name=new_plan.name,
        kind="upgrade",
        status="initiated",
        amount=Decimal(str(invoice_info.get("amount_due", 0.0))),
        currency=invoice_info.get("currency") or getattr(new_plan, "currency", "usd"),
        stripe_invoice_id=invoice_info.get("invoice_id"),
        stripe_invoice_url=invoice_info.get("hosted_invoice_url"),
        stripe_invoice_pdf=invoice_info.get("invoice_pdf"),
        description=f"Upgrade to {new_plan.name}",
        meta={"proration": True},
    )
    return subscription, invoice_info


def create_checkout_session_for_subscription(user, plan, success_url: str, cancel_url: str):
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    _, price_id = ensure_product_and_price(plan)
    if isinstance(plan, SupportAgentPlan):
        component = "support_agent"
    elif isinstance(plan, OutboundCallingPlan):
        component = "outbound_calling"
    else:
        component = "bundle"
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        success_url=f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=cancel_url,
        client_reference_id=str(user.pk),
        subscription_data={
            "metadata": {
                "component": component,
                "django_plan_id": str(plan.pk),
                "django_model": plan.__class__.__name__,
            },
        },
        metadata={
            "intent": "new_subscription",
            "component": component,
            "django_user_id": str(user.pk),
            "django_plan_id": str(plan.pk),
        },
    )
    return session


def create_checkout_session_for_topup(user, subscription: Subscription, component: str, minutes: int, success_url: str, cancel_url: str):
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    subscription.initialize_or_rollover_usage_buckets()
    bucket = subscription.get_active_bucket(component)
    if not bucket:
        raise RuntimeError("No active usage bucket for component")
    currency = getattr(subscription.plan, "currency", "usd")
    unit_price = Decimal(str(bucket.extra_per_minute or "0"))
    name = "Support Agent Minutes Top-Up" if component == "support_agent" else "Outbound Calls Minutes Top-Up"
    unit_amount = _decimal_to_cents(unit_price)
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{
            "price_data": {
                "currency": currency,
                "product_data": {"name": name},
                "unit_amount": unit_amount,
            },
            "quantity": int(minutes),
        }],
        allow_promotion_codes=False,
        success_url=f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=cancel_url,
        client_reference_id=str(user.pk),
        metadata={
            "intent": "minutes_topup",
            "component": component,
            "django_user_id": str(user.pk),
            "subscription_pk": str(subscription.pk),
            "minutes": str(minutes),
        },
    )
    return session


def create_portal_update_confirm_session(user, subscription: Subscription, new_plan, return_url: str):
    _ensure_stripe_initialized()
    customer_id = get_or_create_customer(user)
    _, new_price_id = ensure_product_and_price(new_plan)
    cfg_id = getattr(settings, "STRIPE_PORTAL_CONFIGURATION_ID", None)
    args = {
        "customer": customer_id,
        "return_url": return_url,
        "flow_data": {
            "type": "subscription_update_confirm",
            "after_completion": {"type": "redirect", "redirect": {"return_url": return_url}},
            "subscription_update_confirm": {
                "subscription": subscription.stripe_subscription_id,
                "items": [{
                    "id": subscription.stripe_subscription_item_id,
                    "price": new_price_id,
                }],
            },
        },
    }
    if cfg_id:
        args["configuration"] = cfg_id
    sess = stripe.billing_portal.Session.create(**args)
    return sess


def upsert_local_subscription_from_stripe(stripe_sub_id):
    import logging
    from django.contrib.auth import get_user_model
    from ..models import SupportAgentPlan, OutboundCallingPlan, BundlePlan
    logger = logging.getLogger(__name__)
    User = get_user_model()
    sub = stripe.Subscription.retrieve(
        stripe_sub_id,
        expand=["items.data.price.product", "customer", "items.data", "latest_invoice", "latest_invoice.lines.data"],
    )
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        raise ValueError(f"Stripe subscription {stripe_sub_id} has no items")
    price = items[0].get("price") or {}
    price_id = price.get("id")
    product = price.get("product")
    product_id = product.get("id") if isinstance(product, dict) else product
    plan = (
        SupportAgentPlan.objects.filter(stripe_price_id=price_id).first()
        or OutboundCallingPlan.objects.filter(stripe_price_id=price_id).first()
        or BundlePlan.objects.filter(stripe_price_id=price_id).first()
    )
    if not plan and product_id:
        plan = (
            SupportAgentPlan.objects.filter(stripe_product_id=product_id).first()
            or OutboundCallingPlan.objects.filter(stripe_product_id=product_id).first()
            or BundlePlan.objects.filter(stripe_product_id=product_id).first()
        )
    if not plan:
        raise ValueError(f"No local plan mapped to Stripe price={price_id} product={product_id}")
    user = None
    md = sub.get("metadata") or {}
    if md.get("user_id"):
        user = User.objects.filter(pk=md["user_id"]).first()
    if not user:
        cust = sub.get("customer")
        if isinstance(cust, dict):
            cmd = cust.get("metadata") or {}
            if cmd.get("user_id"):
                user = User.objects.filter(pk=cmd["user_id"]).first()
            if not user and cust.get("email"):
                user = User.objects.filter(email__iexact=cust["email"]).first()
        elif isinstance(cust, str):
            try:
                customer = stripe.Customer.retrieve(cust)
                cmd = customer.get("metadata") or {}
                if cmd.get("user_id"):
                    user = User.objects.filter(pk=cmd["user_id"]).first()
                if not user and customer.get("email"):
                    user = User.objects.filter(email__iexact=customer["email"]).first()
            except Exception:
                logger.exception("Failed to retrieve customer %s for subscription %s", cust, stripe_sub_id)
    if not user:
        raise ValueError("Cannot resolve local user for subscription")
    period_start, period_end, item_id, _ = normalize_subscription_with_periods(sub)
    ct = ContentType.objects.get_for_model(plan.__class__)
    local, _ = Subscription.objects.update_or_create(
        stripe_subscription_id=sub["id"],
        defaults=dict(
            user=user,
            plan_content_type=ct,
            plan_object_id=plan.pk,
            status=sub.get("status"),
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=sub.get("cancel_at_period_end", False),
            stripe_subscription_item_id=item_id,
        ),
    )
    local.initialize_or_rollover_usage_buckets()
    try:
        active_statuses = ["trialing", "active", "incomplete", "past_due"]
        trials = Subscription.objects.filter(user=local.user, status__in=active_statuses).exclude(pk=local.pk)
        for t in trials:
            try:
                if getattr(t.plan, "is_trial", False):
                    cancel_subscription(t, at_period_end=False)
            except Exception:
                continue
    except Exception:
        pass
    return local



def normalize_subscription_with_periods(sub_or_id, preferred_item_id: Optional[str] = None):
    if isinstance(sub_or_id, str):
        s = stripe.Subscription.retrieve(sub_or_id, expand=["items.data", "latest_invoice", "latest_invoice.lines.data"])
    else:
        s = sub_or_id
        try:
            s_id = s.get("id") if isinstance(s, dict) else s.id
        except Exception:
            s_id = None
        items_data = (s.get("items") or {}).get("data") if isinstance(s, dict) else getattr(s, "items", None)
        if (not items_data) and s_id:
            try:
                s = stripe.Subscription.retrieve(s_id, expand=["items.data", "latest_invoice", "latest_invoice.lines.data"])
            except Exception:
                pass
    ps, pe, item_id = extract_period_from_subscription(s, preferred_item_id)
    if not ps or not pe:
        inv = s.get("latest_invoice")
        lines = []
        if isinstance(inv, dict):
            lines = ((inv.get("lines") or {}).get("data")) or []
        elif isinstance(inv, str):
            try:
                inv_obj = stripe.Invoice.retrieve(inv, expand=["lines.data"])
                lines = ((inv_obj.get("lines") or {}).get("data")) or []
            except Exception:
                lines = []
        for ln in lines or []:
            if ln.get("type") == "subscription":
                pr = ln.get("period") or {}
                if not ps:
                    ps = _ts_to_dt(pr.get("start"))
                if not pe:
                    pe = _ts_to_dt(pr.get("end"))
                if ps and pe:
                    break
    return ps, pe, item_id, s


def extract_period_from_subscription(sub, preferred_item_id: Optional[str] = None):
    items = (sub.get("items") or {}).get("data") or []
    selected = None
    if preferred_item_id:
        for it in items:
            if it.get("id") == preferred_item_id:
                selected = it
                break
    if not selected and items:
        selected = items[0]
    cps = selected.get("current_period_start") if selected else None
    cpe = selected.get("current_period_end") if selected else None
    return _ts_to_dt(cps), _ts_to_dt(cpe), (selected.get("id") if selected else (items[0].get("id") if items else None))

def _create_local_trial_subscription(user, plan):
    from vai.billing.models import Subscription as BillingSubscription
    now = timezone.now()
    days = int(getattr(plan, "trial_period_days", 0) or 0)
    period_start = now
    period_end = now + timedelta(days=days) if days > 0 else now
    ct = ContentType.objects.get_for_model(plan.__class__)
    sub = BillingSubscription.objects.create(
        user=user,
        plan_content_type=ct,
        plan_object_id=plan.pk,
        status="trialing",
        started_at=now,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=True,
        metadata={},
    )
    sub.initialize_or_rollover_usage_buckets()
    return sub

def apply_topup_credit(subscription: Subscription, component: str, minutes: int):
    subscription.initialize_or_rollover_usage_buckets()
    bucket = subscription.get_active_bucket(component)
    if not bucket:
        raise RuntimeError("No active usage bucket for component")
    extra_seconds = int(minutes) * 60
    bucket.seconds_included = F("seconds_included") + extra_seconds
    bucket.save(update_fields=["seconds_included"])
    bucket.refresh_from_db()
    return bucket

