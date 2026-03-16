from fastapi import FastAPI
from app.api.v1.api import api_router
from app.core.config import settings

from sqlmodel import SQLModel
from shared.database import engine
from shared.models.identity import UserRepoLink

from shared.security import verify_passport, TokenData
from fastapi import Security

app = FastAPI(title=settings.PROJECT_NAME, version="2026.1.0")

@app.on_event("startup")
def on_startup():
    # This creates the tables if they don't exist
    SQLModel.metadata.create_all(engine)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/v1/auth/verify-me")
def test_security(passport: TokenData = Security(verify_passport)):
    return {"status": "Verified", "data": passport}

@app.get("/healthz")
def liveness_probe():
    return {"status": "alive", "service": "identity-service"}