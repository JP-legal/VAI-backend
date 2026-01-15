from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vai.accounts"
    label = "accounts"
    def ready(self):
        from . import signals