"""Add changes_summary to repo_commits.

Revision ID: 003
Revises: 002
Create Date: 2026-03-29

Changes:
  repo_commits:
    - Add changes_summary TEXT NULLABLE (e.g. "3 files changed, 1 added, 2 modified").
      Populated when a draft is submitted for review so reviewers can see what changed
      without reading individual files.
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repo_commits", sa.Column("changes_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("repo_commits", "changes_summary")
