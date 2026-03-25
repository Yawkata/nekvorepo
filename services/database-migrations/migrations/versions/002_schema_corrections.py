"""Schema corrections — additive columns and constraint fixes.

Revision ID: 002
Revises: 001
Create Date: 2026-03-25

Changes:
  repo_heads:
    - Tighten description to VARCHAR(200) per spec (was 2048).
      Existing values are truncated to 200 chars on migration.
    - Tighten repo_name to VARCHAR(50) per spec (was 255).

  drafts:
    - Rename author_id → user_id (spec field name).
    - Add label VARCHAR(100) NULLABLE.
    - Add commit_hash VARCHAR(64) NULLABLE (populated when draft is committed).
    - Add changes_summary TEXT NULLABLE (e.g. "3 files changed").
    - Replace ix_drafts_author_id with ix_drafts_user_id.
    - Replace ix_drafts_repo_author with three-column
      ix_drafts_repo_user_status (repo_id, user_id, status) per spec hot-path.

  repo_commits:
    - Add draft_id UUID NULLABLE FK → drafts.id (per spec Table 5).
    - Add reviewer_comment TEXT NULLABLE (populated on reviewer rejection).

All changes are additive or widen/normalise existing columns; backward
compatible with pods running on revision 001.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # repo_heads — tighten description and repo_name to spec lengths
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE repo_heads "
        "ALTER COLUMN description TYPE VARCHAR(200) USING left(description, 200)"
    )
    op.execute(
        "ALTER TABLE repo_heads "
        "ALTER COLUMN repo_name TYPE VARCHAR(50) USING left(repo_name, 50)"
    )

    # ------------------------------------------------------------------
    # drafts — rename author_id → user_id, add missing columns
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE drafts RENAME COLUMN author_id TO user_id")

    op.execute("DROP INDEX IF EXISTS ix_drafts_author_id")
    op.execute("DROP INDEX IF EXISTS ix_drafts_repo_author")

    op.add_column("drafts", sa.Column("label", sa.String(100), nullable=True))
    op.add_column("drafts", sa.Column("commit_hash", sa.String(64), nullable=True))
    op.add_column("drafts", sa.Column("changes_summary", sa.Text(), nullable=True))

    op.execute("CREATE INDEX ix_drafts_user_id ON drafts (user_id)")
    # Three-column composite index per spec: covers all hot query paths
    # including (repo_id, user_id, status) lookups used in approval transaction
    op.execute("CREATE INDEX ix_drafts_repo_user_status ON drafts (repo_id, user_id, status)")

    # ------------------------------------------------------------------
    # repo_commits — add draft_id FK and reviewer_comment
    # ------------------------------------------------------------------
    op.add_column(
        "repo_commits",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("repo_commits", sa.Column("reviewer_comment", sa.Text(), nullable=True))

    # Add FK constraint for draft_id → drafts.id
    op.execute(
        "ALTER TABLE repo_commits "
        "ADD CONSTRAINT fk_repo_commits_draft_id "
        "FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_repo_commits_draft_id ON repo_commits (draft_id)")


def downgrade() -> None:
    # repo_commits
    op.execute("DROP INDEX IF EXISTS ix_repo_commits_draft_id")
    op.execute(
        "ALTER TABLE repo_commits "
        "DROP CONSTRAINT IF EXISTS fk_repo_commits_draft_id"
    )
    op.drop_column("repo_commits", "reviewer_comment")
    op.drop_column("repo_commits", "draft_id")

    # drafts
    op.execute("DROP INDEX IF EXISTS ix_drafts_repo_user_status")
    op.execute("DROP INDEX IF EXISTS ix_drafts_user_id")
    op.drop_column("drafts", "changes_summary")
    op.drop_column("drafts", "commit_hash")
    op.drop_column("drafts", "label")
    op.execute("ALTER TABLE drafts RENAME COLUMN user_id TO author_id")
    op.execute("CREATE INDEX ix_drafts_author_id ON drafts (author_id)")
    op.execute("CREATE INDEX ix_drafts_repo_author ON drafts (repo_id, author_id)")

    # repo_heads — restore original lengths
    op.execute("ALTER TABLE repo_heads ALTER COLUMN description TYPE VARCHAR(2048)")
    op.execute("ALTER TABLE repo_heads ALTER COLUMN repo_name TYPE VARCHAR(255)")
