# pricing_api.py
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import OutboundCallingPlan, SupportAgentPlan, BundlePlan


def _yes_no(value: bool) -> str:
    return "Yes" if bool(value) else "No"

def _fmt_qty(n: int, unit: str) -> str:
    return f"{n:,} {unit}"

def _fmt_support_hours(minutes: int, unlimited: bool) -> str:
    return "Unlimited hours" if unlimited else _fmt_qty(minutes // 60, "hours")


@csrf_exempt
@require_http_methods(["GET", "OPTIONS"])
def public_plans(request):
    # CORS preflight
    if request.method == "OPTIONS":
        resp = HttpResponse()
        resp["Access-Control-Allow-Origin"] = "*"
        resp["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    # OUTBOUND CALLS (active, non-free, max 4)
    oc_qs = (
        OutboundCallingPlan.objects
        .filter(is_active=True, is_trial=False, price__gt=0)
        .values("name", "price", "minutes", "extra_per_minute", "can_use_vai_database")
        .order_by("price", "minutes")[:4]
    )
    outbounds = [
        {
            "name": r["name"],
            "price": f"{r['price']:.0f}",
            "minutes_included": _fmt_qty(r["minutes"], "min"),
            "vai_database_access": _yes_no(r["can_use_vai_database"]),
            "extra_per_minute": f"{r['extra_per_minute']:.1f}",
        }
        for r in oc_qs
    ]

    # SUPPORT AGENT (active, non-free, max 4)
    sa_qs = (
        SupportAgentPlan.objects
        .filter(is_active=True, is_trial=False, price__gt=0)
        .values("name", "price", "minutes", "unlimited_minutes",
                "customizations_enabled", "extra_per_minute")
        .order_by("price", "minutes")[:4]
    )
    supports = [
        {
            "name": r["name"],
            "price": f"{r['price']:.0f}",
            "hours_included": _fmt_support_hours(r["minutes"], r["unlimited_minutes"]),
            "customization": _yes_no(r["customizations_enabled"]),
            "extra_per_minute":f"{r['extra_per_minute']:.1f}",
        }
        for r in sa_qs
    ]

    # BUNDLES (active, non-free, max 4)
    bundle_qs = (
        BundlePlan.objects
        .filter(is_active=True, is_trial=False, price__gt=0)
        .values(
            "name", "price",
            "oc_minutes", "oc_can_use_vai_database", "oc_extra_per_minute",
            "sa_minutes", "sa_unlimited_minutes", "sa_customizations_enabled", "sa_extra_per_minute",
        )
        .order_by("price", "oc_minutes", "sa_minutes")[:4]
    )
    bundles = [
        {
            "name": r["name"],
            "price": f"{r['price']:.0f}",
            "outbound": {
                "minutes_included": _fmt_qty(r["oc_minutes"], "min"),
                "vai_database_access": _yes_no(r["oc_can_use_vai_database"]),
                "extra_per_minute":f"{r['oc_extra_per_minute']:.1f}",
            },
            "support": {
                "hours_included": _fmt_support_hours(r["sa_minutes"], r["sa_unlimited_minutes"]),
                "customization": _yes_no(r["sa_customizations_enabled"]),
                "extra_per_minute": f"{r['sa_extra_per_minute']:.1f}",
            },
        }
        for r in bundle_qs
    ]

    payload = {
        "outbound_calls": outbounds,
        "support_agent": supports,
        "bundles": bundles,
    }

    resp = JsonResponse(payload, json_dumps_params={"indent": 2})
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp["Access-Control-Allow-Headers"] = "Content-Type"
    resp["Cache-Control"] = "public, max-age=60"
    return resp
