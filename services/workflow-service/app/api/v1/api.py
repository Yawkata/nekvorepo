from fastapi import APIRouter
from app.api.v1.endpoints import repos, commits

api_router = APIRouter()
api_router.include_router(repos.router, prefix="/repos", tags=["repos"])
api_router.include_router(commits.router, prefix="/repos/{repo_id}/commits", tags=["commits"])
