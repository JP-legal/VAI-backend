from django.contrib.auth import get_user_model
from rest_framework import serializers

from voices.models import User
from .models import PhoneNumberRequest

class PhoneNumberRequestSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField(source="public_id", read_only=True)
    rejection_reason = serializers.CharField(read_only=True)

    class Meta:
        model = PhoneNumberRequest
        fields = [
            "id",
            "request_id",
            "number",
            "country",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "request_id",
            "number",
            "status",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]

    def validate_country(self, value: str):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Country is required.")
        return value


# ------------------------ Admin list/detail serializers ------------------------
class AdminPhoneNumberSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField(source="public_id", read_only=True)
    owner_id = serializers.IntegerField(source="owner.id", read_only=True)
    owner_name = serializers.SerializerMethodField()

    class Meta:
        model = PhoneNumberRequest
        fields = [
            "id",
            "request_id",
            "number",
            "country",
            "status",
            "provider",
            "provider_phone_id",
            "assigned_on",
            "owner_id",
            "owner_name",
            "created_at",
            "updated_at",
        ]

    def get_owner_name(self, obj):
        if not obj.owner:
            return None
        return getattr(obj.owner, "username", None) or getattr(obj.owner, "email", None) or str(obj.owner)


class AdminPhoneNumberRequestSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField(source="public_id", read_only=True)
    owner_id = serializers.IntegerField(source="owner.id", read_only=True)
    owner_name = serializers.SerializerMethodField()

    class Meta:
        model = PhoneNumberRequest
        fields = [
            "id",
            "request_id",
            "country",
            "status",
            "rejection_reason",
            "owner_id",
            "owner_name",
            "created_at",
        ]

    def get_owner_name(self, obj):
        if not obj.owner:
            return None
        return getattr(obj.owner, "username", None) or getattr(obj.owner, "email", None) or str(obj.owner)


# ------------------------ Admin action payloads ------------------------
class AdminApproveSerializer(serializers.Serializer):
    """
    Instant-approve: no number/provider required at approval time.
    """
    # no fields


class AdminRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField()


class AdminAssignSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()

    def validate_user_id(self, value):
        User = get_user_model()
        if not User.objects.filter(pk=value).exists():
            raise serializers.ValidationError("User not found.")
        return value


class AdminCreateNumberSerializer(serializers.ModelSerializer):
    owner_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = PhoneNumberRequest
        fields = [
            "number",
            "country",
            "status",
            "provider",
            "provider_phone_id",
            "owner_id",
        ]

    def validate(self, attrs):
        if not attrs.get("number"):
            raise serializers.ValidationError("number is required.")
        if not attrs.get("provider_phone_id"):
            raise serializers.ValidationError("provider_phone_id is required.")
        # status can be enabled/disabled/suspended; if omitted we will default to 'disabled'
        return attrs
