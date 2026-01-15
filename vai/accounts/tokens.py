# accounts/tokens.py
from django.contrib.auth.tokens import PasswordResetTokenGenerator

# We can reuse Django’s secure token generator for email verification
email_verification_token = PasswordResetTokenGenerator()
