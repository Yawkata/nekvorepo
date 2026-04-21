from fastapi import APIRouter
from app.api.v1.endpoints import auth, internal, repos, invites, members

api_router = APIRouter()
api_router.include_router(auth.router,     prefix="/auth",     tags=["Authentication"])
api_router.include_router(repos.router,    prefix="/repos",    tags=["Repositories"])
api_router.include_router(invites.router,  prefix="/repos",    tags=["Invites"])
api_router.include_router(members.router,  prefix="/repos",    tags=["Members"])
api_router.include_router(internal.router, prefix="/internal", tags=["Internal"])