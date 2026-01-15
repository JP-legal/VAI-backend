from django.conf import settings
from django.db import models

class LeadList(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lead_lists")
    name = models.CharField(max_length=255)
    country = models.CharField(max_length=128, blank=True)
    leads = models.ManyToManyField('leads.Lead', related_name='lists', blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "created_at"]),
            models.Index(fields=["owner", "name"]),
            models.Index(fields=["owner", "country"]),
        ]
        unique_together = [("owner", "name")]

    def __str__(self):
        return f"{self.name} [{self.country}]"