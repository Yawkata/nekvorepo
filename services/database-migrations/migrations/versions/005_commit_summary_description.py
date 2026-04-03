"""Add commit_summary and commit_description to repo_commits.

commit_summary  — required VARCHAR(200): one-line title shown in commit lists.
commit_description — optional TEXT: extended description / body of the commit.

Existing rows are back-filled with a placeholder summary so the NOT NULL
constraint can be applied without breaking deployed data.

Revision ID: 005
Revises:     004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add commit_summary as nullable first so we can back-fill existing rows.
    op.add_column(
        "repo_commits",
        sa.Column("commit_summary", sa.String(200), nullable=True),
    )
    op.add_column(
        "repo_commits",
        sa.Column("commit_description", sa.Text(), nullable=True),
    )

    # Back-fill: use changes_summary when available, fall back to a placeholder.
    op.execute(
        """
        UPDATE repo_commits
        SET commit_summary = COALESCE(
            NULLIF(TRIM(changes_summary), ''),
            'Initial commit'
        )
        WHERE commit_summary IS NULL
        """
    )

    # Now tighten the column to NOT NULL.
    op.alter_column("repo_commits", "commit_summary", nullable=False)


def downgrade() -> None:
    op.drop_column("repo_commits", "commit_description")
    op.drop_column("repo_commits", "commit_summary")
