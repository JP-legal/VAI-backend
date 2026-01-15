
INTERVAL_CHOICES = (
    ("month", "Monthly"),
)

SUBSCRIPTION_STATUS = (
    ("incomplete", "Incomplete"),
    ("incomplete_expired", "Incomplete (Expired)"),
    ("trialing", "Trialing"),
    ("active", "Active"),
    ("past_due", "Past Due"),
    ("canceled", "Canceled"),
    ("unpaid", "Unpaid"),
    ("paused", "Paused"),
)

COMPONENT_CHOICES = (
    ("support_agent", "Support Agent"),
    ("outbound_calling", "Outbound Calling"),
)
TRANSACTION_STATUS = (
    ("initiated", "initiated"),
    ("pending", "pending"),
    ("requires_action", "requires_action"),
    ("succeeded", "succeeded"),
    ("failed", "failed"),
    ("canceled", "canceled"),
)
TRANSACTION_KIND = (
    ("purchase", "purchase"),
    ("upgrade", "upgrade"),
    ("renewal", "renewal"),
    ("overage", "overage"),
    ("topup", "topup"),
    ("cancel", "cancel"),
)