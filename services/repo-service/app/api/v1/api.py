from fastapi import APIRouter
from app.api.v1.endpoints import drafts, internal

api_router = APIRouter()
api_router.include_router(drafts.router, prefix="/repos", tags=["Drafts"])
api_router.include_router(internal.router, prefix="/internal", tags=["Internal"])
