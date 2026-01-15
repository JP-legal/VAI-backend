from django.urls import path

from .pricing_api import public_plans
from .views import (
    FreeTrialPlansView,
    SupportAgentTrialView,
    OutboundCallingTrialView,
    FreeTrialUsersView,
    FreeTrialUserAdjustView,
    FreeTrialUserDeleteView, BundlePlansView, BundlePlanDetailView, BundleUsersView, SupportAgentPlansView,
    SupportAgentPlanDetailView, SupportAgentUsersView, OutboundCallingPlansView, OutboundCallingPlanDetailView,
    OutboundCallingUsersView, PaymentMethodsView, CreateSetupIntentView, SetDefaultPaymentMethodView,
    DeletePaymentMethodView,
)
from .views_user import MySubscriptionsView, MyTransactionsView, CancelMySubscriptionView, UpgradeMySubscriptionView, \
    StartSubscriptionView, AvailablePlansView, BeginSubscriptionCheckoutView,DailyUsageView, \
    BeginTopUpCheckoutView, BeginPortalUpdateConfirmView, CreateTestClockView, AdvanceTestClockView, AssignCustomerToClockView, \
    SetDefaultTestPaymentMethodView, BannerStateView,   MetricsOverviewView, \
    MetricsUsageMonthlyView,MetricsSubscriptionBreakdownView,MetricsRevenueMonthlyView
from .views_summary import SubscriptionSummaryView
from .webhooks import stripe_webhook
from .views import PaymentsAdminView, RetryPaymentView, RetryFailedChargesView

urlpatterns = [
    path("free-trials", FreeTrialPlansView.as_view(), name="free-trials"),
    path("free-trials/support-agent", SupportAgentTrialView.as_view(), name="free-trials-support-agent"),
    path("free-trials/outbound-calling", OutboundCallingTrialView.as_view(), name="free-trials-outbound-calling"),
    path("free-trials/users", FreeTrialUsersView.as_view(), name="free-trials-users"),
    path("free-trials/users/<int:subscription_id>/adjust", FreeTrialUserAdjustView.as_view(), name="free-trials-user-adjust"),
    path("free-trials/users/<int:subscription_id>", FreeTrialUserDeleteView.as_view(), name="free-trials-user-delete"),
    path("bundles/plans/", BundlePlansView.as_view(), name="bundle-plans"),
    path("bundles/plans/<int:pk>/", BundlePlanDetailView.as_view(), name="bundle-plan-detail"),
    path("bundles/users/", BundleUsersView.as_view(), name="bundle-users"),
    path("support-agent/plans/", SupportAgentPlansView.as_view(), name="sa-plans"),
    path("support-agent/plans/<int:pk>/", SupportAgentPlanDetailView.as_view(), name="sa-plan-detail"),
    path("support-agent/users/", SupportAgentUsersView.as_view(), name="sa-users"),
    path("outbound-calling/plans/", OutboundCallingPlansView.as_view(), name="oc-plans"),
    path("outbound-calling/plans/<int:pk>/", OutboundCallingPlanDetailView.as_view(), name="oc-plan-detail"),
    path("outbound-calling/users/", OutboundCallingUsersView.as_view(), name="oc-users"),
    path("payment-methods", PaymentMethodsView.as_view(), name="payment_methods"),
    path("payment-methods/setup-intent", CreateSetupIntentView.as_view(), name="payment_methods_setup_intent"),
    path("payment-methods/set-default", SetDefaultPaymentMethodView.as_view(), name="payment_methods_set_default"),
    path("payment-methods/<str:pm_id>", DeletePaymentMethodView.as_view(), name="payment_methods_delete"),

    path("me/subscriptions", MySubscriptionsView.as_view()),
    path("me/subscriptions/summary", SubscriptionSummaryView.as_view()),
    path("me/plans", AvailablePlansView.as_view()),
    path("me/subscriptions/start", StartSubscriptionView.as_view()),
    path("me/subscriptions/<int:subscription_id>/upgrade", UpgradeMySubscriptionView.as_view()),
    path("me/subscriptions/<int:subscription_id>/cancel", CancelMySubscriptionView.as_view()),
    path("me/transactions", MyTransactionsView.as_view()),
    path("public/plans/", public_plans, name="public-plans"),
    path("me/checkout/subscription", BeginSubscriptionCheckoutView.as_view()),
    path("me/checkout/topup", BeginTopUpCheckoutView.as_view()),
    path("me/portal/subscription-update-confirm", BeginPortalUpdateConfirmView.as_view()),
    path("stripe/webhook", stripe_webhook),

    # NEW: last 2 weeks of daily minutes for Outbound Calls / Support Agent
    path("me/usage/daily-minutes", DailyUsageView.as_view()),

    path("test/clocks/create", CreateTestClockView.as_view(), name="billing-test-clock-create"),
    path("test/clocks/advance", AdvanceTestClockView.as_view(), name="billing-test-clock-advance"),
    path("test/assign-customer-to-clock", AssignCustomerToClockView.as_view(), name="billing-test-assign-customer"),
    path("test/set-default-payment-method", SetDefaultTestPaymentMethodView.as_view(), name="billing-test-set-default-pm"),
    path("me/banner", BannerStateView.as_view()),
    path("payments", PaymentsAdminView.as_view(), name="billing-payments"),
    path("payments/retry-failed", RetryFailedChargesView.as_view(), name="billing-payments-retry-failed"),
    path("payments/<int:pk>/retry", RetryPaymentView.as_view(), name="billing-payment-retry"),
    path("metrics/overview", MetricsOverviewView.as_view(), name="metrics-overview"),
    path("metrics/usage", MetricsUsageMonthlyView.as_view(), name="metrics-usage"),
    path("metrics/subscriptions", MetricsSubscriptionBreakdownView.as_view(), name="metrics-subscriptions"),
    path("metrics/revenue", MetricsRevenueMonthlyView.as_view(), name="metrics-revenue"),
]

