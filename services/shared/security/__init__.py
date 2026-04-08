"""
Public API for the shared security package.

Exports only the universal, Cognito-free passport utilities.
Services that need Cognito verification must use their own local copy:

    identity-service: from app.security.cognito import verify_cognito_token, JWKS_URL
"""
from shared.security.passport import verify_passport, security
from shared.schemas.auth import TokenData

__all__ = [
    "verify_passport",
    "security",
    "TokenData",
]
