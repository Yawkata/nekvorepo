from fastapi import APIRouter, Depends, Security, status
from sqlmodel import Session, func, select
from pydantic import BaseModel, EmailStr
from shared.schemas.auth import Token, TokenData
from shared.models.identity import UserRepoLink
from shared.security import verify_passport
from shared.security.cognito import verify_cognito_token
from app.api import deps
from app.services.cognito import CognitoService
from app.core.security import create_passport_token

router = APIRouter()

_401 = {401: {"description": "Invalid or expired token"}}
_400 = {400: {"description": "Validation error (e.g. email already registered, bad password)"}}
_404 = {404: {"description": "User not found"}}


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


def _build_passport(user_sub: str, email: str, db: Session) -> str:
    """
    Build a Passport JWT for the given user.

    The JWT carries user_id, email, and a repo_count hint — NOT permissions.
    Downstream services resolve roles via GET /v1/internal/repos/{id}/role
    with a 60-second TTL cache.
    """
    repo_count = db.exec(
        select(func.count()).select_from(UserRepoLink).where(UserRepoLink.user_id == user_sub)
    ).one()
    return create_passport_token(user_id=user_sub, email=email, repo_count=repo_count)


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
    db: Session = Depends(deps.get_db),
    cognito: CognitoService = Depends(deps.get_cognito),
):
    aws_auth = cognito.login(payload.email, payload.password)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    passport_jwt = _build_passport(decoded["sub"], payload.email, db)
    return Token(
        access_token=passport_jwt,
        token_type="bearer",
        refresh_token=aws_auth.get("RefreshToken"),
    )


@router.post("/refresh", response_model=Token, responses=_401)
def refresh(
    payload: RefreshRequest,
    db: Session = Depends(deps.get_db),
    cognito: CognitoService = Depends(deps.get_cognito),
):
    """
    Exchange a Cognito refresh token for a fresh Passport JWT.
    The frontend should call this proactively before the 1-hour passport expires.
    Cognito does not issue a new refresh token on refresh — the existing one
    remains valid until its 30-day TTL.
    """
    aws_auth = cognito.refresh_session(payload.refresh_token, payload.email)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    passport_jwt = _build_passport(decoded["sub"], payload.email, db)
    return Token(access_token=passport_jwt, token_type="bearer")


@router.get("/verify-me", responses=_401)
def verify_me(passport: TokenData = Security(verify_passport)):
    """Returns the decoded passport payload for the caller. Useful for debugging."""
    return {"status": "verified", "data": passport}
