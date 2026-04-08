from app.database import get_session as get_db  # noqa: F401 — re-exported
from app.services.cognito import CognitoService


def get_cognito() -> CognitoService:
    return CognitoService()


__all__ = ["get_db", "get_cognito"]
