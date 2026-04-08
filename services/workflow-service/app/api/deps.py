"""
Shared FastAPI dependencies for workflow-service.

Provides:
  - get_db         — SQLModel session (one per request)
  - verify_passport — decoded JWT (from shared security module)
  - require_member  — verifies the caller is a member of the target repo;
                      returns (TokenData, role_str) so callers can branch on role.
"""
import uuid

from fastapi import HTTPException, Security

from app.database import get_session as get_db  # noqa: F401 — re-exported
from shared.security.passport import verify_passport  # noqa: F401 — re-exported
from shared.schemas.auth import TokenData

from app.core.config import settings
from app.services import identity_client


def require_member(
    repo_id: uuid.UUID,
    passport: TokenData = Security(verify_passport),
) -> tuple[TokenData, str]:
    """
    Verify the caller holds any role in the given repo.
    FastAPI injects `repo_id` from the path parameter automatically.

    Returns (passport, role_string) so individual endpoints can enforce
    finer-grained checks without a second round-trip to identity-service.

    Raises 403 when the caller is not a member.
    Raises 503/502 when identity-service is unreachable (propagated from client).
    """
    role = identity_client.get_role(
        repo_id=repo_id,
        user_id=passport.user_id,
        ttl=settings.ROLE_CACHE_TTL_SECONDS,
    )
    if role is None:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this repository.",
        )
    return passport, role
