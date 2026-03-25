from fastapi import APIRouter
from app.api.v1.endpoints import repos

api_router = APIRouter()
api_router.include_router(repos.router, prefix="/repos", tags=["repos"])
