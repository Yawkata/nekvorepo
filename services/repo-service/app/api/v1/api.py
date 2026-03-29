from fastapi import APIRouter
from app.api.v1.endpoints import drafts, internal, view

api_router = APIRouter()
api_router.include_router(drafts.router, prefix="/repos", tags=["Drafts"])
api_router.include_router(view.router, prefix="/repos", tags=["View Mode"])
api_router.include_router(internal.router, prefix="/internal", tags=["Internal"])
