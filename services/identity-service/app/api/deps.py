from typing import Generator
from shared.database import get_session
from app.services.cognito import CognitoService

def get_db() -> Generator:
    # Use the centralized generator from shared
    yield from get_session()

def get_cognito() -> CognitoService:
    return CognitoService()