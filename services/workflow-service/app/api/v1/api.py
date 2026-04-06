from fastapi import APIRouter
from app.api.v1.endpoints import commits, internal

api_router = APIRouter()
api_router.include_router(commits.router, prefix="/repos/{repo_id}/commits", tags=["commits"])
api_router.include_router(internal.router, prefix="/internal", tags=["Internal"])
