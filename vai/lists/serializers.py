from rest_framework import serializers
from .models import LeadList
from vai.leads.models import Lead  # adjust to your actual app for Lead

class LeadListSerializer(serializers.ModelSerializer):
    leads_count = serializers.IntegerField(read_only=True)
    campaigns_linked = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = LeadList
        fields = ["id", "name", "country", "leads_count", "campaigns_linked", "created_at", "updated_at"]

    def validate(self, attrs):
        if self.instance and "country" in attrs and attrs["country"] != self.instance.country:
            raise serializers.ValidationError({"country": "Country cannot be changed after list creation."})
        return attrs

    def create(self, validated_data):
        user = self.context["request"].user
        name = validated_data["name"]
        country = validated_data.get("country", "").strip()
        lead_list = LeadList.objects.create(owner=user, name=name, country=country)
        if country:
            leads_qs = Lead.objects.filter(owner=user, country=country)
            lead_list.leads.set(leads_qs)
        return lead_list

    def update(self, instance, validated_data):
        instance.name = validated_data.get("name", instance.name)
        instance.save(update_fields=["name", "updated_at"])
        return instance
class LeadMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = ["id", "name", "email", "country", "position", "language", "created_at"]