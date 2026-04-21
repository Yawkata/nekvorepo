import logging

from fastapi import APIRouter, Depends, Security, status
from pydantic import BaseModel, EmailStr
from typing import Optional
from sqlalchemy import text
from sqlmodel import Session
from shared.schemas.auth import TokenData
from shared.security import verify_passport
from app.security.cognito import verify_cognito_token
from app.api import deps
from app.services.cognito import CognitoService
from app.core.security import create_passport_token

log = logging.getLogger(__name__)

router = APIRouter()

_401 = {401: {"description": "Invalid or expired token"}}
_400 = {400: {"description": "Validation error (e.g. email already registered, bad password)"}}
_404 = {404: {"description": "User not found"}}


class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None  # Cognito opaque refresh token (30-day TTL)


class MessageResponse(BaseModel):
    message: str


class UserRegister(BaseModel):
    """Identity-service only — requires email-validator (intentional)."""
    email: EmailStr
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ConfirmRequest(BaseModel):
    email: EmailStr
    code: str  # 6-digit OTP from the Cognito verification email


class RefreshRequest(BaseModel):
    refresh_token: str
    email: EmailStr  # Required for Cognito SecretHash with username_attributes=["email"]


def _upsert_user(user_id: str, email: str, db: Session) -> None:
    """
    Upsert into the users table so member list queries can return email.
    Errors are logged and swallowed — login must succeed even if this fails.
    """
    try:
        db.exec(  # type: ignore[call-overload]
            text(
                "INSERT INTO users (id, email) VALUES (:id, :email) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email"
            ).bindparams(id=user_id, email=email)
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("user_upsert_failed user_id=%s error=%s", user_id, exc)


def _build_passport(user_sub: str, email: str) -> str:
    """
    Build a Passport JWT for the given user.

    The JWT carries only stable identity claims (user_id, email).
    Downstream services resolve roles via GET /v1/internal/repos/{id}/role
    with a 60-second TTL cache, not from JWT claims.
    """
    return create_passport_token(user_id=user_sub, email=email)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=MessageResponse, responses=_400)
def register(payload: UserRegister, cognito: CognitoService = Depends(deps.get_cognito)):
    return cognito.register_user(payload.email, payload.password, payload.full_name)


@router.post("/confirm", status_code=status.HTTP_200_OK, response_model=MessageResponse, responses={**_400, **_404})
def confirm(payload: ConfirmRequest, cognito: CognitoService = Depends(deps.get_cognito)):
    """
    Confirms a newly registered account using the OTP emailed by Cognito.
    Must be called before the first login is possible.
    """
    cognito.confirm_user(payload.email, payload.code)
    return {"message": "Account confirmed. You can now log in."}


@router.post("/login", response_model=Token, responses={**_401, 403: {"description": "Account not confirmed"}})
def login(
    payload: LoginRequest,
    cognito: CognitoService = Depends(deps.get_cognito),
    db: Session = Depends(deps.get_db),
):
    aws_auth = cognito.login(payload.email, payload.password)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    passport_jwt = _build_passport(decoded["sub"], payload.email)
    _upsert_user(decoded["sub"], payload.email, db)
    return Token(
        access_token=passport_jwt,
        token_type="bearer",
        refresh_token=aws_auth.get("RefreshToken"),
    )


@router.post("/refresh", response_model=Token, responses=_401)
def refresh(
    payload: RefreshRequest,
    cognito: CognitoService = Depends(deps.get_cognito),
    db: Session = Depends(deps.get_db),
):
    """
    Exchange a Cognito refresh token for a fresh Passport JWT.
    The frontend should call this proactively before the 1-hour passport expires.
    Cognito does not issue a new refresh token on refresh — the existing one
    remains valid until its 30-day TTL.
    """
    aws_auth = cognito.refresh_session(payload.refresh_token, payload.email)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    passport_jwt = _build_passport(decoded["sub"], payload.email)
    _upsert_user(decoded["sub"], payload.email, db)
    return Token(access_token=passport_jwt, token_type="bearer")


@router.get("/verify-me", responses=_401)
def verify_me(passport: TokenData = Security(verify_passport)):
    """Returns the decoded passport payload for the caller. Useful for debugging."""
    return {"status": "verified", "data": passport}
