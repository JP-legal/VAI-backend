import re
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from .models import Lead

E164_DIGITS_RE = r'^[1-9]\d{7,14}$'
def normalize_to_e164(value: str) -> str:
    if value is None:
        raise serializers.ValidationError(
            "Enter a valid international phone number (E.164), e.g. +14155550123."
        )
    # Remove common formatting noise: space, dash, dot, parentheses, NBSP
    s = re.sub(r'[\s\-\.\(\)\u00A0]', '', str(value))

    # Handle international prefixes
    if s.startswith('+'):
        digits = s[1:]
    elif s.startswith('00'):
        digits = s[2:]
    elif s.startswith('011'):
        digits = s[3:]
    else:
        digits = s

    if not re.fullmatch(E164_DIGITS_RE, digits or ''):
        raise serializers.ValidationError(
            "Enter a valid international phone number (E.164, 8–15 digits). Example: +14155550123."
        )
    return '+' + digits
class LeadSerializer(serializers.ModelSerializer):
    name         = serializers.CharField(required=True,  allow_blank=False, max_length=255)
    position     = serializers.CharField(required=True,  allow_blank=False, max_length=255)
    phone_number = serializers.CharField(required=True,  allow_blank=False, max_length=16)
    email        = serializers.EmailField(required=True,  allow_blank=False)
    language     = serializers.CharField(required=True,  allow_blank=False, max_length=64)
    company      = serializers.CharField(required=True,  allow_blank=False, max_length=255)
    industry     = serializers.CharField(required=True,  allow_blank=False, max_length=255)
    country      = serializers.CharField(required=True,  allow_blank=False, max_length=128)
    address      = serializers.CharField(required=True,  allow_blank=False, max_length=512)

    owner = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Lead
        fields = [
            "id", "owner",
            "name", "position", "phone_number", "email",
            "language", "company", "industry",
            "country", "address",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        validators = [
            UniqueTogetherValidator(
                queryset=Lead.objects.all(),
                fields=["owner", "phone_number"],
                message="A lead with this phone number already exists.",
            )
        ]

    def validate_phone_number(self, value: str) -> str:
        # Allow input without '+', but always store '+E.164'
        return normalize_to_e164(value)

    def validate(self, attrs):
        # Trim all string fields
        for k, v in list(attrs.items()):
            if isinstance(v, str):
                attrs[k] = v.strip()
        return attrs
class LeadAdminImportSerializer(LeadSerializer):
    owner = serializers.HiddenField(default=None)

    class Meta(LeadSerializer.Meta):
        validators = [
            UniqueTogetherValidator(
                queryset=Lead.objects.filter(owner__isnull=True),
                fields=["owner", "phone_number"],
                message="A lead with this phone number already exists among unassigned leads.",
            )
        ]