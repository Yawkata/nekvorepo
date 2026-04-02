"""Add author_email to repo_commits.

Revision ID: 004
Revises: 003
Create Date: 2026-04-02

Changes:
  repo_commits:
    - Add author_email VARCHAR(254) NULLABLE.
      Populated at commit submission time from the author's Passport JWT email
      claim. Used by notification emails sent on approval and rejection without
      requiring an extra round-trip to identity-service or Cognito.
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repo_commits", sa.Column("author_email", sa.String(254), nullable=True))


def downgrade() -> None:
    op.drop_column("repo_commits", "author_email")
