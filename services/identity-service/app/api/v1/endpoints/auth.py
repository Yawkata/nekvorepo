from fastapi import APIRouter, Depends, status
from sqlmodel import Session, select
from pydantic import BaseModel, EmailStr
from shared.schemas.auth import UserRegister, Token
from shared.models.identity import UserRepoLink
from shared.security import verify_cognito_token
from app.api import deps
from app.services.cognito import CognitoService
from app.core.security import create_passport_token

router = APIRouter()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str
    email: EmailStr  # needed for Cognito SecretHash calculation


def _build_passport(user_sub: str, email: str, db: Session) -> str:
    links = db.exec(select(UserRepoLink).where(UserRepoLink.user_id == user_sub)).all()
    permissions = {str(link.repo_id): link.role for link in links}
    return create_passport_token(user_id=user_sub, email=email, permissions=permissions)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, cognito: CognitoService = Depends(deps.get_cognito)):
    return cognito.register_user(payload.email, payload.password, payload.full_name)


@router.post("/login", response_model=Token)
def login(
    payload: LoginRequest,
    db: Session = Depends(deps.get_db),
    cognito: CognitoService = Depends(deps.get_cognito),
):
    aws_auth = cognito.login(payload.email, payload.password)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    user_sub = decoded["sub"]
    passport_jwt = _build_passport(user_sub, payload.email, db)
    return Token(
        access_token=passport_jwt,
        token_type="bearer",
        refresh_token=aws_auth.get("RefreshToken"),
    )


@router.post("/refresh", response_model=Token)
def refresh(
    payload: RefreshRequest,
    db: Session = Depends(deps.get_db),
    cognito: CognitoService = Depends(deps.get_cognito),
):
    """
    Exchange a Cognito refresh token for a new Passport JWT.
    The frontend should call this proactively before the 1-hour access token expires.
    """
    aws_auth = cognito.refresh_session(payload.refresh_token, payload.email)
    decoded = verify_cognito_token(aws_auth["IdToken"])
    user_sub = decoded["sub"]
    passport_jwt = _build_passport(user_sub, payload.email, db)
    # Cognito does not issue a new RefreshToken on refresh — return None
    return Token(access_token=passport_jwt, token_type="bearer")