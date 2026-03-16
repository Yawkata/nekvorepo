from fastapi import APIRouter, Depends, status, HTTPException
from sqlmodel import Session, select
import jwt # Standard PyJWT
from shared.schemas.auth import UserRegister, Token
from shared.models.identity import UserRepoLink
from shared.security import verify_cognito_token
from app.api import deps
from app.services.cognito import CognitoService
from app.core.security import create_passport_token
from pydantic import BaseModel, EmailStr

router = APIRouter()

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, cognito: CognitoService = Depends(deps.get_cognito)):
    return cognito.register_user(payload.email, payload.password, payload.full_name)

@router.post("/login", response_model=Token)
def login(
    payload: LoginRequest,
    db: Session = Depends(deps.get_db),
    cognito: CognitoService = Depends(deps.get_cognito)
):
    # 1. Authenticate with AWS
    aws_auth = cognito.login(payload.email, payload.password)

    # 2. Securely verify and extract 'sub'
    decoded_token = verify_cognito_token(aws_auth["IdToken"])
    user_sub = decoded_token["sub"]

    # 3. Fetch permissions
    links = db.exec(select(UserRepoLink).where(UserRepoLink.user_id == user_sub)).all()
    
    # 4. Create high-performance permission map: {repo_id: role}
    permissions = {str(link.repo_id): link.role for link in links}

    # 5. Issue Passport
    passport_jwt = create_passport_token(
        user_id=user_sub,
        email=payload.email,
        permissions=permissions
    )

    return Token(access_token=passport_jwt, token_type="bearer")