"""Grants required by the phase-10 repo deletion cascade.

Revision ID: 010
Revises: 009

The delete-repo saga is owned by identity-service (it owns repo_heads +
user_repo_links + invite_tokens) and fans out to workflow-service for commit
cleanup.  Migration 008 was authored before phase 10 and therefore withheld:

  * DELETE on repo_heads for identity_svc   — now needed: identity-service
    is the final writer in the cascade and hard-deletes the row itself.
  * DELETE on repo_commits for workflow_svc — now needed: workflow-service
    exposes DELETE /v1/internal/repos/{id} to purge commit rows for a
    deleted repo.

All other privileges from 008 remain unchanged.  AWS Well-Architected SEC05
(least privilege) is preserved: each service still only has DELETE on the
tables it conceptually owns.
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT DELETE ON repo_heads   TO identity_svc")
    op.execute("GRANT DELETE ON repo_commits TO workflow_svc")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON repo_heads   FROM identity_svc")
    op.execute("REVOKE DELETE ON repo_commits FROM workflow_svc")
